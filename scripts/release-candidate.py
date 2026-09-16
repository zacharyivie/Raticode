"""Stage verified release files, then publish their verified draft without rebuilding.

Only the candidate workflow writes drafts. The tag workflow verifies the attested
manifest, the exact successful source run and every remote byte before publishing.
All subprocess arguments and API payloads are passed without shell interpolation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import tomllib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = "candidate-manifest.json"
WORKFLOW = ".github/workflows/release-candidate.yml"
GROUPS = ("linux", "windows", "macos", "audit-evidence")
MAX_AGE = timedelta(days=7)
READY = "<!-- raticode-candidate-ready:"


def require(condition: Any, message: str) -> None:
    if not condition:
        raise ValueError(message)


def gh(*args: str, output: Path | None = None) -> str:
    if output is not None:
        with output.open("wb") as stream:
            subprocess.run(["gh", *args], stdout=stream, check=True)
        return ""
    return subprocess.run(["gh", *args], check=True, text=True, stdout=subprocess.PIPE).stdout


def api(endpoint: str, payload: dict[str, Any] | None = None, method: str = "GET") -> Any:
    args = ["api", endpoint, "--method", method]
    if payload is None:
        return json.loads(gh(*args))
    with tempfile.TemporaryDirectory() as temporary:
        body = Path(temporary) / "body.json"
        body.write_text(json.dumps(payload), encoding="utf8")
        return json.loads(gh(*args, "--input", str(body)))


def pages(endpoint: str, key: str | None = None) -> list[dict[str, Any]]:
    result = []
    for page in range(1, 1001):
        response = api(f"{endpoint}?per_page=100&page={page}")
        items = response[key] if key else response
        result.extend(items)
        if len(items) < 100:
            return result
    raise ValueError("GitHub pagination exceeded 1000 pages")


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def versions(root: Path = ROOT) -> str:
    python: str = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    frontend = json.loads((root / "frontend/package.json").read_text())["version"]
    lock = json.loads((root / "frontend/package-lock.json").read_text())
    python_lock = tomllib.loads((root / "uv.lock").read_text())
    locked = [p["version"] for p in python_lock["package"] if p["name"] == "gofer-flow"]
    require(re.fullmatch(r"\d+\.\d+\.\d+", python), "Only stable x.y.z versions can be published")
    require(
        frontend == lock["version"] == lock["packages"][""]["version"] == python,
        "Python, frontend and npm lock versions must agree",
    )
    require(locked == [python], "uv.lock project version must agree")
    return python


def required_files(version: str, signed: bool = False) -> dict[str, set[str]]:
    files = {
        "linux": {
            f"Raticode-{version}-x86_64.AppImage",
            f"Raticode-{version}-amd64.deb",
            f"Raticode-{version}-x86_64.rpm",
            f"gofer-flow-cli_{version}_amd64.deb",
            f"gofer-flow-cli-{version}-1.x86_64.rpm",
            "gof-linux-x64",
            "latest-linux.yml",
            "checksums-linux.txt",
            "package-tests-linux.json",
        },
        "windows": {
            f"Raticode-Setup-{version}.exe",
            f"Raticode-Setup-{version}.exe.blockmap",
            "gof-windows-x64.exe",
            "latest.yml",
            "checksums-windows.txt",
            "package-tests-windows.json",
        },
        "macos": {
            f"Raticode-{version}-arm64.dmg",
            f"Raticode-{version}-arm64.dmg.blockmap",
            f"Raticode-{version}-arm64.zip",
            f"Raticode-{version}-arm64.zip.blockmap",
            "gof-macos-arm64",
            "latest-mac.yml",
            "checksums-macos.txt",
            "package-tests-macos.json",
        },
        "audit-evidence": {
            "npm-audit.json",
            "python-audit.json",
            "npm-lock-inventory.json",
            "python-lock-inventory.txt",
        },
    }

    if signed:
        files["windows"].add("signatures-windows.json")
        files["macos"].update({"notarization-macos-dmg.json", "notarization-macos-cli.json"})
    return files


def verify_group(folder: Path, group: str, version: str, signed: bool = False) -> None:
    expected = required_files(version, signed)[group]
    actual = {p.name for p in folder.iterdir()}
    # AppImage blockmaps can be embedded instead of separate, depending on builder.
    optional = {f"Raticode-{version}-x86_64.AppImage.blockmap"} if group == "linux" else set()
    require(
        expected <= actual <= expected | optional,
        f"{group}: missing {sorted(expected - actual)}, "
        f"unexpected {sorted(actual - expected - optional)}",
    )
    require(
        all(p.is_file() and not p.is_symlink() for p in folder.iterdir()),
        f"{group}: only regular files are accepted",
    )
    if group == "audit-evidence":
        return
    checksums = {}
    # PowerShell can emit a UTF-8 BOM. Universal newlines handle Windows CRLF.
    for line in (folder / f"checksums-{group}.txt").read_text(encoding="utf-8-sig").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  ([^/\\]+)", line)
        require(match, f"Malformed {group} checksum line")
        assert match is not None
        checksum, name = match.groups()
        require(name not in checksums, f"Duplicate checksum: {name}")
        checksums[name] = checksum
    require(
        set(checksums) == actual - {f"checksums-{group}.txt"},
        f"{group}: checksums do not cover exactly the uploaded files",
    )
    for name, checksum in checksums.items():
        require(digest(folder / name) == checksum, f"Checksum mismatch: {name}")
    smoke = json.loads((folder / f"package-tests-{group}.json").read_text())
    require(
        smoke.get("status") == "passed" and smoke.get("version") == version,
        f"{group}: missing successful distribution tests",
    )
    if signed and group == "windows":
        signatures = json.loads(
            (folder / "signatures-windows.json").read_text(encoding="utf-8-sig")
        )
        require(
            {item["file"] for item in signatures} == {n for n in actual if n.endswith(".exe")},
            "Signature evidence must cover every Windows executable",
        )
        require(all(item["status"] == "Valid" for item in signatures), "Invalid Windows signature")
    if signed and group == "macos":
        for kind in ("dmg", "cli"):
            receipt = json.loads((folder / f"notarization-macos-{kind}.json").read_text())
            require(receipt.get("status") == "Accepted", f"macOS {kind} notarization not accepted")


def create_manifest(
    source: Path,
    output: Path,
    repo: str,
    sha: str,
    run_id: int,
    attempt: int,
    artifacts: list[dict[str, Any]],
    signed: bool = False,
) -> dict[str, Any]:
    version = versions()
    require(re.fullmatch(r"[0-9a-f]{40}", sha), "Expected full source SHA")
    require(
        {p.name for p in source.iterdir()} == {f"gofer-flow-{g}" for g in GROUPS},
        "Expected exactly all three platform artifacts and audit evidence",
    )
    records = [a for a in artifacts if a["name"].startswith("gofer-flow-")]
    require(
        len(records) == len(GROUPS)
        and {a["name"] for a in records} == {f"gofer-flow-{g}" for g in GROUPS},
        "Run artifact inventory is incomplete or ambiguous",
    )
    require(all(not a["expired"] and a["id"] for a in records), "Candidate artifacts expired")
    output.mkdir(exist_ok=True)
    require(not list(output.iterdir()), "Manifest output directory must be empty")
    files = {}
    for group in GROUPS:
        folder = source / f"gofer-flow-{group}"
        verify_group(folder, group, version, signed)
        for path in sorted(folder.iterdir()):
            require(path.name not in files, f"Artifact filename collision: {path.name}")
            files[path.name] = {"sha256": digest(path), "size": path.stat().st_size, "group": group}
            shutil.copyfile(path, output / path.name)
    manifest = {
        "schema": 1,
        "repository": repo,
        "commit": sha,
        "version": version,
        "workflow": WORKFLOW,
        "run_id": run_id,
        "run_attempt": attempt,
        "created_at": datetime.now(UTC).isoformat(),
        "signed": signed,
        "artifacts": [{k: a[k] for k in ("id", "name", "digest")} for a in records],
        "checks": [
            "source-validation",
            "platform-runtime",
            "distribution-backends",
            "updater-hashes",
        ]
        + (["signatures-notarization"] if signed else []),
        "files": files,
    }
    (output / MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def validate_manifest(manifest: dict[str, Any], repo: str, sha: str, version: str) -> None:
    require(
        manifest.get("schema") == 1 and type(manifest.get("signed")) is bool,
        "Candidate must declare a boolean signing policy in schema 1",
    )
    for key, expected_value in (
        ("repository", repo),
        ("commit", sha),
        ("version", version),
        ("workflow", WORKFLOW),
    ):
        require(manifest.get(key) == expected_value, f"Candidate {key} mismatch")
    require(
        ("signatures-notarization" in manifest.get("checks", [])) == manifest["signed"],
        "Candidate signing checks disagree with its signing policy",
    )
    files = manifest["files"]
    expected_groups = required_files(version, manifest["signed"])
    for group, expected in expected_groups.items():
        actual = {name for name, info in files.items() if info["group"] == group}
        optional = (
            {f"Raticode-{version}-x86_64.AppImage.blockmap"} if group == "linux" else set()
        )
        require(expected <= actual <= expected | optional, f"Candidate {group} inventory mismatch")
    for name, info in files.items():
        require(
            name not in {".", "..", MANIFEST} and re.fullmatch(r"[A-Za-z0-9_.-]+", name),
            "Unsafe candidate filename",
        )
        require(
            info["group"] in GROUPS and re.fullmatch(r"[0-9a-f]{64}", info["sha256"]),
            "Malformed candidate file metadata",
        )
        require(isinstance(info["size"], int) and info["size"] > 0, "Empty candidate asset")
    require(
        isinstance(manifest["run_id"], int)
        and manifest["run_id"] > 0
        and isinstance(manifest["run_attempt"], int)
        and manifest["run_attempt"] > 0,
        "Invalid source run identity",
    )


def validate_run(run: dict[str, Any], sha: str, repo: str) -> None:
    require(
        run.get("conclusion") == "success" and run.get("status") == "completed",
        "Candidate preparation run has not succeeded. Wait, then rerun publication.",
    )
    require(
        run.get("head_sha") == sha
        and run.get("head_branch") == "main"
        and run.get("event") in {"push", "workflow_dispatch"}
        and run.get("path") == WORKFLOW
        and run.get("repository", {}).get("full_name", "").lower() == repo.lower(),
        "Candidate must come from this repository's trusted main preparation workflow",
    )


def assets(repo: str, release_id: int) -> list[dict[str, Any]]:
    return pages(f"repos/{repo}/releases/{release_id}/assets")


def download(repo: str, asset: dict[str, Any], destination: Path) -> None:
    gh(
        "api",
        f"repos/{repo}/releases/assets/{asset['id']}",
        "-H",
        "Accept: application/octet-stream",
        output=destination,
    )


def verify_remote(
    repo: str,
    release: dict[str, Any],
    directory: Path,
    manifest: dict[str, Any],
    manifest_hash: str,
) -> None:
    entries = assets(repo, release["id"])
    expected = {**manifest["files"], MANIFEST: {"sha256": manifest_hash}}
    require(
        len(entries) == len(expected) and {a["name"] for a in entries} == set(expected),
        "Remote release has missing, duplicate or unexpected assets",
    )
    for entry in entries:
        target = directory / entry["name"]
        download(repo, entry, target)
        info = expected[entry["name"]]
        require(digest(target) == info["sha256"], f"Remote hash mismatch: {entry['name']}")
        if "size" in info:
            require(target.stat().st_size == info["size"], f"Remote size mismatch: {entry['name']}")


def candidate_tag(manifest: dict[str, Any]) -> str:
    return f"candidate-{manifest['commit']}-{manifest['run_id']}-{manifest['run_attempt']}"


def stage(repo: str, directory: Path) -> None:
    manifest_path = directory / MANIFEST
    manifest = json.loads(manifest_path.read_text())
    validate_manifest(manifest, repo, os.environ["GITHUB_SHA"], versions())
    verify_provenance(repo, manifest_path, manifest["commit"])
    tag = candidate_tag(manifest)
    matches = [r for r in pages(f"repos/{repo}/releases") if r["tag_name"] == tag]
    require(len(matches) <= 1, "Ambiguous candidate draft")
    release = (
        matches[0]
        if matches
        else api(
            f"repos/{repo}/releases",
            {
                "tag_name": tag,
                "target_commitish": manifest["commit"],
                "draft": True,
                "name": f"Candidate v{manifest['version']} / {manifest['commit'][:12]}",
                "body": "Candidate upload in progress. Do not publish manually.",
            },
            "POST",
        )
    )
    require(release["draft"], "Refusing to change a public release")
    # Resume only identical assets. Never use --clobber or mutate an uploaded file.
    existing = {a["name"]: a for a in assets(repo, release["id"])}
    expected = {**manifest["files"], MANIFEST: {"sha256": digest(manifest_path)}}
    require(set(existing) <= set(expected), "Draft contains unexpected files")
    with tempfile.TemporaryDirectory() as temporary:
        remote = Path(temporary)
        for name, info in expected.items():
            require(digest(directory / name) == info["sha256"], f"Local hash mismatch: {name}")
            if name in existing:
                download(repo, existing[name], remote / name)
                require(
                    digest(remote / name) == info["sha256"], f"Existing draft asset differs: {name}"
                )
            else:
                gh("release", "upload", tag, str(directory / name), "--repo", repo)
        verify_remote(repo, release, remote, manifest, digest(manifest_path))
    notes = api(
        f"repos/{repo}/releases/generate-notes",
        {
            "tag_name": f"v{manifest['version']}",
            "target_commitish": manifest["commit"],
        },
        "POST",
    )
    signing_note = (
        "Windows packages are signed; macOS packages are Developer ID signed and notarized."
        if manifest["signed"]
        else "These builds have no verified publisher signature or Apple notarization. "
        "Windows may show Unknown publisher or SmartScreen prompts. "
        "macOS may require approval in System Settings > Privacy & Security. "
        "macOS updates use manual downloads from this release page."
    )
    body = (
        notes["body"] + "\n\n" + signing_note + "\n\n"
        f"Source: `{manifest['commit']}`\n\n"
        f"Preparation: https://github.com/{repo}/actions/runs/{manifest['run_id']}"
        f"/attempts/{manifest['run_attempt']}\n\n"
        f"{READY}{digest(manifest_path)} -->"
    )
    api(f"repos/{repo}/releases/{release['id']}", {"body": body}, "PATCH")
    summary = (
        f"Candidate staged: {release['html_url']}\n\n"
        "After this run succeeds, review the draft files and push the version tag at the "
        "exact source commit shown below. Do not use the web Publish button.\n\n" + body + "\n"
    )
    print(summary)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a") as stream:
            stream.write(summary)


def verify_provenance(repo: str, path: Path, sha: str) -> None:
    gh(
        "attestation",
        "verify",
        str(path),
        "--repo",
        repo,
        "--signer-workflow",
        f"{repo}/{WORKFLOW}",
        "--source-digest",
        sha,
        "--source-ref",
        "refs/heads/main",
    )


def read_candidate(
    repo: str, release: dict[str, Any], directory: Path, sha: str, version: str
) -> dict[str, Any]:
    entries = [a for a in assets(repo, release["id"]) if a["name"] == MANIFEST]
    require(len(entries) == 1, "Draft has no unique manifest")
    path = directory / MANIFEST
    download(repo, entries[0], path)
    require(
        f"{READY}{digest(path)} -->" in (release.get("body") or ""),
        "Candidate remote verification has not completed",
    )
    verify_provenance(repo, path, sha)
    manifest: dict[str, Any] = json.loads(path.read_text())
    validate_manifest(manifest, repo, sha, version)
    run = api(f"repos/{repo}/actions/runs/{manifest['run_id']}/attempts/{manifest['run_attempt']}")
    validate_run(run, sha, repo)
    return manifest


def publish(repo: str) -> None:
    tag = os.environ["GITHUB_REF_NAME"]
    version = versions()
    require(tag == f"v{version}", f"Tag {tag} does not match package version v{version}")
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], text=True, stdout=subprocess.PIPE, check=True
    ).stdout.strip()
    # Peel annotated tags on the server too; refuse a tag moved after this run began.
    require(api(f"repos/{repo}/commits/{tag}")["sha"] == sha, "Remote tag moved")
    releases = pages(f"repos/{repo}/releases")
    public = [r for r in releases if r["tag_name"] == tag]
    require(len(public) <= 1, "Ambiguous version release")
    # Draft tag names are editable metadata, including GitHub's untagged-* names.
    # Discover ready drafts by their marker; read_candidate verifies their identity
    # using the attested manifest and successful source run before publication.
    candidates = public or [
        r
        for r in releases
        if r["draft"]
        and READY in (r.get("body") or "")
    ]
    require(
        candidates,
        "No verified draft for this commit. Finish Prepare release candidate on main, "
        "then rerun this publish job. The tag job never builds missing candidates.",
    )
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        ready = []
        for release in candidates:
            try:
                manifest = read_candidate(repo, release, directory, sha, version)
            except (ValueError, subprocess.CalledProcessError) as error:
                if public:
                    raise
                print(f"Skipping unusable draft {release['id']}: {error}")
                continue
            ready.append((release, manifest))
        require(
            len(ready) == 1,
            "Expected exactly one verified, successful candidate for this commit. "
            "Wait for preparation or remove superseded drafts, then rerun publication.",
        )
        release, manifest = ready[0]
        if release["draft"]:
            created = datetime.fromisoformat(manifest["created_at"])
            age = datetime.now(UTC) - created
            require(
                timedelta(0) <= age <= MAX_AGE,
                "Candidate is older than seven days. Prepare and review a fresh candidate.",
            )
        # Re-read the chosen manifest; previous candidates may have overwritten the temp file.
        manifest = read_candidate(repo, release, directory, sha, version)
        manifest_hash = digest(directory / MANIFEST)
        verify_remote(repo, release, directory, manifest, manifest_hash)
        require(api(f"repos/{repo}/commits/{tag}")["sha"] == sha, "Remote tag moved")
        if release["draft"]:
            api(
                f"repos/{repo}/releases/{release['id']}",
                {
                    "tag_name": tag,
                    "target_commitish": sha,
                    "name": tag,
                    "body": release["body"],
                    "draft": False,
                    "prerelease": False,
                    "make_latest": "legacy",
                },
                "PATCH",
            )
        result = api(f"repos/{repo}/releases/{release['id']}")
        require(not result["draft"] and result["tag_name"] == tag, "Publication not confirmed")
        verify_remote(repo, result, directory, manifest, manifest_hash)
        print(f"Published and verified {result['html_url']}. No files were rebuilt or replaced.")


def signing_policy() -> bool:
    value = os.environ.get("SIGNED_RELEASE", "false")
    require(value in {"true", "false"}, "SIGNED_RELEASE must be true or false")
    return value == "true"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("versions", "manifest", "stage", "publish"))
    command = parser.parse_args().command
    if command == "versions":
        print(f"Package versions agree: {versions()}")
        return
    repo = os.environ["GITHUB_REPOSITORY"]
    require(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo), "Invalid repository")
    if command in {"manifest", "stage"}:
        require(os.environ["GITHUB_REF"] == "refs/heads/main", "Candidates require trusted main")
    if command == "manifest":
        run_id = int(os.environ["GITHUB_RUN_ID"])
        create_manifest(
            Path("candidate-input"),
            Path("release-artifacts"),
            repo,
            os.environ["GITHUB_SHA"],
            run_id,
            int(os.environ["GITHUB_RUN_ATTEMPT"]),
            pages(f"repos/{repo}/actions/runs/{run_id}/artifacts", "artifacts"),
            signed=signing_policy(),
        )
    elif command == "stage":
        stage(repo, Path("release-artifacts"))
    else:
        publish(repo)


if __name__ == "__main__":
    main()
