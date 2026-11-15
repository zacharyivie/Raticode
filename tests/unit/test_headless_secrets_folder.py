"""The headless secrets folder: read-only secrets for servers with no system keychain."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from gofer.classifiers import jev_settings
from gofer.classifiers.jev import JevError
from gofer.core import secrets_folder
from gofer.core import usage_credentials as credentials
from gofer.ui import headless
from gofer.utils.process import credential_env

FAKE_KEY = "jev-test-key-0123456789"
posix_only = pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")


def put(folder: Path, name: str, value: str, mode: int = 0o600) -> Path:
    path = folder / name
    path.write_text(value, encoding="utf-8")
    path.chmod(mode)
    return path


def no_keychain() -> Any:
    raise RuntimeError("protected_store_unavailable")


class MemoryStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, account: str) -> str | None:
        return self.values.get(account)

    def put(self, account: str, value: str) -> None:
        self.values[account] = value

    def delete(self, account: str) -> None:
        self.values.pop(account, None)


def tree_text(root: Path) -> str:
    """Everything written under [root], to prove a value never lands on disk there."""
    chunks = []
    for path in root.rglob("*"):
        if path.is_file():
            chunks.append(path.read_bytes().decode("utf-8", errors="replace"))
    return "\n".join(chunks)


# The folder and its files ------------------------------------------------------------


def test_nothing_is_read_without_a_configured_folder(tmp_path, monkeypatch) -> None:
    folder = tmp_path / "creds"
    folder.mkdir(mode=0o700)
    put(folder, "jev-key", FAKE_KEY)
    # systemd's variable only names a folder; reads never consult the environment.
    monkeypatch.setenv(secrets_folder.CREDENTIALS_DIRECTORY_ENV, str(folder))
    monkeypatch.setenv("jev-key", "from-the-environment")
    assert secrets_folder.configured_secrets_dir() is None
    assert secrets_folder.read_folder_secret("jev-key") is None


def test_option_wins_over_credentials_directory(tmp_path) -> None:
    option = tmp_path / "option"
    env = {secrets_folder.CREDENTIALS_DIRECTORY_ENV: str(tmp_path / "systemd")}
    assert secrets_folder.resolve_secrets_dir(option, env) == option
    assert secrets_folder.resolve_secrets_dir(None, env) == tmp_path / "systemd"
    assert secrets_folder.resolve_secrets_dir(None, {}) is None
    assert secrets_folder.resolve_secrets_dir(None, {"CREDENTIALS_DIRECTORY": " "}) is None


@posix_only
def test_files_follow_the_token_file_rule(secrets_dir: Path) -> None:
    put(secrets_dir, "good", f"  {FAKE_KEY}\n")
    put(secrets_dir, "read-only", FAKE_KEY, mode=0o400)
    put(secrets_dir, "readable", FAKE_KEY, mode=0o640)
    put(secrets_dir, "writable", FAKE_KEY, mode=0o620)
    put(secrets_dir, "empty", "\n")
    (secrets_dir / "link").symlink_to(secrets_dir / "good")
    (secrets_dir / "folder").mkdir()
    put(secrets_dir, "huge", "x" * (secrets_folder.MAX_SECRET_BYTES + 1))

    assert secrets_folder.read_folder_secret("good") == FAKE_KEY
    assert secrets_folder.read_folder_secret("read-only") == FAKE_KEY
    assert secrets_folder.read_folder_secret("missing") is None
    assert secrets_folder.read_folder_secret("../escape") is None
    for name, message in (
        ("readable", "chmod 600"),
        ("writable", "chmod 600"),
        ("empty", "empty"),
        ("link", "regular file"),
        ("folder", "regular file"),
        ("huge", "larger than"),
    ):
        with pytest.raises(secrets_folder.SecretsFolderError, match=message) as error:
            secrets_folder.read_folder_secret(name)
        assert FAKE_KEY not in str(error.value)


@posix_only
def test_file_owned_by_another_account_is_refused(secrets_dir, monkeypatch) -> None:
    put(secrets_dir, "jev-key", FAKE_KEY)
    monkeypatch.setattr(os, "getuid", lambda: os.stat(secrets_dir).st_uid + 1)
    with pytest.raises(secrets_folder.SecretsFolderError, match="another account"):
        secrets_folder.read_folder_secret("jev-key")


@posix_only
def test_folder_checks(tmp_path: Path) -> None:
    with pytest.raises(secrets_folder.SecretsFolderError, match="doesn't exist"):
        secrets_folder.configure_secrets_dir(tmp_path / "missing")
    shared = tmp_path / "shared"
    shared.mkdir()
    shared.chmod(0o777)
    with pytest.raises(secrets_folder.SecretsFolderError, match="chmod go-w"):
        secrets_folder.configure_secrets_dir(shared)
    afile = put(tmp_path, "afile", "x")
    with pytest.raises(secrets_folder.SecretsFolderError, match="isn't a folder"):
        secrets_folder.configure_secrets_dir(afile)
    good = tmp_path / "good"
    good.mkdir(mode=0o700)
    assert secrets_folder.configure_secrets_dir(good) == good
    assert secrets_folder.configured_secrets_dir() == good
    assert secrets_folder.configure_secrets_dir(None) is None


def test_file_names_for_org_and_usage_secrets() -> None:
    assert secrets_folder.org_secret_file("github-hook") == "org-secret-github-hook"
    assert secrets_folder.org_secret_file("org-1/hook/abc-123") == "org-secret-org-1.hook.abc-123"
    # A dot can't appear in an account, so no two accounts share a file.
    assert secrets_folder.org_secret_file("a.b") is None
    assert secrets_folder.org_secret_file("../etc") is None
    assert secrets_folder.org_secret_file("") is None
    assert (
        secrets_folder.usage_secret_file("openai_api", None, "admin_api_key")
        == "usage-openai_api-admin_api_key"
    )
    assert secrets_folder.usage_secret_file("cursor", "work", "email") == "usage-cursor-work-email"
    assert secrets_folder.usage_secret_file("cursor", "bad/profile", "email") is None


def test_keychain_then_folder_order(secrets_dir: Path) -> None:
    put(secrets_dir, "org-secret-hook", "from-folder")
    assert secrets_folder.keychain_then_folder(lambda: "from-keychain", "org-secret-hook") == (
        "from-keychain"
    )
    assert secrets_folder.keychain_then_folder(lambda: None, "org-secret-hook") == "from-folder"
    assert secrets_folder.keychain_then_folder(no_keychain, "org-secret-hook") == "from-folder"
    assert secrets_folder.keychain_then_folder(lambda: None, "org-secret-other") is None
    with pytest.raises(RuntimeError, match="protected_store_unavailable"):
        secrets_folder.keychain_then_folder(no_keychain, "org-secret-other")


# Jev ---------------------------------------------------------------------------------


@pytest.fixture
def jev_no_keychain(monkeypatch) -> None:
    monkeypatch.setattr(jev_settings, "_secret_store", no_keychain)
    jev_settings._cache.clear()


def test_jev_key_comes_from_the_folder_without_a_keychain(
    tmp_path, secrets_dir, jev_no_keychain
) -> None:
    data_dir = tmp_path / "data"
    assert jev_settings.read_jev_key(data_dir) is None
    assert not jev_settings.jev_key_configured(data_dir)

    put(secrets_dir, "jev-key", FAKE_KEY + "\n")
    assert jev_settings.jev_key_configured(data_dir)
    assert jev_settings.read_jev_key(data_dir) == FAKE_KEY
    status = jev_settings.jev_status(data_dir)
    assert status["configured"] is True and status["keySource"] == "secrets folder"
    assert FAKE_KEY not in json.dumps(status)

    # A rotated file is read again; nothing caches the old value.
    put(secrets_dir, "jev-key", FAKE_KEY + "-rotated")
    assert jev_settings.read_jev_key(data_dir) == FAKE_KEY + "-rotated"

    jev_settings.save_jev_settings({"enabled": False}, data_dir)
    with pytest.raises(JevError, match="turned off"):
        jev_settings.read_jev_key(data_dir)


def test_jev_save_still_needs_the_keychain_and_writes_no_plaintext(
    tmp_path, secrets_dir, jev_no_keychain
) -> None:
    data_dir = tmp_path / "data"
    put(secrets_dir, "jev-key", FAKE_KEY)
    before = sorted(p.name for p in secrets_dir.iterdir())
    with pytest.raises(jev_settings.JevSettingsError, match="--secrets-dir") as error:
        jev_settings.save_jev_key("another-key-0123456789", data_dir)
    assert "another-key" not in str(error.value)
    with pytest.raises(jev_settings.JevSettingsError, match="--secrets-dir"):
        jev_settings.remove_jev_key(data_dir)
    assert sorted(p.name for p in secrets_dir.iterdir()) == before
    assert (secrets_dir / "jev-key").read_text() == FAKE_KEY
    assert "another-key" not in tree_text(tmp_path)
    assert FAKE_KEY not in tree_text(data_dir)


def test_jev_keychain_key_wins_and_folder_covers_a_locked_keychain(
    tmp_path, secrets_dir, monkeypatch
) -> None:
    data_dir = tmp_path / "data"
    memory = MemoryStore()
    monkeypatch.setattr(jev_settings, "_secret_store", lambda: memory)
    jev_settings._cache.clear()
    jev_settings.save_jev_key("keychain-key-0123456789", data_dir)
    put(secrets_dir, "jev-key", FAKE_KEY)
    assert jev_settings.jev_status(data_dir)["keySource"] == "keychain"
    assert jev_settings.read_jev_key(data_dir) == "keychain-key-0123456789"

    jev_settings._cache.clear()
    monkeypatch.setattr(jev_settings, "_secret_store", no_keychain)
    assert jev_settings.read_jev_key(data_dir) == FAKE_KEY


def test_jev_names_the_folder_when_nothing_has_a_key(tmp_path, monkeypatch) -> None:
    data_dir = tmp_path / "data"
    memory = MemoryStore()
    monkeypatch.setattr(jev_settings, "_secret_store", lambda: memory)
    jev_settings.save_jev_key(FAKE_KEY, data_dir)
    jev_settings._cache.clear()
    monkeypatch.setattr(jev_settings, "_secret_store", no_keychain)
    with pytest.raises(JevError, match="jev-key"):
        jev_settings.read_jev_key(data_dir)


@posix_only
def test_jev_refuses_an_unsafe_folder_file(tmp_path, secrets_dir, jev_no_keychain) -> None:
    put(secrets_dir, "jev-key", FAKE_KEY, mode=0o644)
    with pytest.raises(JevError, match="chmod 600") as error:
        jev_settings.read_jev_key(tmp_path / "data")
    assert FAKE_KEY not in str(error.value)
    put(secrets_dir, "jev-key", "two words", mode=0o600)
    with pytest.raises(JevError, match="one line with no spaces"):
        jev_settings.read_jev_key(tmp_path / "data")


# Usage reporting credentials ------------------------------------------------------------


def test_usage_credentials_come_from_the_folder(tmp_path, secrets_dir, monkeypatch) -> None:
    monkeypatch.setattr(credentials, "_secret_store", no_keychain)
    data_dir = tmp_path / "data"
    assert credentials.resolve_usage_credentials("cursor", data_dir=data_dir) == {}
    assert credentials.usage_credential_revision("cursor", data_dir=data_dir) == ""

    put(secrets_dir, "usage-cursor-admin_api_key", "private-admin-key")
    put(secrets_dir, "usage-cursor-email", "me@example.com")
    assert credentials.resolve_usage_credentials("cursor", data_dir=data_dir) == {
        "admin_api_key": "private-admin-key",
        "email": "me@example.com",
    }
    revision = credentials.usage_credential_revision("cursor", data_dir=data_dir)
    assert revision.startswith("secrets-folder:")
    assert "private" not in revision

    payload = credentials.usage_credentials_payload("cursor", data_dir=data_dir)
    assert payload["fields"][0]["configured"] is True
    assert payload["storage_available"] is False
    assert "--secrets-dir" in payload["storage_error"]
    assert "private-admin-key" not in json.dumps(payload)

    put(secrets_dir, "usage-cursor-admin_api_key", "private-admin-key-2")
    os.utime(secrets_dir / "usage-cursor-admin_api_key", ns=(1, 1))
    assert credentials.usage_credential_revision("cursor", data_dir=data_dir) != revision

    with pytest.raises(credentials.UsageCredentialError, match="--secrets-dir"):
        credentials.save_usage_credentials(
            "cursor", None, {"email": "new@example.com"}, data_dir=data_dir
        )
    assert "private-admin-key" not in tree_text(data_dir)


def test_usage_save_never_copies_folder_values_into_the_keychain(
    tmp_path, secrets_dir, monkeypatch
) -> None:
    memory = MemoryStore()
    monkeypatch.setattr(credentials, "_secret_store", lambda: memory)
    put(secrets_dir, "usage-cursor-admin_api_key", "private-folder-key")
    credentials.save_usage_credentials(
        "cursor", None, {"email": "me@example.com"}, data_dir=tmp_path / "data"
    )
    assert "private-folder-key" not in json.dumps(memory.values)


# Organizations secrets and webhooks ------------------------------------------------------


def test_org_mcp_secrets_read_the_folder_without_a_keychain(secrets_dir: Path) -> None:
    from gofer.ui.organization_operations import resolve_secrets

    class Locked:
        def get(self, account: str) -> str | None:
            raise RuntimeError("protected_store_unavailable")

    put(secrets_dir, "org-secret-github-token", "folder-github-token")
    org = {"runtime": {"secrets": {"github": {"account": "github-token", "revoked": False}}}}
    employee = {"secretRefs": ["github"]}
    resources = {"mcpServers": [{"env": {"TOKEN": "secret://github"}}]}
    assert resolve_secrets(org, employee, resources, Locked()) == ["folder-github-token"]
    assert resources["mcpServers"][0]["env"]["TOKEN"] == "folder-github-token"

    missing = {"mcpServers": [{"env": {"TOKEN": "secret://other"}}]}
    org["runtime"]["secrets"]["other"] = {"account": "not-in-folder", "revoked": False}
    with pytest.raises(RuntimeError, match="protected_store_unavailable"):
        resolve_secrets(org, {"secretRefs": ["other"]}, missing, Locked())


# Startup log and doctor ------------------------------------------------------------------


def test_prepare_uses_credentials_directory_and_hides_it_from_children(
    tmp_path, monkeypatch
) -> None:
    folder = tmp_path / "creds"
    folder.mkdir(mode=0o700)
    monkeypatch.setenv(secrets_folder.CREDENTIALS_DIRECTORY_ENV, str(folder))
    assert headless.prepare_secrets_dir(None) == folder
    assert secrets_folder.CREDENTIALS_DIRECTORY_ENV not in os.environ
    assert secrets_folder.configured_secrets_dir() == folder
    # Sealed like the desktop's tokens: gone from the environment, still known in-process
    # so the sandbox for unattended Claude runs can hide it.
    assert credential_env(secrets_folder.CREDENTIALS_DIRECTORY_ENV) == str(folder)
    leaked = subprocess.run(
        [sys.executable, "-c", "import os; print(os.environ.get('CREDENTIALS_DIRECTORY', ''))"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert leaked == ""


@posix_only
def test_prepare_refuses_an_unsafe_folder(tmp_path: Path) -> None:
    folder = tmp_path / "shared"
    folder.mkdir()
    folder.chmod(0o777)
    with pytest.raises(headless.HeadlessError, match="chmod go-w"):
        headless.prepare_secrets_dir(folder)


def test_log_lines_list_unavailable_features(secrets_dir: Path) -> None:
    put(secrets_dir, "jev-key", FAKE_KEY)
    lines = headless.secret_feature_log_lines(keychain=False)
    text = "\n".join(line for _warning, line in lines)
    assert FAKE_KEY not in text
    assert f"secrets folder {secrets_dir}" in text
    warnings = [line for warning, line in lines if warning]
    assert any("Unavailable: Usage reporting credentials" in line for line in warnings)
    assert any("Unavailable: Organizations secrets" in line for line in warnings)
    assert any("Unavailable: Phone pairing" in line for line in warnings)
    assert any("Unavailable: Workflow secrets" in line for line in warnings)
    assert not any("Jev" in line for line in warnings)
    assert any("Jev classifier key: secrets folder" in line for _w, line in lines)

    with_keychain = headless.secret_feature_log_lines(keychain=True)
    assert not any(warning for warning, _line in with_keychain)


def test_doctor_lists_secret_backed_features(tmp_path, monkeypatch) -> None:
    from gofer.cli.main import app

    folder = tmp_path / "creds"
    folder.mkdir(mode=0o700)
    put(folder, "jev-key", FAKE_KEY)
    put(folder, "org-secret-github-token", "folder-github-token")
    put(folder, "workflow-secret-DEPLOY_TOKEN", "folder-workflow-token")
    monkeypatch.setattr(secrets_folder, "keychain_available", lambda: False)
    result = CliRunner().invoke(app, ["doctor", "--format", "json", "--secrets-dir", str(folder)])
    assert FAKE_KEY not in result.stdout and "folder-github-token" not in result.stdout
    assert "folder-workflow-token" not in result.stdout
    report = json.loads(result.stdout)
    by_id = {item["id"]: item for item in report["diagnostics"]}
    assert by_id["secrets.folder"]["detail"]["path"] == str(folder)
    assert by_id["secrets.jev"]["severity"] == "ok"
    assert by_id["secrets.jev"]["detail"]["source"] == "secrets folder"
    assert by_id["secrets.organization_secrets"]["severity"] == "ok"
    assert by_id["secrets.workflow_secrets"]["severity"] == "ok"
    assert by_id["secrets.workflow_secrets"]["detail"]["source"] == "secrets folder"
    assert "workflow-secret-DEPLOY_TOKEN" in by_id["secrets.workflow_secrets"]["message"]
    for unavailable in ("usage_credentials", "devices"):
        assert by_id[f"secrets.{unavailable}"]["severity"] == "warning"
    assert "usage-PROVIDER-FIELD" in by_id["secrets.usage_credentials"]["message"]


def test_doctor_finds_the_running_backends_folder(tmp_path, monkeypatch) -> None:
    from gofer.cli.main import app

    data_dir = tmp_path / "data"
    folder = tmp_path / "creds"
    folder.mkdir(mode=0o700)
    headless.ensure_private_dir(headless.headless_dir(data_dir))
    headless.publish_endpoint(
        data_dir, host="127.0.0.1", port=1, token_file=None, secrets_dir=folder
    )
    monkeypatch.setattr(secrets_folder, "keychain_available", lambda: False)
    result = CliRunner().invoke(app, ["doctor", "--format", "json", "--data-dir", str(data_dir)])
    by_id = {item["id"]: item for item in json.loads(result.stdout)["diagnostics"]}
    assert by_id["secrets.folder"]["detail"]["path"] == str(folder)
    assert by_id["secrets.jev"]["severity"] == "warning"
    assert str(folder) in by_id["secrets.jev"]["message"]


def test_doctor_warns_when_the_backends_folder_is_private(tmp_path, monkeypatch) -> None:
    from gofer.cli.main import app

    data_dir = tmp_path / "data"
    headless.ensure_private_dir(headless.headless_dir(data_dir))
    private = tmp_path / "run" / "credentials" / "raticode.service"
    headless.publish_endpoint(
        data_dir, host="127.0.0.1", port=1, token_file=None, secrets_dir=private
    )
    monkeypatch.setattr(secrets_folder, "keychain_available", lambda: False)
    result = CliRunner().invoke(app, ["doctor", "--format", "json", "--data-dir", str(data_dir)])
    by_id = {item["id"]: item for item in json.loads(result.stdout)["diagnostics"]}
    assert by_id["secrets.folder"]["severity"] == "warning"
    assert "Raticode api GET /doctor" in by_id["secrets.folder"]["message"]


def test_doctor_with_a_keychain_reports_one_line(monkeypatch) -> None:
    from gofer.cli.main import app

    monkeypatch.setattr(secrets_folder, "keychain_available", lambda: True)
    result = CliRunner().invoke(app, ["doctor", "--format", "json"])
    ids = [item["id"] for item in json.loads(result.stdout)["diagnostics"]]
    assert [i for i in ids if i.startswith("secrets.")] == ["secrets.keychain"]


def test_doctor_rejects_an_unsafe_folder(tmp_path) -> None:
    from gofer.cli.main import app

    result = CliRunner().invoke(app, ["doctor", "--secrets-dir", str(tmp_path / "missing")])
    assert result.exit_code == 1
    # Rich wraps at 80 columns, so a long temporary path can split the words.
    assert "doesn't exist" in " ".join(result.stdout.split())


def test_bad_secrets_dir_stops_headless_startup_before_writing(tmp_path) -> None:
    import logging

    from gofer.cli.main import app

    data_dir = tmp_path / "data"
    handlers = list(logging.getLogger().handlers)
    result = CliRunner().invoke(
        app,
        [
            "serve",
            "--headless",
            "--port",
            "0",
            "--data-dir",
            str(data_dir),
            "--secrets-dir",
            str(tmp_path / "missing"),
        ],
    )
    assert result.exit_code == 2
    stderr = " ".join(result.stderr.split())
    assert "secrets folder" in stderr and "doesn't exist" in stderr
    assert not (data_dir / "headless" / "owner-token").exists()
    # The backend's logging setup is undone, so no handler keeps writing to a closed stream.
    assert logging.getLogger().handlers == handlers


def test_secrets_dir_needs_headless(tmp_path) -> None:
    from gofer.cli.main import app

    result = CliRunner().invoke(app, ["serve", "--secrets-dir", str(tmp_path)])
    assert result.exit_code == 2
    assert "--secrets-dir need --headless" in result.stderr
