from __future__ import annotations

import base64
import copy
import hashlib
import importlib.util
import json
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

REPO = "zacharyivie/Raticode"
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


TEAM = "UG6K997W6C"
FORK = "someone/Raticode"


UPDATER_FILES = {
    "linux": ["{target}", "Raticode-{version}-amd64.deb", "Raticode-{version}-x86_64.rpm"],
    "windows": ["{target}"],
    "macos": ["{target}", "Raticode-{version}-arm64.dmg"],
}


def sha512(path: Path) -> str:
    return base64.b64encode(hashlib.sha512(path.read_bytes()).digest()).decode()


def write_updater_metadata(
    candidate: Any, folder: Path, group: str, version: str, target: str | None = None
) -> None:
    """electron-builder's latest-*.yml for the fixture's packages, as the build writes it."""
    target = target or candidate.updater_target(group, version)
    names = [n.format(target=target, version=version) for n in UPDATER_FILES[group]]
    lines = [f"version: {version}", "files:"]
    for name in names:
        size = (folder / name).stat().st_size
        lines += [f"  - url: {name}", f"    sha512: {sha512(folder / name)}", f"    size: {size}"]
    lines += [
        f"path: {target}",
        f"sha512: {sha512(folder / target)}",
        "releaseDate: '2026-11-11T05:46:21.892Z'",
    ]
    (folder / candidate.UPDATER_METADATA[group]).write_text("\n".join(lines) + "\n")


def write_candidate_input(
    candidate: Any, source: Path, windows_signed: bool, macos_signed: bool
) -> list[dict[str, Any]]:
    """Platform artifacts as the build jobs upload them, with matching evidence."""
    source.mkdir()
    version = candidate.versions()
    artifacts = []
    groups = candidate.required_files(version, windows_signed, macos_signed)
    for index, (group, expected) in enumerate(groups.items(), 1):
        folder = source / f"raticode-{group}"
        folder.mkdir()
        for name in expected:
            (folder / name).write_text(f"fixture {name}")
        if group != "audit-evidence":
            write_updater_metadata(candidate, folder, group, version)
            smoke: dict[str, Any] = {"status": "passed", "version": version}
            if group == "macos":
                smoke["signing"] = "developer-id" if macos_signed else "unsigned"
                smoke["update_feed"] = candidate.publish_feed()
                if macos_signed:
                    smoke["team_id"] = TEAM
            (folder / f"package-tests-{group}.json").write_text(json.dumps(smoke))
        signed = windows_signed if group == "windows" else macos_signed
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
    return artifacts


