from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

REPO = "zacharyivie/gofer-flow"
SHA = "a" * 40
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def candidate() -> Any:
    spec = importlib.util.spec_from_file_location(
        "release_candidate", ROOT / "scripts/release-candidate.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(params=[False, True], ids=["unsigned", "signed"])
def prepared(
    candidate: Any, tmp_path: Path, request: pytest.FixtureRequest
) -> tuple[Path, dict[str, Any]]:
    signed = request.param
    source = tmp_path / "input"
    source.mkdir()
    version = candidate.versions()
    artifacts = []
    for index, (group, expected) in enumerate(candidate.required_files(version, signed).items(), 1):
        folder = source / f"gofer-flow-{group}"
        folder.mkdir()
        for name in expected:
            (folder / name).write_text(f"fixture {name}")
        if group != "audit-evidence":
            (folder / f"package-tests-{group}.json").write_text(
                json.dumps({"status": "passed", "version": version})
            )
        if signed and group == "windows":
            (folder / "signatures-windows.json").write_text(
                json.dumps(
                    [
                        {"file": name, "status": "Valid"}
                        for name in expected
                        if name.endswith(".exe")
                    ]
                )
            )
        if signed and group == "macos":
            for name in ("notarization-macos-cli.json", "notarization-macos-dmg.json"):
                (folder / name).write_text('{"status": "Accepted"}')
        if group != "audit-evidence":
            checksum = folder / f"checksums-{group}.txt"
            checksum.write_text(
                "".join(
                    f"{candidate.digest(p)}  {p.name}\n"
                    for p in sorted(folder.iterdir())
                    if p != checksum
                )
            )
        artifacts.append(
            {"id": index, "name": folder.name, "expired": False, "digest": f"sha256:{'b' * 64}"}
        )
    output = tmp_path / "release"
    manifest = candidate.create_manifest(source, output, REPO, SHA, 42, 1, artifacts, signed=signed)
    return output, manifest


class GitHub:
    """Stateful fake for uploads, remote downloads and release publication."""

    def __init__(self, candidate: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        self.candidate = candidate
        self.releases: list[dict[str, Any]] = []
        self.files: dict[int, dict[str, bytes]] = {}
        self.uploads = 0
        self.publish_calls = 0
        self.fail_upload_at = 0
        self.fail_after_publish = False
        self.attestation_valid = True
        self.sha = SHA
        self.run: dict[str, Any] = {
            "conclusion": "success",
            "status": "completed",
            "head_sha": SHA,
            "head_branch": "main",
            "event": "push",
            "path": candidate.WORKFLOW,
            "repository": {"full_name": REPO},
        }
        monkeypatch.setattr(candidate, "api", self.api)
        monkeypatch.setattr(candidate, "gh", self.gh)
        monkeypatch.setattr(candidate, "download", self.download)
        monkeypatch.setenv("GITHUB_SHA", SHA)
        monkeypatch.setenv("GITHUB_REF_NAME", f"v{candidate.versions()}")
        monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
        monkeypatch.setattr(
            candidate.subprocess,
            "run",
            lambda *a, **kw: subprocess.CompletedProcess(a, 0, stdout=SHA + "\n"),
        )

    def api(self, endpoint: str, payload: Any = None, method: str = "GET") -> Any:
        route = endpoint.split(f"repos/{REPO}/", 1)[1].split("?", 1)[0]
        if route.startswith("commits/"):
            return {"sha": self.sha}
        if route.startswith("actions/runs/"):
            return copy.deepcopy(self.run)
        if route == "releases/generate-notes":
            return {"body": "Release notes"}
        if route == "releases":
            if method == "POST":
                release_id = len(self.releases) + 1
                release = {**payload, "id": release_id, "html_url": f"https://example/{release_id}"}
                self.releases.append(release)
                self.files[release_id] = {}
                return copy.deepcopy(release)
            return copy.deepcopy(self.releases)
        release_id = int(route.split("/")[1])
        release = next(r for r in self.releases if r["id"] == release_id)
        if route.endswith("/assets"):
            return [
                {"id": index, "name": name, "release_id": release_id}
                for index, name in enumerate(self.files[release_id], 1)
            ]
        if method == "PATCH":
            release.update(payload)
            if payload.get("draft") is False:
                self.publish_calls += 1
                if self.fail_after_publish:
                    self.fail_after_publish = False
                    raise subprocess.CalledProcessError(1, "gh")
        return copy.deepcopy(release)

    def gh(self, *args: str, output: Path | None = None) -> str:
        if args[:2] == ("attestation", "verify"):
            assert "--source-digest" in args and SHA in args
            assert f"{REPO}/{self.candidate.WORKFLOW}" in args
            if not self.attestation_valid:
                raise subprocess.CalledProcessError(1, "gh attestation verify")
            return "verified"
        assert args[:2] == ("release", "upload")
        self.uploads += 1
        if self.uploads == self.fail_upload_at:
            raise subprocess.CalledProcessError(1, "gh release upload")
        release = next(r for r in self.releases if r["tag_name"] == args[2])
        path = Path(args[3])
        assert path.name not in self.files[release["id"]]
        assert "--clobber" not in args
        self.files[release["id"]][path.name] = path.read_bytes()
        return ""

    def download(self, repo: str, asset: dict[str, Any], destination: Path) -> None:
        assert repo == REPO
        destination.write_bytes(self.files[asset["release_id"]][asset["name"]])


def test_complete_manifest_is_bound_to_source_and_all_platforms(
    candidate: Any, prepared: Any
) -> None:
    output, manifest = prepared
    candidate.validate_manifest(manifest, REPO, SHA, candidate.versions())
    assert set(manifest["files"]) == {p.name for p in output.iterdir()} - {candidate.MANIFEST}
    assert len(manifest["artifacts"]) == 4


@pytest.mark.parametrize(
    "change", ["missing-platform", "signing-type", "signing-mismatch", "commit", "version", "path"]
)
def test_invalid_manifest_is_rejected(candidate: Any, prepared: Any, change: str) -> None:
    _, manifest = prepared
    if change == "missing-platform":
        manifest["files"] = {k: v for k, v in manifest["files"].items() if v["group"] != "windows"}
    elif change == "signing-type":
        manifest["signed"] = "false"
    elif change == "signing-mismatch":
        manifest["signed"] = not manifest["signed"]
    elif change == "path":
        manifest["files"]["../escape"] = next(iter(manifest["files"].values()))
    else:
        manifest[change] = "wrong"
    with pytest.raises(ValueError):
        candidate.validate_manifest(manifest, REPO, SHA, candidate.versions())


@pytest.mark.parametrize("change", ["missing", "extra", "corrupt", "checksum-omission"])
def test_staging_rejects_incomplete_or_changed_build_outputs(
    candidate: Any,
    prepared: Any,
    tmp_path: Path,
    change: str,
) -> None:
    folder = tmp_path / "input/gofer-flow-linux"
    if change == "missing":
        (folder / "gof-linux-x64").unlink()
    elif change == "extra":
        (folder / "unexpected.exe").write_text("unexpected")
    elif change == "corrupt":
        (folder / "gof-linux-x64").write_text("different bytes")
    else:
        checksum = folder / "checksums-linux.txt"
        checksum.write_text("\n".join(checksum.read_text().splitlines()[1:]) + "\n")
    with pytest.raises(ValueError):
        candidate.verify_group(folder, "linux", candidate.versions())


def test_upload_retry_resumes_identical_assets_then_publish_is_idempotent(
    candidate: Any,
    prepared: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output, _ = prepared
    github = GitHub(candidate, monkeypatch)
    github.fail_upload_at = 4
    with pytest.raises(subprocess.CalledProcessError):
        candidate.stage(REPO, output)
    assert candidate.READY not in github.releases[0]["body"]
    assert len(github.files[1]) == 3
    candidate.stage(REPO, output)
    snapshot = copy.deepcopy(github.files)
    count = github.uploads
    candidate.stage(REPO, output)
    assert github.uploads == count
    candidate.publish(REPO)
    candidate.publish(REPO)
    assert github.publish_calls == 1
    assert github.files == snapshot
    assert github.releases[0]["tag_name"] == f"v{candidate.versions()}"
    assert not github.releases[0]["draft"]


def test_retry_after_publication_response_loss_verifies_existing_release(
    candidate: Any,
    prepared: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output, _ = prepared
    github = GitHub(candidate, monkeypatch)
    candidate.stage(REPO, output)
    github.fail_after_publish = True
    with pytest.raises(subprocess.CalledProcessError):
        candidate.publish(REPO)
    candidate.publish(REPO)
    assert github.publish_calls == 1


@pytest.mark.parametrize(
    "change",
    [
        "wrong-tag",
        "moved-tag",
        "corrupt",
        "extra-asset",
        "missing-asset",
        "failed-run",
        "pending-run",
        "wrong-run-sha",
        "untrusted-branch",
        "untrusted-workflow",
        "fork",
        "bad-attestation",
        "not-ready",
        "expired",
        "ambiguous",
    ],
)
def test_publication_fails_closed(
    candidate: Any,
    prepared: Any,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    output, manifest = prepared
    if change == "expired":
        manifest["created_at"] = (datetime.now(UTC) - timedelta(days=8)).isoformat()
        (output / candidate.MANIFEST).write_text(json.dumps(manifest))
    github = GitHub(candidate, monkeypatch)
    candidate.stage(REPO, output)
    if change == "wrong-tag":
        monkeypatch.setenv("GITHUB_REF_NAME", "v9.9.9")
    elif change == "moved-tag":
        github.sha = "b" * 40
    elif change == "corrupt":
        github.files[1]["gof-linux-x64"] = b"tampered"
    elif change == "extra-asset":
        github.files[1]["surprise.exe"] = b"extra"
    elif change == "missing-asset":
        del github.files[1]["gof-linux-x64"]
    elif change == "failed-run":
        github.run["conclusion"] = "failure"
    elif change == "pending-run":
        github.run["status"] = "in_progress"
    elif change == "wrong-run-sha":
        github.run["head_sha"] = "b" * 40
    elif change == "untrusted-branch":
        github.run["head_branch"] = "feature"
    elif change == "untrusted-workflow":
        github.run["path"] = ".github/workflows/release-dry-run.yml"
    elif change == "fork":
        github.run["repository"]["full_name"] = "other/Raticode"
    elif change == "bad-attestation":
        github.attestation_valid = False
    elif change == "not-ready":
        github.releases[0]["body"] = "Uploading"
    elif change == "ambiguous":
        manifest["run_attempt"] = 2
        (output / candidate.MANIFEST).write_text(json.dumps(manifest))
        candidate.stage(REPO, output)
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        candidate.publish(REPO)
    assert github.publish_calls == 0
    assert all(r["draft"] for r in github.releases)


def test_stage_refuses_to_overwrite_changed_remote_asset(
    candidate: Any,
    prepared: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output, _ = prepared
    github = GitHub(candidate, monkeypatch)
    candidate.stage(REPO, output)
    github.files[1]["gof-linux-x64"] = b"tampered"
    with pytest.raises(ValueError, match="Existing draft asset differs"):
        candidate.stage(REPO, output)
    assert github.files[1]["gof-linux-x64"] == b"tampered"


def test_tag_without_candidate_never_creates_a_release(
    candidate: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    github = GitHub(candidate, monkeypatch)
    with pytest.raises(ValueError, match="No verified draft"):
        candidate.publish(REPO)
    assert not github.releases


@pytest.mark.parametrize("value, expected", [(None, False), ("false", False), ("true", True)])
def test_signing_policy_is_explicit(
    candidate: Any, monkeypatch: pytest.MonkeyPatch, value: str | None, expected: bool
) -> None:
    if value is None:
        monkeypatch.delenv("SIGNED_RELEASE", raising=False)
    else:
        monkeypatch.setenv("SIGNED_RELEASE", value)
    assert candidate.signing_policy() is expected


@pytest.mark.parametrize("value", ["", "True", "0", "signed"])
def test_unknown_signing_policy_is_rejected(
    candidate: Any, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("SIGNED_RELEASE", value)
    with pytest.raises(ValueError, match="SIGNED_RELEASE"):
        candidate.signing_policy()


def test_release_notes_disclose_signing_and_manual_updates(
    candidate: Any, prepared: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    output, manifest = prepared
    github = GitHub(candidate, monkeypatch)
    candidate.stage(REPO, output)
    body = github.releases[0]["body"]
    if manifest["signed"]:
        assert "Developer ID signed and notarized" in body
    else:
        assert "no verified publisher signature" in body
        assert "macOS updates use manual downloads" in body


def test_signed_policy_cannot_accept_unsigned_artifacts(
    candidate: Any, prepared: Any, tmp_path: Path
) -> None:
    _, manifest = prepared
    for group in ("windows", "macos"):
        with pytest.raises(ValueError, match="missing|unexpected"):
            candidate.verify_group(
                tmp_path / f"input/gofer-flow-{group}",
                group,
                candidate.versions(),
                not manifest["signed"],
            )


@pytest.mark.parametrize("prepared", [True], indirect=True)
@pytest.mark.parametrize("group", ["windows", "macos"])
def test_signed_candidate_still_rejects_failed_platform_verification(
    candidate: Any, prepared: Any, tmp_path: Path, group: str
) -> None:
    folder = tmp_path / f"input/gofer-flow-{group}"
    if group == "windows":
        path = folder / "signatures-windows.json"
        evidence = json.loads(path.read_text())
        evidence[0]["status"] = "NotSigned"
    else:
        path = folder / "notarization-macos-dmg.json"
        evidence = {"status": "Invalid"}
    path.write_text(json.dumps(evidence))
    checksum = folder / f"checksums-{group}.txt"
    checksum.write_text(
        "".join(
            f"{candidate.digest(p)}  {p.name}\n" for p in sorted(folder.iterdir()) if p != checksum
        )
    )
    with pytest.raises(ValueError, match="Invalid Windows signature|notarization not accepted"):
        candidate.verify_group(folder, group, candidate.versions(), signed=True)
