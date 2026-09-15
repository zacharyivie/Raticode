from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _parse_scalar(value: str) -> Any:
    value = value.split(" # ", 1)[0].strip()
    if value == "":
        return ""
    if value in {"true", "false"}:
        return value == "true"
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    return value


def _next_meaningful_line(lines: list[str], start: int) -> str | None:
    for line in lines[start:]:
        if line.strip() and not line.lstrip().startswith("#"):
            return line
    return None


def _parse_workflow_yaml(path: Path) -> dict[str, Any]:
    lines = path.read_text(encoding="utf8").splitlines()
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any] | list[Any]]] = [(-1, root)]
    index = 0

    while index < len(lines):
        raw_line = lines[index]
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            index += 1
            continue

        indent = len(raw_line) - len(raw_line.lstrip(" "))
        while stack and indent <= stack[-1][0]:
            stack.pop()
        container = stack[-1][1]

        if stripped.startswith("- "):
            assert isinstance(container, list)
            item_text = stripped[2:]
            if ": " in item_text or item_text.endswith(":"):
                key, separator, value = item_text.partition(":")
                item: dict[str, Any] = {}
                container.append(item)
                if separator and value.strip():
                    item[key] = _parse_scalar(value)
                else:
                    next_line = _next_meaningful_line(lines, index + 1)
                    child: dict[str, Any] | list[Any]
                    child = [] if next_line and next_line.strip().startswith("- ") else {}
                    item[key] = child
                    stack.append((indent + 2, item))
                    stack.append((indent + 2, child))
                if separator and value.strip():
                    stack.append((indent, item))
            else:
                container.append(_parse_scalar(item_text))
            index += 1
            continue

        assert isinstance(container, dict)
        key, separator, value = stripped.partition(":")
        assert separator
        if value.strip() == "|":
            block_indent: int | None = None
            block_lines: list[str] = []
            index += 1
            while index < len(lines):
                block_line = lines[index]
                if not block_line.strip():
                    next_block_line = _next_meaningful_line(lines, index + 1)
                    if next_block_line is None:
                        break
                    next_indent = len(next_block_line) - len(next_block_line.lstrip(" "))
                    if next_indent <= indent:
                        break
                    block_lines.append("")
                    index += 1
                    continue
                current_indent = len(block_line) - len(block_line.lstrip(" "))
                if current_indent <= indent:
                    break
                if block_indent is None:
                    block_indent = current_indent
                block_lines.append(block_line[block_indent:])
                index += 1
            container[key] = "\n".join(block_lines) + "\n"
            continue
        if value.strip():
            container[key] = _parse_scalar(value)
            index += 1
            continue

        next_line = _next_meaningful_line(lines, index + 1)
        child = [] if next_line and next_line.strip().startswith("- ") else {}
        container[key] = child
        stack.append((indent, child))
        index += 1

    return root


def _release_workflow() -> dict[str, Any]:
    return _parse_workflow_yaml(REPO_ROOT / ".github" / "workflows" / "release-build.yml")


def _entry_workflow(name: str) -> dict[str, Any]:
    return _parse_workflow_yaml(REPO_ROOT / ".github" / "workflows" / name)


def _job(workflow: dict[str, Any], name: str) -> dict[str, Any]:
    return cast(dict[str, Any], cast(dict[str, Any], workflow["jobs"])[name])


def _build_job(workflow: dict[str, Any]) -> dict[str, Any]:
    return _job(workflow, "build")


def _steps_by_name(build_job: dict[str, Any]) -> dict[str, dict[str, Any]]:
    steps = cast(list[dict[str, Any]], build_job["steps"])
    return {cast(str, step["name"]): step for step in steps}


def _matrix_by_platform(build_job: dict[str, Any]) -> dict[str, dict[str, Any]]:
    strategy = cast(dict[str, Any], build_job["strategy"])
    matrix = cast(dict[str, Any], strategy["matrix"])
    entries = cast(list[dict[str, Any]], matrix["include"])
    return {cast(str, entry["name"]): entry for entry in entries}


def _artifact_globs(entry: dict[str, Any]) -> list[str]:
    return cast(str, entry["artifact-glob"]).splitlines()


def _checksum_inputs(entry: dict[str, Any]) -> list[str]:
    return [
        pattern.removeprefix("frontend/release/")
        for pattern in _artifact_globs(entry)
        if not pattern.startswith("frontend/release/checksums-")
    ]


