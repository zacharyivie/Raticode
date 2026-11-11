from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
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
        "frontend/release/raticode-cli-linux-x64",
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
        "frontend/release/raticode-cli-macos-*",
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


REVIEWED_ADVISORY = (
    '{"metadata":{"vulnerabilities":{"total":1}},"vulnerabilities":{"minimatch":'
    '{"via":[{"source":1,"name":"minimatch",'
    '"url":"https://github.com/advisories/GHSA-g3hv-75wg-m249"}],'
    '"nodes":["node_modules/minimatch"]}}}'
)
NEW_ADVISORY = REVIEWED_ADVISORY.replace("GHSA-g3hv-75wg-m249", "GHSA-2222-3333-4444")


@pytest.mark.parametrize(
    ("report", "audit_status", "expected_status"),
    [
        ('{"metadata":{"vulnerabilities":{"total":0}},"vulnerabilities":{}}', 0, 0),
        (REVIEWED_ADVISORY, 1, 0),
        (NEW_ADVISORY, 1, 1),
        ('{"error":{"code":"ENOTFOUND"}}', 1, 1),
    ],
    ids=["clean", "reviewed-build-tool", "new-advisory", "audit-error"],
)
def test_npm_audit_logs_failure_evidence_and_allows_only_reviewed_advisories(
    tmp_path: Path, report: str, audit_status: int, expected_status: int
) -> None:
    if os.name == "nt":
        pytest.skip("Source validation runs in bash on Linux")
    validation = _steps_by_name(_job(_entry_workflow("validate-source.yml"), "validate"))
    command = validation["Audit frontend and shipped Electron dependencies"]["run"]
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (tmp_path / "scripts").mkdir()
    shutil.copy2(REPO_ROOT / "scripts/check-npm-audit.py", tmp_path / "scripts")
    (frontend / "package-lock.json").write_text(
        '{"packages":{"":{},"node_modules/minimatch":{"version":"3.1.5","dev":true}}}'
    )
    shutil.copy2(REPO_ROOT / "frontend/npm-audit-exceptions.json", frontend)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    npm = bin_dir / "npm"
    npm.write_text(f"#!/bin/sh\nprintf '%s\\n' '{report}'\nexit {audit_status}\n")
    npm.chmod(0o755)
    result = subprocess.run(
        ["bash", "-e", "-c", command.replace("python ", f"{shlex.quote(sys.executable)} ")],
        cwd=frontend,
        env={**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"]},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == expected_status, result.stderr
    assert (tmp_path / "audit-evidence/npm-audit.json").read_text().strip() == report
    # A failed audit always prints its report, even when the exceptions cover it.
    assert result.stdout.startswith(report) if audit_status else result.stdout == ""


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
    assert evidence["with"]["name"] == "raticode-audit-evidence"
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
        "Sign Windows backend before packaging",
        "Verify Windows release signatures",
    ):
        assert build[name]["if"] == "inputs.windows_signing && runner.os == 'Windows'"
        assert "continue-on-error" not in build[name]
    for name in ("Prepare macOS signing keychain", "Verify macOS signatures and notarization"):
        assert build[name]["if"] == "inputs.macos_signing && runner.os == 'macOS'"
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
    assert candidate["on"]["push"]["branches"] == ["stage"]
    not_desktop = ["mobile/android/**", ".github/workflows/mobile-*.yml", "website/**"]
    assert candidate["on"]["push"]["paths-ignore"] == not_desktop
    assert validation["on"]["pull_request"]["paths-ignore"] == not_desktop
    assert set(candidate["on"]) == {"push", "workflow_dispatch"}
    assert publish["on"] == {"push": {"tags": ["v*"]}}
    assert _job(candidate, "prepare")["if"] == "github.ref == 'refs/heads/stage'"
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
    assert set(workflow["on"]) == {"workflow_call"}
    inputs = workflow["on"]["workflow_call"]["inputs"]
    assert set(inputs) == {"macos_signing", "windows_signing"}
    for name in inputs:
        assert inputs[name]["default"] is False
        assert inputs[name]["type"] == "boolean"
    assert _job(workflow, "credentials")["environment"] == (
        "${{ (inputs.macos_signing || inputs.windows_signing) "
        "&& 'release-signing' || 'release-unsigned' }}"
    )
    # Linux, and a platform that doesn't sign, never enter the signing environment.
    assert _build_job(workflow)["environment"] == (
        "${{ (matrix.name == 'macos' && inputs.macos_signing || matrix.name == 'windows' "
        "&& inputs.windows_signing) && 'release-signing' || 'release-unsigned' }}"
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


def _run_electron_build(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    step = _steps_by_name(_build_job(_release_workflow()))["Build Electron packages"]
    script = step["run"].replace("${{ matrix.electron_builder_args }}", "--native-test")
    # The fake electron-builder prints its arguments, then the signing settings it sees.
    probe = r"""
    npm() { :; }
    npx() {
      printf '%s\n' "$@"
      for name in CSC_LINK CSC_NAME CSC_KEYCHAIN APPLE_KEYCHAIN APPLE_KEYCHAIN_PROFILE \
          APPLE_ID APPLE_APP_SPECIFIC_PASSWORD APPLE_TEAM_ID CSC_IDENTITY_AUTO_DISCOVERY; do
        [ -z "${!name+x}" ] || printf 'env %s=%s\n' "$name" "${!name}"
      done
    }
    """
    return subprocess.run(
        [shutil.which("bash") or "bash", "-eo", "pipefail", "-c", probe + script],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


MAC_KEYCHAIN_OUTPUTS = {
    "CSC_NAME": "0123456789ABCDEF0123456789ABCDEF01234567",
    "CSC_KEYCHAIN": "/runner/temp/raticode-signing.keychain-db",
    "APPLE_KEYCHAIN": "/runner/temp/raticode-signing.keychain-db",
    "APPLE_KEYCHAIN_PROFILE": "raticode-notary",
}


@pytest.mark.parametrize("runner_os", ["macOS", "Windows", "Linux"])
@pytest.mark.parametrize("macos_signing", [False, True])
@pytest.mark.parametrize("windows_signing", [False, True])
def test_electron_build_obeys_each_platforms_signing_policy(
    runner_os: str, macos_signing: bool, windows_signing: bool
) -> None:
    if os.name == "nt":
        pytest.skip("The packaging step runs in bash")
    signed = {"macOS": macos_signing, "Windows": windows_signing, "Linux": False}[runner_os]
    mac_signed = signed and runner_os == "macOS"
    windows_signed = signed and runner_os == "Windows"
    env = {
        **os.environ,
        "RUNNER_OS": runner_os,
        "MACOS_SIGNING": str(macos_signing).lower(),
        "WINDOWS_SIGNING": str(windows_signing).lower(),
        # The workflow fills these only for the platform that signs.
        "CSC_LINK": "certificate" if windows_signed else "",
        "CSC_KEY_PASSWORD": "password" if windows_signed else "",
        # Even if they leaked into the step's environment, they must never reach the builder.
        "APPLE_ID": "apple",
        "APPLE_APP_SPECIFIC_PASSWORD": "apple-password",
        "APPLE_TEAM_ID": "team",
        **(MAC_KEYCHAIN_OUTPUTS if mac_signed else dict.fromkeys(MAC_KEYCHAIN_OUTPUTS, "")),
    }
    result = _run_electron_build(env)
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    args = [line for line in lines if not line.startswith("env ")]
    seen = dict(line[4:].split("=", 1) for line in lines if line.startswith("env "))
    assert "--publish=never" in args
    assert ("--config.forceCodeSigning=true" in args) == signed
    assert ("--config.forceCodeSigning=false" in args) == (not signed)
    mode = "signed" if signed else "unsigned"
    assert f"--config.extraMetadata.raticodeReleaseSigning={mode}" in args
    assert ("--config.mac.notarize=true" in args) == mac_signed
    assert ("--config.mac.hardenedRuntime=true" in args) == mac_signed
    # The verify step signs the DMG with a timestamp, then notarizes and staples it.
    assert ("--config.dmg.sign=false" in args) == (runner_os == "macOS")
    assert "--config.dmg.sign=true" not in args
    assert ("--config.mac.identity=-" in args) == (runner_os == "macOS" and not signed)
    assert ("Unsigned macOS build" in result.stdout) == (runner_os == "macOS" and not signed)
    assert not {"APPLE_ID", "APPLE_APP_SPECIFIC_PASSWORD", "APPLE_TEAM_ID"} & set(seen)
    assert seen["CSC_IDENTITY_AUTO_DISCOVERY"] == str(signed).lower()
    if mac_signed:
        assert {k: seen[k] for k in MAC_KEYCHAIN_OUTPUTS} == MAC_KEYCHAIN_OUTPUTS
    else:
        assert not set(MAC_KEYCHAIN_OUTPUTS) & set(seen)
    assert ("CSC_LINK" in seen) == windows_signed


def test_signed_mac_build_stops_without_a_prepared_keychain() -> None:
    if os.name == "nt":
        pytest.skip("The packaging step runs in bash")
    env = {
        **os.environ,
        "RUNNER_OS": "macOS",
        "MACOS_SIGNING": "true",
        "WINDOWS_SIGNING": "false",
        **dict.fromkeys(MAC_KEYCHAIN_OUTPUTS, ""),
    }
    result = _run_electron_build(env)
    assert result.returncode != 0
    assert "--publish=never" not in result.stdout
    assert "no prepared signing keychain" in result.stderr


MAC_SECRETS = {
    "MACOS_CERTIFICATE",
    "MACOS_CERTIFICATE_PASSWORD",
    "APPLE_ID",
    "APPLE_APP_SPECIFIC_PASSWORD",
}
WINDOWS_SECRETS = {"WINDOWS_CERTIFICATE", "WINDOWS_CERTIFICATE_PASSWORD"}


@pytest.mark.parametrize("macos_signing", [False, True])
@pytest.mark.parametrize("windows_signing", [False, True])
def test_credential_preflight_reports_each_platforms_missing_names_without_values(
    macos_signing: bool, windows_signing: bool
) -> None:
    step = _steps_by_name(_job(_release_workflow(), "credentials"))[
        "Check all required settings without printing values"
    ]
    assert step["if"] == "inputs.macos_signing || inputs.windows_signing"
    # Exercise the actual embedded Python rather than merely checking YAML strings.
    source = step["run"].split("<<'PYCODE'\n", 1)[1].rsplit("PYCODE", 1)[0]
    env = {k: v for k, v in os.environ.items() if k not in step["env"]}
    env["MACOS_SIGNING"] = str(macos_signing).lower()
    env["WINDOWS_SIGNING"] = str(windows_signing).lower()
    env["APPLE_ID"] = "do-not-print-this"
    result = subprocess.run(
        [sys.executable, "-c", source], env=env, capture_output=True, text=True, check=False
    )
    expected = (MAC_SECRETS if macos_signing else set()) | (
        WINDOWS_SECRETS if windows_signing else set()
    )
    assert "do-not-print-this" not in result.stdout + result.stderr
    assert (result.returncode != 0) == bool(expected - {"APPLE_ID"})
    for name in expected - {"APPLE_ID"}:
        assert name in result.stderr
    for name in (MAC_SECRETS | WINDOWS_SECRETS) - expected:
        assert name not in result.stderr
    if macos_signing:
        # The APPLE_CERTIFICATE_* names and electron-builder's own names are accepted
        # for the certificate and its password.
        assert "MACOS_CERTIFICATE (or APPLE_CERTIFICATE_P12_BASE64 or CSC_LINK)" in result.stderr
        assert (
            "MACOS_CERTIFICATE_PASSWORD (or APPLE_CERTIFICATE_PASSWORD or CSC_KEY_PASSWORD)"
            in result.stderr
        )
    env.update(dict.fromkeys(expected, "configured"))
    assert subprocess.run([sys.executable, "-c", source], env=env, check=False).returncode == 0


def test_mac_secrets_reach_only_the_keychain_step() -> None:
    build = _steps_by_name(_build_job(_release_workflow()))
    prepare = build["Prepare macOS signing keychain"]
    assert prepare["run"] == "uv run --locked python scripts/macos-release-signing.py prepare"
    assert prepare["id"] == "macos-signing"
    expected = {
        "MACOS_CERTIFICATE": (
            "(secrets.MACOS_CERTIFICATE || secrets.APPLE_CERTIFICATE_P12_BASE64"
            " || secrets.CSC_LINK)"
        ),
        "MACOS_CERTIFICATE_PASSWORD": (
            "(secrets.MACOS_CERTIFICATE_PASSWORD || secrets.APPLE_CERTIFICATE_PASSWORD"
            " || secrets.CSC_KEY_PASSWORD)"
        ),
        "APPLE_ID": "(secrets.APPLE_ID || vars.APPLE_ID)",
        "APPLE_APP_SPECIFIC_PASSWORD": "secrets.APPLE_APP_SPECIFIC_PASSWORD",
        "APPLE_TEAM_ID": "(secrets.APPLE_TEAM_ID || vars.APPLE_TEAM_ID)",
        "MACOS_SIGNING_IDENTITY": (
            "(secrets.MACOS_SIGNING_IDENTITY || vars.MACOS_SIGNING_IDENTITY)"
        ),
    }
    assert set(prepare["env"]) == set(expected)
    for name, source in expected.items():
        assert prepare["env"][name] == (
            "${{ inputs.macos_signing && runner.os == 'macOS' && " + source + " || '' }}"
        )
    apple = re.compile(r"secrets\.(MACOS_|APPLE_|CSC_)")
    for name, step in build.items():
        if name != prepare["name"]:
            assert not apple.search(str(step.get("env", {}))), name
    # Pull requests run only source validation, which reads no secrets at all.
    assert "secrets." not in (REPO_ROOT / ".github/workflows/validate-source.yml").read_text()


def test_signing_keychain_outlives_its_users_and_is_always_deleted() -> None:
    build = _steps_by_name(_build_job(_release_workflow()))
    names = list(build)
    delete = build["Delete temporary macOS signing keychain"]
    assert delete["if"] == "always() && inputs.macos_signing && runner.os == 'macOS'"
    assert delete["run"] == "python3 scripts/macos-release-signing.py cleanup"
    users = [n for n, step in build.items() if "steps.macos-signing.outputs" in str(step)]
    assert set(users) >= {
        "Build backend",
        "Build Electron packages",
        "Verify macOS signatures and notarization",
        "Smoke-test final distributions",
    }
    keychain_users = [n for n in users if "outputs.keychain" in str(build[n].get("env"))]
    assert keychain_users == ["Build Electron packages", "Verify macOS signatures and notarization"]
    assert all(names.index(n) < names.index(delete["name"]) for n in users[:-1])
    verify = build["Verify macOS signatures and notarization"]
    assert verify["run"] == "uv run --locked python scripts/macos-release-signing.py verify"
    # Stapling rewrites the DMG; the updater hashes and checksums are checked afterwards.
    for later in (
        "Verify final updater hashes",
        "Smoke-test final distributions",
        "Generate macOS checksums",
        "Upload workflow artifacts",
    ):
        assert names.index(verify["name"]) < names.index(later)
    smoke = build["Smoke-test final distributions"]["env"]
    assert smoke["RATICODE_MACOS_SIGNING"] == (
        "${{ inputs.macos_signing && 'developer-id' || 'unsigned' }}"
    )
    backend = build["Build backend"]["env"]
    assert backend["GOFER_CODESIGN_IDENTITY"] == "${{ steps.macos-signing.outputs.identity }}"
    assert "entitlements.mac.plist" in backend["GOFER_CODESIGN_ENTITLEMENTS"]


def test_candidate_manifest_receives_the_build_policy() -> None:
    workflow = _entry_workflow("release-candidate.yml")
    policy = _job(workflow, "prepare")["with"]
    # No repository variable can turn Mac signing off for this repository's candidates.
    assert policy["macos_signing"] == "${{ github.repository == 'zacharyivie/Raticode' }}"
    assert policy["windows_signing"] == "${{ vars.WINDOWS_RELEASE_SIGNING == 'true' }}"
    step = _steps_by_name(_job(workflow, "stage"))[
        "Create candidate manifest and verify completeness"
    ]
    build = _release_workflow()
    for platform in ("macos", "windows"):
        assert step["env"][f"{platform.upper()}_SIGNED"] == (
            "${{ needs.prepare.outputs." + platform + "_signed }}"
        )
        assert build["on"]["workflow_call"]["outputs"][f"{platform}_signed"]["value"] == (
            "${{ jobs.credentials.outputs." + platform + "_signed }}"
        )
        assert _job(build, "credentials")["outputs"][f"{platform}_signed"] == (
            "${{ steps.policy.outputs." + platform + "_signed }}"
        )
    steps = _steps_by_name(_build_job(build))["Build Electron packages"]["env"]
    assert steps["MACOS_SIGNING"] == "${{ inputs.macos_signing }}"
    assert steps["WINDOWS_SIGNING"] == "${{ inputs.windows_signing }}"


def test_mac_packages_build_the_speech_helper_they_bundle() -> None:
    # electron-builder skips a missing extraResources file, so without this step a Mac
    # release has no dictation helper and silently falls back to the offline model.
    steps = _steps_by_name(_build_job(_release_workflow()))
    names = list(steps)
    helper = steps["Build macOS speech helper"]
    assert helper["if"] == "runner.os == 'macOS'"
    assert helper["run"] == "node scripts/build-macos-speech-helper.cjs --require"
    assert names.index("Build backend") < names.index("Build macOS speech helper")
    assert names.index("Build macOS speech helper") < names.index("Build Electron packages")