# Releases sign the Mac packages; Windows signing is optional and still unconfigured.
@pytest.fixture(params=[(False, True), (True, True)], ids=["mac-signed", "all-signed"])
def prepared(
    candidate: Any, tmp_path: Path, request: pytest.FixtureRequest
) -> tuple[Path, dict[str, Any]]:
    windows_signed, macos_signed = request.param
    artifacts = write_candidate_input(candidate, tmp_path / "input", windows_signed, macos_signed)
    output = tmp_path / "release"
    manifest = candidate.create_manifest(
        tmp_path / "input",
        output,
        REPO,
        SHA,
        42,
        1,
        artifacts,
        windows_signed=windows_signed,
        macos_signed=macos_signed,
    )
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
        # Commits with build provenance, and which ones publication checked.
        self.attested = {SHA}
        self.verified: list[str] = []
        self.runs: dict[int, dict[str, Any]] = {}
        self.sha = SHA
        # The checked-out tag commit, and each commit's file tree.
        self.head = SHA
        self.trees: dict[str, str] = {}
        self.notes_request: dict[str, Any] | None = None
        self.run: dict[str, Any] = {
            "conclusion": "success",
            "status": "completed",
            "head_sha": SHA,
            "head_branch": "stage",
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
            lambda *a, **kw: subprocess.CompletedProcess(a, 0, stdout=self.head + "\n"),
        )

    def api(self, endpoint: str, payload: Any = None, method: str = "GET") -> Any:
        route = endpoint.split(f"repos/{REPO}/", 1)[1].split("?", 1)[0]
        if route.startswith("commits/"):
            return {"sha": self.sha}
        if route.startswith("git/commits/"):
            commit = route.rsplit("/", 1)[1]
            return {"sha": commit, "tree": {"sha": self.trees.get(commit, f"tree-{commit}")}}
        if route.startswith("git/trees/"):
            tree = route.removeprefix("git/trees/")
            return {"tree": [{"path": "src", "sha": tree}, {"path": "mobile", "sha": "any"}]}
        if route.startswith("actions/runs/"):
            run_id = int(route.split("/")[2])
            return copy.deepcopy(self.runs.get(run_id, self.run))
        if route == "releases/generate-notes":
            self.notes_request = payload
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
            source = args[args.index("--source-digest") + 1]
            assert f"{REPO}/{self.candidate.WORKFLOW}" in args
            assert args[args.index("--source-ref") + 1] == "refs/heads/stage"
            self.verified.append(source)
            if not self.attestation_valid or source not in self.attested:
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
        manifest["macos_signed"] = "true"
    elif change == "signing-mismatch":
        manifest["windows_signed"] = not manifest["windows_signed"]
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
    folder = tmp_path / "input/raticode-linux"
    if change == "missing":
        (folder / "raticode-cli-linux-x64").unlink()
    elif change == "extra":
        (folder / "unexpected.exe").write_text("unexpected")
    elif change == "corrupt":
        (folder / "raticode-cli-linux-x64").write_text("different bytes")
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


@pytest.mark.parametrize("draft_tag", ["untagged-b56196d48257bff84c18", "reviewed-draft"])
def test_publication_finds_renamed_draft(
    candidate: Any, prepared: Any, monkeypatch: pytest.MonkeyPatch, draft_tag: str
) -> None:
    output, _ = prepared
    github = GitHub(candidate, monkeypatch)
    candidate.stage(REPO, output)
    github.releases[0]["tag_name"] = draft_tag
    snapshot = copy.deepcopy(github.files)
    candidate.publish(REPO)
    candidate.publish(REPO)
    assert github.publish_calls == 1
    assert github.files == snapshot
    assert github.releases[0]["tag_name"] == f"v{candidate.versions()}"


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
        "main-branch-run",
        "untrusted-workflow",
        "fork",
        "bad-attestation",
        "not-ready",
        "expired",
        "ambiguous",
        "wrong-manifest-sha",
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
    github.releases[0]["tag_name"] = "untagged-renamed-draft"
    if change == "wrong-tag":
        monkeypatch.setenv("GITHUB_REF_NAME", "v9.9.9")
    elif change == "moved-tag":
        github.sha = "b" * 40
    elif change == "corrupt":
        github.files[1]["raticode-cli-linux-x64"] = b"tampered"
    elif change == "extra-asset":
        github.files[1]["surprise.exe"] = b"extra"
    elif change == "missing-asset":
        del github.files[1]["raticode-cli-linux-x64"]
    elif change == "failed-run":
        github.run["conclusion"] = "failure"
    elif change == "pending-run":
        github.run["status"] = "in_progress"
    elif change == "wrong-run-sha":
        github.run["head_sha"] = "b" * 40
    elif change == "untrusted-branch":
        github.run["head_branch"] = "feature"
    elif change == "main-branch-run":
        # Candidates now come only from stage; a main run no longer qualifies.
        github.run["head_branch"] = "main"
    elif change == "untrusted-workflow":
        github.run["path"] = ".github/workflows/release-dry-run.yml"
    elif change == "fork":
        github.run["repository"]["full_name"] = "other/Raticode"
    elif change == "bad-attestation":
        github.attestation_valid = False
    elif change == "not-ready":
        github.releases[0]["body"] = "Uploading"
    elif change == "wrong-manifest-sha":
        manifest["commit"] = "b" * 40
        path = output / candidate.MANIFEST
        path.write_text(json.dumps(manifest))
        github.files[1][candidate.MANIFEST] = path.read_bytes()
        github.releases[0]["body"] = f"{candidate.READY}{candidate.digest(path)} -->"
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
    github.files[1]["raticode-cli-linux-x64"] = b"tampered"
    with pytest.raises(ValueError, match="Existing draft asset differs"):
        candidate.stage(REPO, output)
    assert github.files[1]["raticode-cli-linux-x64"] == b"tampered"