def _bash_checksum_patterns(run: str) -> set[str]:
    match = re.search(r"for pattern in (?P<patterns>.*?); do", run)
    assert match is not None
    return set(match.group("patterns").split())


def _powershell_checksum_patterns(run: str) -> set[str]:
    return set(re.findall(r'\$_.Name -(?:like|eq) "([^"]+)"', run))


def test_release_workflow_uploads_expected_artifacts_and_checksums() -> None:
    workflow = _release_workflow()
    build_job = _build_job(workflow)
    matrix = _matrix_by_platform(build_job)
    steps = _steps_by_name(build_job)

    assert _artifact_globs(matrix["linux"]) == [
        "frontend/release/*.AppImage",
        "frontend/release/*.AppImage.blockmap",
        "frontend/release/*.deb",
        "frontend/release/*.rpm",
        "frontend/release/latest-linux.yml",
        "frontend/release/gof-linux-x64",
        "frontend/release/checksums-linux.txt",
        "frontend/release/package-tests-linux.json",
    ]
    assert _artifact_globs(matrix["windows"]) == [
        "frontend/release/*.exe",
        "frontend/release/*.exe.blockmap",
        "frontend/release/latest.yml",
        "frontend/release/checksums-windows.txt",
        "frontend/release/package-tests-windows.json",
        "frontend/release/signatures-windows.json",
    ]
    assert _artifact_globs(matrix["macos"]) == [
        "frontend/release/*.dmg",
        "frontend/release/*.dmg.blockmap",
        "frontend/release/*.zip",
        "frontend/release/*.zip.blockmap",
        "frontend/release/latest-mac.yml",
        "frontend/release/gof-macos-*",
        "frontend/release/checksums-macos.txt",
        "frontend/release/package-tests-macos.json",
        "frontend/release/notarization-macos-*.json",
    ]

    for platform, step_name in (
        ("linux", "Generate Linux checksums"),
        ("macos", "Generate macOS checksums"),
    ):
        checksum_run = cast(str, steps[step_name]["run"])
        assert _bash_checksum_patterns(checksum_run) == set(_checksum_inputs(matrix[platform]))
        assert f"checksums-{platform}.txt" in checksum_run

    windows_checksum_run = cast(str, steps["Generate Windows checksums"]["run"])
    assert _powershell_checksum_patterns(windows_checksum_run) == set(
        _checksum_inputs(matrix["windows"])
    )
    assert "checksums-windows.txt" in windows_checksum_run


def test_release_security_gates_are_mandatory_and_publish_provenance() -> None:
    workflow = _release_workflow()
    validation = _steps_by_name(_job(_entry_workflow("validate-source.yml"), "validate"))
    expected = {
        "Install locked Python dependencies": (
            "uv sync --locked --extra dev --extra xlsx --group dev"
        ),
        "Lint Python": "uv run --locked ruff check src tests",
        "Type-check Python": "uv run --locked mypy src tests",
        "Test Python": "uv run --locked pytest",
        "Audit all locked Python platform dependencies": (
            "uv run --locked python scripts/audit-dependencies.py"
        ),
    }
    for name, command in expected.items():
        assert validation[name]["run"] == command
        assert "continue-on-error" not in validation[name]
        assert "if" not in validation[name]
    npm_audit = validation["Audit frontend and shipped Electron dependencies"]
    assert "npm audit --include=peer --audit-level=low --json" in npm_audit["run"]
    assert "--omit=dev" not in npm_audit["run"]
    assert "continue-on-error" not in npm_audit
    evidence = validation["Save dependency audit evidence"]
    assert evidence["if"] == "always() && hashFiles('audit-evidence/*') != ''"
    assert evidence["with"]["name"] == "gofer-flow-audit-evidence"
    assert evidence["with"]["path"] == "audit-evidence/*"
    assert evidence["with"]["if-no-files-found"] == "error"
    build = _steps_by_name(_build_job(workflow))
    assert "--locked" in build["Install Python dependencies"]["run"]
    filesystem_tests = build["Test platform filesystem security"]
    assert "test_native_filesystem_security.py" in filesystem_tests["run"]
    native_tests = build["Test native terminal and Electron security on packaged runtime version"]
    assert native_tests["run"] == "npm run test:platform"
    assert "if" not in native_tests
    assert "continue-on-error" not in native_tests
    names = list(build)
    assert names.index("Build Electron packages") < names.index(native_tests["name"])
    assert names.index(native_tests["name"]) < names.index("Upload workflow artifacts")
    assert "--config.forceCodeSigning=true" in build["Build Electron packages"]["run"]
    for name in (
        "Require Windows signing credentials",
        "Prepare macOS signing keychain",
        "Sign Windows backend before packaging",
        "Verify Windows release signatures",
        "Verify macOS signatures and notarization",
    ):
        assert "inputs.signed_release && runner.os" in build[name]["if"]
        assert "continue-on-error" not in build[name]
    assert "env" not in build["Install frontend dependencies"]
    assert "env" not in _build_job(workflow)
    publish = _steps_by_name(_job(_entry_workflow("release-candidate.yml"), "stage"))
    provenance = publish["Attest all desktop, CLI, checksum and audit artifacts"]
    assert re.fullmatch(r"actions/attest-build-provenance@[0-9a-f]{40}", provenance["uses"])
    assert provenance["with"]["subject-path"] == "release-artifacts/*"


