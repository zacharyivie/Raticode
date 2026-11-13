"""Stage verified release files, then publish their verified draft without rebuilding.

Only the candidate workflow writes drafts, from pushes to the stage branch. The tag
workflow verifies the attested manifest, the exact successful source run and every
remote byte before publishing. A version tag may point at the staged commit itself or
at a commit with an identical file tree, such as its squash merge into main.
All subprocess arguments and API payloads are passed without shell interpolation.
"""

from __future__ import annotations

import argparse
import base64
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
# Pushes to this branch prepare candidates. Main receives them by squash merge.
CANDIDATE_BRANCH = "stage"
SCHEMA = 2
# Mac packages of this repository's releases must be Developer ID signed, notarized and
# stapled. A fork may stage an unsigned candidate for testing, but nothing publishes one.
CANONICAL_REPOSITORY = "zacharyivie/Raticode"
MAC_SIGNING_MODES = {True: "developer-id", False: "unsigned"}
# The Apple Developer team that signs this repository's Mac releases, read from the
# published v0.5.3 evidence. A certificate from any other team is refused, even when
# the signing secrets hold it. Change this only when the releases move to a new team.
MACOS_TEAM_ID = "UG6K997W6C"


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
    locked = [p["version"] for p in python_lock["package"] if p["name"] == "raticode"]
    require(re.fullmatch(r"\d+\.\d+\.\d+", python), "Only stable x.y.z versions can be published")
    require(
        frontend == lock["version"] == lock["packages"][""]["version"] == python,
        "Python, frontend and npm lock versions must agree",
    )
    require(locked == [python], "uv.lock project version must agree")
    return python