def test_tag_without_candidate_never_creates_a_release(
    candidate: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    github = GitHub(candidate, monkeypatch)
    with pytest.raises(ValueError, match="No verified draft"):
        candidate.publish(REPO)
    assert not github.releases


@pytest.mark.parametrize("name", ["MACOS_SIGNED", "WINDOWS_SIGNED"])
@pytest.mark.parametrize("value, expected", [(None, False), ("false", False), ("true", True)])
def test_signing_policy_is_explicit(
    candidate: Any, monkeypatch: pytest.MonkeyPatch, name: str, value: str | None, expected: bool
) -> None:
    if value is None:
        monkeypatch.delenv(name, raising=False)
    else:
        monkeypatch.setenv(name, value)
    assert candidate.signing_policy(name) is expected


@pytest.mark.parametrize("value", ["", "True", "0", "signed"])
def test_unknown_signing_policy_is_rejected(
    candidate: Any, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("MACOS_SIGNED", value)
    with pytest.raises(ValueError, match="MACOS_SIGNED"):
        candidate.signing_policy("MACOS_SIGNED")


def test_release_notes_disclose_signing_and_manual_updates(
    candidate: Any, prepared: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    output, manifest = prepared
    github = GitHub(candidate, monkeypatch)
    candidate.stage(REPO, output)
    body = github.releases[0]["body"]
    assert "macOS packages are Developer ID signed, notarized by Apple and stapled" in body
    # Copies from unsigned releases can't install a signed update themselves.
    assert "download the DMG once" in body
    if manifest["windows_signed"]:
        assert "Windows packages are signed." in body
    else:
        assert "Windows packages have no verified publisher signature" in body


def test_signed_policy_cannot_accept_differently_signed_artifacts(
    candidate: Any, prepared: Any, tmp_path: Path
) -> None:
    _, manifest = prepared
    version = candidate.versions()
    folder = tmp_path / "input/raticode-macos"
    with pytest.raises(ValueError, match="missing|unexpected"):
        candidate.verify_group(folder, "macos", version, macos_signed=False)
    with pytest.raises(ValueError, match="missing|unexpected"):
        candidate.verify_group(
            tmp_path / "input/raticode-windows",
            "windows",
            version,
            windows_signed=not manifest["windows_signed"],
        )


@pytest.mark.parametrize("prepared", [(True, True)], indirect=True)
@pytest.mark.parametrize("group", ["windows", "macos"])
def test_signed_candidate_still_rejects_failed_platform_verification(
    candidate: Any, prepared: Any, tmp_path: Path, group: str
) -> None:
    folder = tmp_path / f"input/raticode-{group}"
    if group == "windows":
        path = folder / "signatures-windows.json"
        evidence = json.loads(path.read_text())
        evidence[0]["status"] = "NotSigned"
    else:
        path = folder / "notarization-macos-dmg.json"
        evidence = {"status": "Invalid"}
    path.write_text(json.dumps(evidence))
    _rewrite_checksums(candidate, folder, group)
    with pytest.raises(ValueError, match="Invalid Windows signature|notarization not accepted"):
        candidate.verify_group(
            folder, group, candidate.versions(), windows_signed=True, macos_signed=True
        )


def _rewrite_checksums(candidate: Any, folder: Path, group: str) -> None:
    checksum = folder / f"checksums-{group}.txt"
    checksum.write_text(
        "".join(
            f"{candidate.digest(p)}  {p.name}\n" for p in sorted(folder.iterdir()) if p != checksum
        )
    )


@pytest.mark.parametrize("change", ["unsigned-app", "no-record", "no-team"])
def test_mac_packages_must_have_been_checked_as_developer_id_signed(
    candidate: Any, prepared: Any, tmp_path: Path, change: str
) -> None:
    folder = tmp_path / "input/raticode-macos"
    path = folder / "package-tests-macos.json"
    smoke = json.loads(path.read_text())
    if change == "unsigned-app":
        smoke["signing"] = "unsigned"
    elif change == "no-record":
        del smoke["signing"]
    else:
        del smoke["team_id"]
    path.write_text(json.dumps(smoke))
    _rewrite_checksums(candidate, folder, "macos")
    with pytest.raises(ValueError, match="did not confirm developer-id|did not record the signing"):
        candidate.verify_group(folder, "macos", candidate.versions(), macos_signed=True)


def test_mac_packages_signed_by_another_team_are_refused(
    candidate: Any, prepared: Any, tmp_path: Path
) -> None:
    assert TEAM == candidate.MACOS_TEAM_ID
    folder = tmp_path / "input/raticode-macos"
    path = folder / "package-tests-macos.json"
    smoke = json.loads(path.read_text())
    # A well-formed team ID that isn't the release team, such as a certificate swapped
    # into the signing secrets, still fails before anything is staged or published.
    smoke["team_id"] = "ZZZZZ99999"
    path.write_text(json.dumps(smoke))
    _rewrite_checksums(candidate, folder, "macos")
    with pytest.raises(ValueError, match="not the release team UG6K997W6C"):
        candidate.verify_group(folder, "macos", candidate.versions(), macos_signed=True)


def test_this_repository_cannot_prepare_unsigned_mac_packages(
    candidate: Any, tmp_path: Path
) -> None:
    artifacts = write_candidate_input(candidate, tmp_path / "input", False, False)
    with pytest.raises(ValueError, match="must be Developer ID signed"):
        candidate.create_manifest(
            tmp_path / "input", tmp_path / "release", REPO, SHA, 42, 1, artifacts
        )
    assert not (tmp_path / "release").exists()


def test_an_unsigned_mac_candidate_is_never_published(
    candidate: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A fork's explicitly unsigned candidate stages, but no tag can publish it.
    monkeypatch.setattr(candidate, "CANONICAL_REPOSITORY", FORK)
    artifacts = write_candidate_input(candidate, tmp_path / "input", False, False)
    output = tmp_path / "release"
    manifest = candidate.create_manifest(tmp_path / "input", output, REPO, SHA, 42, 1, artifacts)
    assert manifest["macos_signed"] is False
    assert "macos-notarization" not in manifest["checks"]
    github = GitHub(candidate, monkeypatch)
    candidate.stage(REPO, output)
    assert "macOS packages have no Developer ID signature" in github.releases[0]["body"]
    with pytest.raises(ValueError, match="Expected exactly one verified"):
        candidate.publish(REPO)
    assert github.publish_calls == 0
    assert all(r["draft"] for r in github.releases)


def test_candidates_from_before_mac_signing_are_refused(candidate: Any, prepared: Any) -> None:
    _, manifest = prepared
    old = {k: v for k, v in manifest.items() if k not in {"windows_signed", "macos_signed"}}
    old.update(schema=1, signed=False)
    with pytest.raises(ValueError, match="schema 2"):
        candidate.validate_manifest(old, REPO, SHA, candidate.versions())


SQUASH = "c" * 40


def test_tag_on_the_squash_merge_into_main_publishes_the_staged_candidate(
    candidate: Any, prepared: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    output, _ = prepared
    github = GitHub(candidate, monkeypatch)
    candidate.stage(REPO, output)
    github.verified.clear()
    # Squash merging stage into main makes a new commit with the same files.
    github.sha = github.head = SQUASH
    github.trees = {SHA: "same-files", SQUASH: "same-files"}

    candidate.publish(REPO)

    release = github.releases[0]
    assert github.publish_calls == 1
    assert not release["draft"] and release["tag_name"] == f"v{candidate.versions()}"
    assert release["target_commitish"] == SQUASH
    # Provenance is still checked against the staged commit the files were built from.
    assert set(github.verified) == {SHA}


def test_published_desktop_release_is_the_repositorys_latest(
    candidate: Any, prepared: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    output, _ = prepared
    github = GitHub(candidate, monkeypatch)
    candidate.stage(REPO, output)

    candidate.publish(REPO)

    assert github.releases[0]["make_latest"] == "true"


def test_phone_app_changes_do_not_change_the_desktop_files(
    candidate: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    trees = {
        "root-a": [("src", "same"), ("mobile", "one")],
        "root-b": [("src", "same"), ("mobile", "two")],
        "root-c": [("src", "other"), ("mobile", "one")],
    }

    def api(endpoint: str) -> dict[str, Any]:
        route = endpoint.split(f"repos/{REPO}/", 1)[1]
        if route.startswith("git/commits/"):
            return {"tree": {"sha": "root-" + route[-1]}}
        entries = trees[route.removeprefix("git/trees/")]
        return {"tree": [{"path": path, "sha": sha} for path, sha in entries]}

    monkeypatch.setattr(candidate, "api", api)
    assert candidate.same_source(REPO, "commit-a", "commit-b")
    assert not candidate.same_source(REPO, "commit-a", "commit-c")


def test_release_notes_start_at_the_previous_desktop_release(
    candidate: Any, prepared: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    output, _ = prepared
    github = GitHub(candidate, monkeypatch)
    for release_id, tag, published in (
        (1, "v0.0.1", "2026-11-01T00:00:00Z"),
        (2, "mobile-v9.9.9", "2026-11-02T00:00:00Z"),
    ):
        github.releases.append(
            {"id": release_id, "tag_name": tag, "draft": False, "published_at": published}
        )
        github.files[release_id] = {}

    candidate.stage(REPO, output)

    assert github.notes_request is not None
    assert github.notes_request["previous_tag_name"] == "v0.0.1"


def test_tag_on_a_main_commit_with_different_files_is_refused(
    candidate: Any, prepared: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    output, _ = prepared
    github = GitHub(candidate, monkeypatch)
    candidate.stage(REPO, output)
    github.verified.clear()
    github.sha = github.head = SQUASH

    with pytest.raises(ValueError, match="Expected exactly one verified"):
        candidate.publish(REPO)
    assert github.publish_calls == 0
    assert github.verified == []


def _second_candidate(
    candidate: Any, github: Any, output: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> str:
    """Stage another push of the same files from a different stage commit."""
    other = "d" * 40
    copy_dir = tmp_path / "other-release"
    shutil.copytree(output, copy_dir)
    manifest = json.loads((copy_dir / candidate.MANIFEST).read_text())
    manifest.update(commit=other, run_id=43)
    (copy_dir / candidate.MANIFEST).write_text(json.dumps(manifest))
    github.attested.add(other)
    github.runs[43] = {**github.run, "head_sha": other}
    monkeypatch.setenv("GITHUB_SHA", other)
    candidate.stage(REPO, copy_dir)
    monkeypatch.setenv("GITHUB_SHA", SHA)
    return other


def test_a_candidate_from_the_tagged_commit_wins_over_one_with_the_same_files(
    candidate: Any, prepared: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output, _ = prepared
    github = GitHub(candidate, monkeypatch)
    candidate.stage(REPO, output)
    other = _second_candidate(candidate, github, output, tmp_path, monkeypatch)
    github.trees = {SHA: "same-files", other: "same-files"}

    candidate.publish(REPO)

    published = [r for r in github.releases if not r["draft"]]
    assert [r["id"] for r in published] == [1]


def test_two_stage_candidates_with_the_squashed_files_are_ambiguous(
    candidate: Any, prepared: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output, _ = prepared
    github = GitHub(candidate, monkeypatch)
    candidate.stage(REPO, output)
    other = _second_candidate(candidate, github, output, tmp_path, monkeypatch)
    github.sha = github.head = SQUASH
    github.trees = {SHA: "same-files", other: "same-files", SQUASH: "same-files"}

    with pytest.raises(ValueError, match="Expected exactly one verified"):
        candidate.publish(REPO)
    assert github.publish_calls == 0


@pytest.mark.parametrize("command", ["manifest", "stage"])
@pytest.mark.parametrize("ref", ["refs/heads/main", "refs/heads/dev", "refs/tags/v1.0.0"])
def test_candidates_are_only_prepared_on_stage(
    candidate: Any, monkeypatch: pytest.MonkeyPatch, command: str, ref: str
) -> None:
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    monkeypatch.setenv("GITHUB_REF", ref)
    monkeypatch.setattr("sys.argv", ["release-candidate.py", command])
    with pytest.raises(ValueError, match="trusted stage branch"):
        candidate.main()


@pytest.mark.parametrize(
    ("change", "error"),
    [
        ("dmg", "must point updates at Raticode-.*-arm64.zip"),
        ("hash", "latest-mac.yml hash doesn't match Raticode-.*-arm64.zip"),
        ("version", "latest-mac.yml is not for version"),
        ("feed", "did not confirm the apps' update feed"),
    ],
)
def test_mac_candidate_must_carry_an_accurate_update_feed(
    candidate: Any, tmp_path: Path, change: str, error: str
) -> None:
    """Signed Macs update from the zip that latest-mac.yml names, so staging checks it."""
    write_candidate_input(candidate, tmp_path / "input", False, True)
    folder = tmp_path / "input/raticode-macos"
    version = candidate.versions()
    if change == "dmg":
        write_updater_metadata(
            candidate, folder, "macos", version, target=f"Raticode-{version}-arm64.dmg"
        )
    elif change == "hash":
        (folder / f"Raticode-{version}-arm64.zip").write_text("changed after the metadata")
    elif change == "version":
        metadata = folder / "latest-mac.yml"
        metadata.write_text(metadata.read_text().replace(f"version: {version}", "version: 0.0.1"))
    else:
        path = folder / "package-tests-macos.json"
        smoke = json.loads(path.read_text())
        del smoke["update_feed"]
        path.write_text(json.dumps(smoke))
    _rewrite_checksums(candidate, folder, "macos")
    with pytest.raises(ValueError, match=error):
        candidate.verify_group(folder, "macos", version, macos_signed=True)


def test_updater_metadata_reader_matches_electron_builder_output(candidate: Any) -> None:
    top, files = candidate.read_updater_metadata(
        "version: 0.5.3\nfiles:\n  - url: Raticode-0.5.3-x86_64.AppImage\n"
        "    sha512: abc==\n    size: 277792927\n    blockMapSize: 288947\n"
        "  - url: Raticode-0.5.3-amd64.deb\n    sha512: def==\n    size: 1\n"
        "path: Raticode-0.5.3-x86_64.AppImage\nsha512: abc==\n"
        "releaseDate: '2026-11-11T05:43:19.556Z'\n"
    )
    assert top == {
        "version": "0.5.3",
        "path": "Raticode-0.5.3-x86_64.AppImage",
        "sha512": "abc==",
        "releaseDate": "2026-11-11T05:43:19.556Z",
    }
    assert [f["url"] for f in files] == [
        "Raticode-0.5.3-x86_64.AppImage",
        "Raticode-0.5.3-amd64.deb",
    ]
    assert files[0]["blockMapSize"] == "288947"