def test_all_external_workflow_actions_are_immutable_and_have_update_configuration() -> None:
    for workflow in (REPO_ROOT / ".github/workflows").glob("*.yml"):
        for action in re.findall(r"uses: ([^\s#]+)", workflow.read_text()):
            if not action.startswith("./"):
                assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", action), action
    updates = (REPO_ROOT / ".github/dependabot.yml").read_text()
    assert "package-ecosystem: github-actions" in updates


def test_validation_and_platform_builds_checkout_the_same_event_commit() -> None:
    for workflow, job_name in (("validate-source.yml", "validate"), ("release-build.yml", "build")):
        checkout = _steps_by_name(_job(_entry_workflow(workflow), job_name))["Check out repository"]
        assert checkout["with"]["ref"] == "${{ github.sha }}"


def test_triggers_separate_validation_preparation_and_publication() -> None:
    validation = _entry_workflow("validate-source.yml")
    candidate = _entry_workflow("release-candidate.yml")
    publish = _entry_workflow("release.yml")
    assert set(validation["on"]) == {"pull_request", "workflow_call"}
    assert candidate["on"]["push"]["branches"] == ["main"]
    assert set(candidate["on"]) == {"push", "workflow_dispatch"}
    assert publish["on"] == {"push": {"tags": ["v*"]}}
    assert _job(candidate, "prepare")["if"] == "github.ref == 'refs/heads/main'"
    assert _job(candidate, "prepare")["secrets"] == "inherit"
    assert _job(candidate, "stage")["needs"] == "prepare"
    assert set(publish["jobs"]) == {"publish"}
    assert publish["concurrency"]["cancel-in-progress"] is False
    steps = _steps_by_name(_job(publish, "publish"))
    assert steps["Verify candidate and publish existing draft"]["run"] == (
        "python scripts/release-candidate.py publish"
    )
    assert not any("build" in s.get("run", "") for s in steps.values())
    assert not (REPO_ROOT / ".github/workflows/release-dry-run.yml").exists()
    assert not (REPO_ROOT / ".github/workflows/frontend-checks.yml").exists()


def test_signing_defaults_to_unsigned_and_environment_settings_are_scoped() -> None:
    workflow = _release_workflow()
    assert workflow["on"]["workflow_call"]["inputs"]["signed_release"]["default"] is False
    assert workflow["on"]["workflow_call"]["inputs"]["signed_release"]["type"] == "boolean"
    for name in ("build", "credentials"):
        assert (
            _job(workflow, name)["environment"]
            == "${{ inputs.signed_release && 'release-signing' || 'release-unsigned' }}"
        )
    build = _build_job(workflow)
    assert build["needs"] == "[validate, credentials]"
    matrix = _matrix_by_platform(build)
    assert {k: v["os"] for k, v in matrix.items()} == {
        "linux": "ubuntu-24.04",
        "windows": "windows-2022",
        "macos": "macos-15",
    }
    assert matrix["windows"]["electron_builder_args"].endswith("--x64")
    assert matrix["macos"]["electron_builder_args"].endswith("--arm64")
    steps = _steps_by_name(build)
    names = list(steps)
    assert steps["Smoke-test final distributions"]["run"] == (
        "uv run --locked python scripts/test-release-packages.py"
    )
    for test in ("Verify final updater hashes", "Smoke-test final distributions"):
        assert "if" not in steps[test]
        assert names.index("Verify macOS signatures and notarization") < names.index(test)
        assert names.index(test) < names.index("Generate Linux checksums")
    candidate = _entry_workflow("release-candidate.yml")
    assert _job(candidate, "stage")["permissions"]["attestations"] == "write"
    assert _job(_entry_workflow("release.yml"), "publish")["permissions"]["attestations"] == "read"