def required_files(
    version: str, windows_signed: bool = False, macos_signed: bool = False
) -> dict[str, set[str]]:
    files = {
        "linux": {
            f"Raticode-{version}-x86_64.AppImage",
            f"Raticode-{version}-amd64.deb",
            f"Raticode-{version}-x86_64.rpm",
            f"raticode-cli_{version}_amd64.deb",
            f"raticode-cli-{version}-1.x86_64.rpm",
            "raticode-cli-linux-x64",
            "latest-linux.yml",
            "checksums-linux.txt",
            "package-tests-linux.json",
        },
        "windows": {
            f"Raticode-Setup-{version}.exe",
            f"Raticode-Setup-{version}.exe.blockmap",
            "raticode-cli-windows-x64.exe",
            "latest.yml",
            "checksums-windows.txt",
            "package-tests-windows.json",
        },
        "macos": {
            f"Raticode-{version}-arm64.dmg",
            f"Raticode-{version}-arm64.dmg.blockmap",
            f"Raticode-{version}-arm64.zip",
            f"Raticode-{version}-arm64.zip.blockmap",
            "raticode-cli-macos-arm64",
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

    if windows_signed:
        files["windows"].add("signatures-windows.json")
    if macos_signed:
        files["macos"].update({"notarization-macos-dmg.json", "notarization-macos-cli.json"})
    return files


UPDATER_METADATA = {"linux": "latest-linux.yml", "windows": "latest.yml", "macos": "latest-mac.yml"}


def updater_target(group: str, version: str) -> str:
    """The file each platform's updater installs. Squirrel.Mac only accepts the zip."""
    return {
        "linux": f"Raticode-{version}-x86_64.AppImage",
        "windows": f"Raticode-Setup-{version}.exe",
        "macos": f"Raticode-{version}-arm64.zip",
    }[group]


def publish_feed(root: Path = ROOT) -> str:
    """The update feed every packaged app must use, from electron-builder's publish config."""
    publish = json.loads((root / "frontend/package.json").read_text())["build"]["publish"]
    entry = publish[0] if isinstance(publish, list) else publish
    return f"{entry['provider']}:{entry['owner']}/{entry['repo']}"


def read_updater_metadata(text: str) -> tuple[dict[str, str], list[dict[str, str]]]:
    """Read electron-builder's flat latest-*.yml: top-level fields and the files list."""
    top: dict[str, str] = {}
    files: list[dict[str, str]] = []
    in_files = False
    for line in text.splitlines():
        if not line.strip():
            continue
        item = re.fullmatch(r"\s+-\s+(\w+):\s*(.*)", line)
        nested = re.fullmatch(r"\s+(\w+):\s*(.*)", line)
        field = re.fullmatch(r"(\w+):\s*(.*)", line)
        if in_files and item:
            files.append({item[1]: item[2].strip("'\"")})
        elif in_files and nested and files:
            files[-1][nested[1]] = nested[2].strip("'\"")
        elif field:
            in_files = field[1] == "files" and not field[2]
            if not in_files:
                top[field[1]] = field[2].strip("'\"")
        else:
            require(False, f"Unreadable updater metadata line: {line!r}")
    return top, files


def verify_updater_metadata(folder: Path, group: str, version: str) -> None:
    """The installed apps update from these files, so they must describe the final bytes."""
    name = UPDATER_METADATA[group]
    target = updater_target(group, version)
    top, files = read_updater_metadata((folder / name).read_text())
    require(top.get("version") == version, f"{name} is not for version {version}")
    require(top.get("path") == target, f"{name} must point updates at {target}")
    hashes = {}
    for entry in files:
        url = entry.get("url", "")
        require(
            re.fullmatch(r"[A-Za-z0-9_.-]+", url) and (folder / url).is_file(),
            f"{name} lists a file this candidate doesn't have: {url!r}",
        )
        with (folder / url).open("rb") as stream:
            sha512 = base64.b64encode(hashlib.file_digest(stream, "sha512").digest()).decode()
        require(entry.get("sha512") == sha512, f"{name} hash doesn't match {url}")
        require(
            entry.get("size") in (None, str((folder / url).stat().st_size)),
            f"{name} size doesn't match {url}",
        )
        hashes[url] = sha512
    require(
        target in hashes and top.get("sha512") == hashes[target],
        f"{name} must list {target} with its hash",
    )


def verify_group(
    folder: Path,
    group: str,
    version: str,
    windows_signed: bool = False,
    macos_signed: bool = False,
) -> None:
    expected = required_files(version, windows_signed, macos_signed)[group]
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
    verify_updater_metadata(folder, group, version)
    if group == "macos":
        # The package check records how it found the Mac apps signed.
        require(
            smoke.get("signing") == MAC_SIGNING_MODES[macos_signed],
            f"macos: distribution tests did not confirm {MAC_SIGNING_MODES[macos_signed]} signing",
        )
        require(
            smoke.get("update_feed") == publish_feed(),
            "macos: distribution tests did not confirm the apps' update feed",
        )
        if macos_signed:
            require(
                re.fullmatch(r"[A-Z0-9]{10}", str(smoke.get("team_id", ""))),
                "macos: distribution tests did not record the signing team",
            )
            require(
                smoke["team_id"] == MACOS_TEAM_ID,
                f"macos: signed by team {smoke['team_id']}, not the release team "
                f"{MACOS_TEAM_ID}. See MACOS_TEAM_ID in scripts/release-candidate.py.",
            )
    if windows_signed and group == "windows":
        signatures = json.loads(
            (folder / "signatures-windows.json").read_text(encoding="utf-8-sig")
        )
        require(
            {item["file"] for item in signatures} == {n for n in actual if n.endswith(".exe")},
            "Signature evidence must cover every Windows executable",
        )
        require(all(item["status"] == "Valid" for item in signatures), "Invalid Windows signature")
    if macos_signed and group == "macos":
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
    windows_signed: bool = False,
    macos_signed: bool = False,
) -> dict[str, Any]:
    version = versions()
    require(
        macos_signed or repo.lower() != CANONICAL_REPOSITORY.lower(),
        "Raticode's Mac packages must be Developer ID signed and notarized",
    )
    require(re.fullmatch(r"[0-9a-f]{40}", sha), "Expected full source SHA")
    require(
        {p.name for p in source.iterdir()} == {f"raticode-{g}" for g in GROUPS},
        "Expected exactly all three platform artifacts and audit evidence",
    )
    records = [a for a in artifacts if a["name"].startswith("raticode-")]
    require(
        len(records) == len(GROUPS)
        and {a["name"] for a in records} == {f"raticode-{g}" for g in GROUPS},
        "Run artifact inventory is incomplete or ambiguous",
    )
    require(all(not a["expired"] and a["id"] for a in records), "Candidate artifacts expired")
    output.mkdir(exist_ok=True)
    require(not list(output.iterdir()), "Manifest output directory must be empty")
    files = {}
    for group in GROUPS:
        folder = source / f"raticode-{group}"
        verify_group(folder, group, version, windows_signed, macos_signed)
        for path in sorted(folder.iterdir()):
            require(path.name not in files, f"Artifact filename collision: {path.name}")
            files[path.name] = {"sha256": digest(path), "size": path.stat().st_size, "group": group}
            shutil.copyfile(path, output / path.name)
    manifest = {
        "schema": SCHEMA,
        "repository": repo,
        "commit": sha,
        "version": version,
        "workflow": WORKFLOW,
        "run_id": run_id,
        "run_attempt": attempt,
        "created_at": datetime.now(UTC).isoformat(),
        "windows_signed": windows_signed,
        "macos_signed": macos_signed,
        "artifacts": [{k: a[k] for k in ("id", "name", "digest")} for a in records],
        "checks": [
            "source-validation",
            "platform-runtime",
            "distribution-backends",
            "updater-hashes",
        ]
        + (["windows-signatures"] if windows_signed else [])
        + (["macos-notarization"] if macos_signed else []),
        "files": files,
    }
    (output / MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def validate_manifest(manifest: dict[str, Any], repo: str, sha: str, version: str) -> None:
    require(
        manifest.get("schema") == SCHEMA
        and type(manifest.get("windows_signed")) is bool
        and type(manifest.get("macos_signed")) is bool,
        f"Candidate must declare boolean Windows and macOS signing policies in schema {SCHEMA}",
    )
    for key, expected_value in (
        ("repository", repo),
        ("commit", sha),
        ("version", version),
        ("workflow", WORKFLOW),
    ):
        require(manifest.get(key) == expected_value, f"Candidate {key} mismatch")
    checks = manifest.get("checks", [])
    require(
        ("windows-signatures" in checks) == manifest["windows_signed"]
        and ("macos-notarization" in checks) == manifest["macos_signed"],
        "Candidate signing checks disagree with its signing policy",
    )
    files = manifest["files"]
    expected_groups = required_files(version, manifest["windows_signed"], manifest["macos_signed"])
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
        and run.get("head_branch") == CANDIDATE_BRANCH
        and run.get("event") in {"push", "workflow_dispatch"}
        and run.get("path") == WORKFLOW
        and run.get("repository", {}).get("full_name", "").lower() == repo.lower(),
        "Candidate must come from this repository's trusted stage preparation workflow",
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


def desktop_entries(repo: str, sha: str) -> list[tuple[str, str]]:
    """The commit's top-level files and folders, apart from the phone app's."""
    tree = api(f"repos/{repo}/git/commits/{sha}")["tree"]["sha"]
    entries = api(f"repos/{repo}/git/trees/{tree}")["tree"]
    return sorted((e["path"], e["sha"]) for e in entries if e["path"] != "mobile")


def same_source(repo: str, candidate: str, released: str) -> bool:
    """Whether the released commit has exactly the candidate's files.

    A squash merge of stage into main makes a new commit; its files are identical to
    the staged commit's when main held nothing stage didn't already contain. The
    mobile folder is left out because the phone app has its own releases.
    """
    if candidate == released:
        return True
    return desktop_entries(repo, candidate) == desktop_entries(repo, released)


def previous_tag(repo: str, tag: str) -> str | None:
    """The most recently published desktop tag, where its release notes should start."""
    published = [
        r
        for r in pages(f"repos/{repo}/releases")
        if not r["draft"] and r["tag_name"].startswith("v") and r["tag_name"] != tag
    ]
    if not published:
        return None
    return max(published, key=lambda r: r.get("published_at") or "")["tag_name"]


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
    release_tag = f"v{manifest['version']}"
    previous = previous_tag(repo, release_tag)
    notes = api(
        f"repos/{repo}/releases/generate-notes",
        {
            "tag_name": release_tag,
            "target_commitish": manifest["commit"],
            **({"previous_tag_name": previous} if previous else {}),
        },
        "POST",
    )
    signing_note = release_signing_note(manifest)
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
        "After this run succeeds, review the draft files. Then push the version tag at the "
        "source commit shown below, or at its squash merge into main if that commit has "
        "exactly the same files. Do not use the web Publish button.\n\n" + body + "\n"
    )
    print(summary)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a") as stream:
            stream.write(summary)


def release_signing_note(manifest: dict[str, Any]) -> str:
    mac = (
        "macOS packages are Developer ID signed, notarized by Apple and stapled. "
        "A copy installed from an unsigned release (0.5.2 or earlier) can't update "
        "itself: download the DMG once and replace the app in Applications. Signed copies "
        "install later updates themselves."
        if manifest["macos_signed"]
        else "macOS packages have no Developer ID signature or Apple notarization. "
        "macOS may require approval in System Settings > Privacy & Security. "
        "macOS updates use manual downloads from this release page."
    )
    windows = (
        "Windows packages are signed."
        if manifest["windows_signed"]
        else "Windows packages have no verified publisher signature, so Windows may show "
        "Unknown publisher or SmartScreen prompts."
    )
    return mac + "\n\n" + windows


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
        f"refs/heads/{CANDIDATE_BRANCH}",
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
    manifest: dict[str, Any] = json.loads(path.read_text())
    source = manifest.get("commit")
    require(isinstance(source, str) and re.fullmatch(r"[0-9a-f]{40}", source), "Bad source SHA")
    require(
        same_source(repo, source, sha),
        "The tagged commit's files differ from the candidate's staged commit",
    )
    # The attestation binds the manifest to the staged commit it was built from.
    verify_provenance(repo, path, source)
    validate_manifest(manifest, repo, source, version)
    require(
        manifest["macos_signed"],
        "The candidate's Mac packages are not Developer ID signed, notarized and stapled. "
        "Prepare a signed candidate on stage.",
    )
    run = api(f"repos/{repo}/actions/runs/{manifest['run_id']}/attempts/{manifest['run_attempt']}")
    validate_run(run, source, repo)
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
        "No verified draft for this commit. Finish Prepare release candidate on stage, "
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
        # A candidate built from the tagged commit itself wins over ones that only
        # share its files, such as an earlier stage push of the same content.
        ready = [item for item in ready if item[1]["commit"] == sha] or ready
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
                    "make_latest": "true",
                },
                "PATCH",
            )
        result = api(f"repos/{repo}/releases/{release['id']}")
        require(not result["draft"] and result["tag_name"] == tag, "Publication not confirmed")
        verify_remote(repo, result, directory, manifest, manifest_hash)
        print(f"Published and verified {result['html_url']}. No files were rebuilt or replaced.")


def signing_policy(name: str) -> bool:
    value = os.environ.get(name, "false")
    require(value in {"true", "false"}, f"{name} must be true or false")
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
        require(
            os.environ["GITHUB_REF"] == f"refs/heads/{CANDIDATE_BRANCH}",
            "Candidates require the trusted stage branch",
        )
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
            windows_signed=signing_policy("WINDOWS_SIGNED"),
            macos_signed=signing_policy("MACOS_SIGNED"),
        )
    elif command == "stage":
        stage(repo, Path("release-artifacts"))
    else:
        publish(repo)


if __name__ == "__main__":
    main()
