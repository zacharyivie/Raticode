from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "collect_licenses", ROOT / "scripts/collect-licenses.py"
)
assert spec and spec.loader
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)


@pytest.fixture
def license_build(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "LICENSE").write_text("Application license")
    (root / "packaging/licenses").mkdir(parents=True)
    (root / "packaging/licenses/README.md").write_text("Source information")
    (root / "LICENSE.txt").write_text("Python license")
    (root / "uv.lock").write_text(
        "\n".join(
            f'[[package]]\nname = "{name}"\nversion = "1"'
            for name in ("gofer-flow", "pyinstaller", "pyinstaller-hooks-contrib", "example")
        )
    )
    distributions = {}
    for name in ("gofer-flow", "pyinstaller", "pyinstaller-hooks-contrib", "example"):
        location = root / name
        location.mkdir()
        (location / "LICENSE").write_text(f"{name} license")
        distributions[name] = SimpleNamespace(
            metadata={"Name": name, "License-Expression": "MIT"},
            version="1",
            files=[Path("LICENSE")],
            requires=["example"] if name == "gofer-flow" else [],
            locate_file=lambda file, location=location: location / file,
        )
    package = root / "frontend/node_modules/example-js"
    (package / "deps").mkdir(parents=True)
    (package / "package.json").write_text(
        json.dumps(
            {
                "name": "example-js",
                "version": "2",
                "license": "MIT",
            }
        )
    )
    (package / "LICENSE").write_text("JS notice")
    (package / "deps/LICENSES.native.html").write_text("Native notices")
    (root / "frontend/package-lock.json").write_text(
        json.dumps(
            {
                "packages": {
                    "": {"name": "app"},
                    "node_modules/example-js": {"version": "2", "license": "MIT"},
                    "node_modules/platform-only": {"version": "3", "optional": True},
                }
            }
        )
    )
    (root / "packaging/licenses/reviewed.json").write_text(
        json.dumps(
            {
                "python": {name: {"version": "1", "license": "MIT"} for name in distributions},
                "npm": {"example-js@2": {"version": "2", "license": "MIT"}},
            }
        )
    )
    monkeypatch.setattr(collector, "ROOT", root)
    monkeypatch.setattr(collector.sysconfig, "get_path", lambda name: str(root))
    monkeypatch.setattr(collector.metadata, "distribution", distributions.__getitem__)
    monkeypatch.setattr(collector.metadata, "distributions", lambda: distributions.values())
    return root, distributions, tmp_path / "bundle"


def test_bundle_keeps_native_notices_and_exact_versions(license_build):
    root, _, output = license_build
    collector.collect(output)
    inventory = json.loads((output / "inventory.json").read_text())
    assert {(row["name"], row["version"]) for row in inventory} == {
        ("example", "1"),
        ("example-js", "2"),
        ("pyinstaller", "1"),
        ("pyinstaller-hooks-contrib", "1"),
    }
    assert any(path.read_text() == "Native notices" for path in output.rglob("*.html"))
    assert (output / "PYTHON-LICENSE.txt").read_text() == "Python license"
    stale = output / "stale-license"
    stale.touch()
    collector.collect(output)
    assert not stale.exists()


def test_bundle_rejects_python_lock_drift(license_build):
    _, distributions, output = license_build
    distributions["example"].version = "2"
    with pytest.raises(ValueError, match="Python lock mismatch"):
        collector.collect(output)


def test_bundle_rejects_npm_lock_drift(license_build):
    root, _, output = license_build
    manifest = root / "frontend/node_modules/example-js/package.json"
    manifest.write_text('{"name":"example-js","version":"9"}')
    with pytest.raises(ValueError, match="npm lock mismatch"):
        collector.collect(output)


def test_bundle_rejects_missing_notice_instead_of_accepting_a_readme(license_build):
    root, _, output = license_build
    package = root / "frontend/node_modules/example-js"
    (package / "LICENSE").unlink()
    (package / "deps/LICENSES.native.html").unlink()
    (package / "README.md").write_text("Declared MIT, but no terms")
    with pytest.raises(ValueError, match="Missing npm license text"):
        collector.collect(output)


def test_bundle_preserves_full_license_in_python_metadata(license_build):
    _, distributions, output = license_build
    distributions["example"].files = []
    text = "Full license text " * 30
    distributions["example"].metadata["License"] = text
    collector.collect(output)
    assert (output / "python/example-1/LICENSE").read_text() == text


def test_bundle_requires_review_when_license_expression_changes(license_build):
    root, _, output = license_build
    manifest = root / "frontend/node_modules/example-js/package.json"
    manifest.write_text('{"name":"example-js","version":"2","license":"UNKNOWN"}')
    with pytest.raises(ValueError, match="License review required"):
        collector.collect(output)


def test_bundle_rejects_tampered_mpl_source(license_build, monkeypatch):
    import io

    root, distributions, output = license_build
    example = distributions.pop("example")
    example.metadata["Name"] = "certifi"
    distributions["certifi"] = example
    distributions["gofer-flow"].requires = ["certifi"]
    lock = root / "uv.lock"
    lock.write_text(
        lock.read_text().replace('name = "example"', 'name = "certifi"')
        + '\nsdist = { url = "https://example.test/certifi.tar.gz", hash = "sha256:bad" }\n'
    )
    policy = root / "packaging/licenses/reviewed.json"
    values = json.loads(policy.read_text())
    values["python"]["certifi"] = values["python"].pop("example")
    policy.write_text(json.dumps(values))
    monkeypatch.setattr(
        collector.urllib.request, "urlopen", lambda *args, **kwargs: io.BytesIO(b"tampered")
    )
    with pytest.raises(ValueError, match="Source hash mismatch"):
        collector.collect(output)


def test_cli_exports_embedded_notices_without_overwriting(tmp_path, monkeypatch):
    import sys

    from typer.testing import CliRunner

    from gofer.cli.main import app

    bundle = tmp_path / "frozen/third-party-licenses"
    bundle.mkdir(parents=True)
    (bundle / "inventory.json").write_text('[{"name":"example","version":"1"}]')
    (bundle / "LICENSE").write_text("License terms")
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle.parent), raising=False)
    target = tmp_path / "exported"
    runner = CliRunner()
    result = runner.invoke(app, ["licenses", "--output", str(target)])
    assert result.exit_code == 0, result.output
    assert (target / "LICENSE").read_text() == "License terms"
    (target / "LICENSE").write_text("Existing user file")
    result = runner.invoke(app, ["licenses", "--output", str(target)])
    assert result.exit_code == 1
    assert (target / "LICENSE").read_text() == "Existing user file"


def test_cli_reports_missing_bundle(tmp_path, monkeypatch):
    import sys

    from typer.testing import CliRunner

    from gofer.cli.main import app

    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    result = CliRunner().invoke(app, ["licenses", "--output", str(tmp_path / "export")])
    assert result.exit_code == 1
    assert "License bundle is missing" in result.output


def test_bundle_handles_platform_specific_locked_versions(license_build):
    root, distributions, output = license_build
    with (root / "uv.lock").open("a") as lock:
        lock.write('\n[[package]]\nname = "example"\nversion = "2"\n')
    # A later platform variant must not mask the installed version.
    collector.collect(output)
    distributions["example"].version = "2"
    policy_path = root / "packaging/licenses/reviewed.json"
    policy = json.loads(policy_path.read_text())
    policy["python"]["example@2"] = {"version": "2", "license": "MIT"}
    policy_path.write_text(json.dumps(policy))
    collector.collect(output)
    assert (output / "python/example-2").is_dir()