@pytest.mark.parametrize("runner_os", ["macOS", "Windows", "Linux"])
@pytest.mark.parametrize("signed", [False, True])
def test_electron_build_obeys_signing_policy(runner_os: str, signed: bool) -> None:
    step = _steps_by_name(_build_job(_release_workflow()))["Build Electron packages"]
    script = step["run"].replace("${{ matrix.electron_builder_args }}", "--native-test")
    env = {
        **os.environ,
        "RUNNER_OS": runner_os,
        "SIGNED_RELEASE": str(signed).lower(),
        "CSC_LINK": "certificate",
        "CSC_KEY_PASSWORD": "password",
        "APPLE_ID": "apple",
        "APPLE_APP_SPECIFIC_PASSWORD": "apple-password",
        "APPLE_TEAM_ID": "team",
    }
    probe = r"""
    npm() { :; }
    npx() {
      if [ "$SIGNED_RELEASE" != "true" ] || [ "$RUNNER_OS" = "Linux" ]; then
        [ -z "${CSC_LINK+x}" ] && [ -z "${APPLE_ID+x}" ] || return 1
      else
        [ "$CSC_LINK" = "certificate" ] || return 1
      fi
      printf '%s\n' "$@"
    }
    """
    result = subprocess.run(
        [shutil.which("bash") or "bash", "-eo", "pipefail", "-c", probe + script],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    args = result.stdout.splitlines()
    assert "--publish=never" in args
    assert ("--config.forceCodeSigning=true" in args) == (signed and runner_os != "Linux")
    assert ("--config.mac.notarize=true" in args) == (signed and runner_os == "macOS")
    assert ("--config.dmg.sign=true" in args) == (signed and runner_os == "macOS")
    assert ("--config.mac.identity=-" in args) == (not signed and runner_os == "macOS")
    assert ("--config.mac.notarize=false" in args) == (not signed and runner_os == "macOS")
    mode = "signed" if signed and runner_os != "Linux" else "unsigned"
    assert f"--config.extraMetadata.raticodeReleaseSigning={mode}" in args


def test_credential_preflight_reports_all_missing_names_without_values() -> None:
    step = _steps_by_name(_job(_release_workflow(), "credentials"))[
        "Check all required settings without printing values"
    ]
    assert step["if"] == "inputs.signed_release"
    # Exercise the actual embedded Python rather than merely checking YAML strings.
    source = step["run"].split("<<'PYCODE'\n", 1)[1].rsplit("PYCODE", 1)[0]
    env = {k: v for k, v in os.environ.items() if k not in step["env"]}
    env["WINDOWS_CERTIFICATE"] = "do-not-print-this"
    result = subprocess.run(
        [os.sys.executable, "-c", source],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "do-not-print-this" not in result.stderr
    for name in set(step["env"]) - {"WINDOWS_CERTIFICATE"}:
        assert name in result.stderr
    env.update(dict.fromkeys(step["env"], "configured"))
    assert subprocess.run([os.sys.executable, "-c", source], env=env).returncode == 0


def test_candidate_manifest_receives_the_build_policy() -> None:
    workflow = _entry_workflow("release-candidate.yml")
    assert (
        _job(workflow, "prepare")["with"]["signed_release"]
        == "${{ vars.RELEASE_SIGNING == 'true' }}"
    )
    step = _steps_by_name(_job(workflow, "stage"))[
        "Create candidate manifest and verify completeness"
    ]
    assert step["env"]["SIGNED_RELEASE"] == "${{ needs.prepare.outputs.signed_release }}"
    build = _release_workflow()
    assert (
        build["on"]["workflow_call"]["outputs"]["signed_release"]["value"]
        == "${{ jobs.credentials.outputs.signed_release }}"
    )
    assert (
        _job(build, "credentials")["outputs"]["signed_release"]
        == "${{ steps.policy.outputs.signed_release }}"
    )
    steps = _steps_by_name(_build_job(build))
    assert (
        steps["Build Electron packages"]["env"]["SIGNED_RELEASE"] == "${{ inputs.signed_release }}"
    )
    assert "inputs.signed_release" in steps["Build backend"]["env"]["GOFER_CODESIGN_IDENTITY"]
