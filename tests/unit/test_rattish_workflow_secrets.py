"""Workflow secrets in the system keychain: store, runtime, preflight, routes and CLI.

The keychain is always replaced with an in-memory store, and no value appears in any
response, index file, run record or command output.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, cast

import pytest

from gofer.rattish import workflow_secrets
from gofer.rattish.preflight import run_preflight
from gofer.rattish.run_service import run_rattish_file
from gofer.rattish.workflow_runtime import execute_workflow
from tests.unit.test_rattish_hardening import compile_source

VALUE = "fake-workflow-secret-value"
SOURCE = """Rattish: 1
Workflow:
  name: secret user
Node inspect:
  type: bash-command
  command: printf '%s' "$TOKEN"
  with:
    token: secret["DEPLOY_TOKEN"]
"""


class MemoryStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.fail = False
        self.reads = 0

    def get(self, account: str) -> str | None:
        self.reads += 1
        if self.fail:
            raise RuntimeError("locked")
        return self.values.get(account)

    def put(self, account: str, value: str) -> None:
        if self.fail:
            raise RuntimeError("locked")
        self.values[account] = value

    def delete(self, account: str) -> None:
        if self.fail:
            raise RuntimeError("locked")
        self.values.pop(account, None)


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> MemoryStore:
    memory = MemoryStore()
    monkeypatch.setattr(workflow_secrets, "_secret_store", lambda: memory)
    workflow_secrets._cache.clear()
    monkeypatch.delenv("DEPLOY_TOKEN", raising=False)
    return memory


# Store ------------------------------------------------------------------------------


def test_values_live_in_the_keychain_and_the_index_holds_names_only(
    tmp_path: Path, store: MemoryStore
) -> None:
    entry = workflow_secrets.save_workflow_secret("DEPLOY_TOKEN", VALUE, tmp_path)

    assert entry["name"] == "DEPLOY_TOKEN" and entry["updatedAt"]
    assert VALUE not in json.dumps(entry)
    ((account, raw),) = store.values.items()
    assert account.endswith(":workflow:DEPLOY_TOKEN")
    assert json.loads(raw)["value"] == VALUE
    index = tmp_path / workflow_secrets.INDEX_FILE
    assert VALUE not in index.read_text()
    assert index.stat().st_mode & 0o077 == 0
    listed = workflow_secrets.list_workflow_secrets(tmp_path)
    assert [item["name"] for item in listed] == ["DEPLOY_TOKEN"]
    assert VALUE not in json.dumps(listed)
    assert workflow_secrets.workflow_secret_value("DEPLOY_TOKEN", tmp_path) == VALUE

    workflow_secrets.remove_workflow_secret("DEPLOY_TOKEN", tmp_path)
    assert store.values == {}
    assert workflow_secrets.list_workflow_secrets(tmp_path) == []
    assert workflow_secrets.workflow_secret_value("DEPLOY_TOKEN", tmp_path) is None


def test_each_data_folder_has_its_own_secrets(tmp_path: Path, store: MemoryStore) -> None:
    workflow_secrets.save_workflow_secret("DEPLOY_TOKEN", "one", tmp_path / "a")
    workflow_secrets.save_workflow_secret("DEPLOY_TOKEN", "two", tmp_path / "b")
    assert workflow_secrets.workflow_secret_value("DEPLOY_TOKEN", tmp_path / "a") == "one"
    assert workflow_secrets.workflow_secret_value("DEPLOY_TOKEN", tmp_path / "b") == "two"


def test_the_keychain_wins_over_the_environment_and_the_environment_still_works(
    tmp_path: Path, store: MemoryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEPLOY_TOKEN", "from-environment")
    assert workflow_secrets.workflow_secret_value("DEPLOY_TOKEN", tmp_path) == "from-environment"
    assert workflow_secrets.secret_status("DEPLOY_TOKEN", tmp_path) == "environment"

    workflow_secrets.save_workflow_secret("DEPLOY_TOKEN", VALUE, tmp_path)
    assert workflow_secrets.workflow_secret_value("DEPLOY_TOKEN", tmp_path) == VALUE
    assert workflow_secrets.secret_status("DEPLOY_TOKEN", tmp_path) == "saved"
    assert workflow_secrets.list_workflow_secrets(tmp_path)[0]["inEnvironment"] is True


def test_unsaved_names_never_touch_the_keychain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_keychain() -> Any:
        raise AssertionError("the keychain was opened for a name nobody saved")

    monkeypatch.setattr(workflow_secrets, "_secret_store", no_keychain)
    monkeypatch.setenv("ONLY_IN_ENV", "x")
    assert workflow_secrets.workflow_secret_value("ONLY_IN_ENV", tmp_path) == "x"
    assert workflow_secrets.workflow_secret_value("NOWHERE", tmp_path) is None


def test_saved_values_are_read_from_the_keychain_once_per_revision(
    tmp_path: Path, store: MemoryStore
) -> None:
    workflow_secrets.save_workflow_secret("DEPLOY_TOKEN", "first", tmp_path)
    for _ in range(3):
        assert workflow_secrets.workflow_secret_value("DEPLOY_TOKEN", tmp_path) == "first"
    assert store.reads == 1
    workflow_secrets.save_workflow_secret("DEPLOY_TOKEN", "second", tmp_path)
    assert workflow_secrets.workflow_secret_value("DEPLOY_TOKEN", tmp_path) == "second"


def test_a_locked_keychain_falls_back_to_the_environment_or_says_so(
    tmp_path: Path, store: MemoryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow_secrets.save_workflow_secret("DEPLOY_TOKEN", VALUE, tmp_path)
    workflow_secrets._cache.clear()
    store.fail = True
    with pytest.raises(workflow_secrets.WorkflowSecretError) as caught:
        workflow_secrets.workflow_secret_value("DEPLOY_TOKEN", tmp_path)
    assert "keychain" in str(caught.value) and VALUE not in str(caught.value)
    monkeypatch.setenv("DEPLOY_TOKEN", "from-environment")
    assert workflow_secrets.workflow_secret_value("DEPLOY_TOKEN", tmp_path) == "from-environment"


@pytest.mark.parametrize("name", ["", "1ABC", "has space", "a/b", "x" * 129, None, 3])
def test_bad_names_are_refused(tmp_path: Path, store: MemoryStore, name: Any) -> None:
    with pytest.raises(workflow_secrets.WorkflowSecretError):
        workflow_secrets.save_workflow_secret(name, VALUE, tmp_path)
    assert store.values == {}


@pytest.mark.parametrize("value", ["", "   ", None, "a\x00b", "x" * (16 * 1024 + 1)])
def test_bad_values_are_refused(tmp_path: Path, store: MemoryStore, value: Any) -> None:
    with pytest.raises(workflow_secrets.WorkflowSecretError):
        workflow_secrets.save_workflow_secret("DEPLOY_TOKEN", value, tmp_path)
    assert store.values == {}


def test_multiline_values_are_kept(tmp_path: Path, store: MemoryStore) -> None:
    pem = "-----BEGIN KEY-----\nabc\n-----END KEY-----"
    workflow_secrets.save_workflow_secret("SIGNING_KEY", pem, tmp_path)
    assert workflow_secrets.workflow_secret_value("SIGNING_KEY", tmp_path) == pem


def test_store_errors_are_safe_and_leave_nothing_saved(tmp_path: Path, store: MemoryStore) -> None:
    store.fail = True
    with pytest.raises(workflow_secrets.WorkflowSecretError) as caught:
        workflow_secrets.save_workflow_secret("DEPLOY_TOKEN", VALUE, tmp_path)
    assert VALUE not in str(caught.value)
    assert workflow_secrets.list_workflow_secrets(tmp_path) == []
    store.fail = False
    with pytest.raises(workflow_secrets.WorkflowSecretError, match="No saved"):
        workflow_secrets.remove_workflow_secret("DEPLOY_TOKEN", tmp_path)


# Runtime and preflight ----------------------------------------------------------------


async def test_runs_read_the_saved_value_and_redact_it_in_run_records(
    tmp_path: Path, store: MemoryStore
) -> None:
    data = tmp_path / "data"
    workflow_secrets.save_workflow_secret("DEPLOY_TOKEN", VALUE, data)
    source = tmp_path / "project" / "workflow.rattish"
    source.parent.mkdir()
    source.write_text(SOURCE, encoding="utf-8")

    result = await run_rattish_file(source, data_dir=data)

    assert result.ok, result.document
    record = result.path.read_text(encoding="utf-8")
    assert VALUE not in record
    assert "[REDACTED]" in record
    assert VALUE not in json.dumps(result.document)


async def test_environment_secrets_keep_working(
    tmp_path: Path, store: MemoryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEPLOY_TOKEN", "from-environment")
    ir = compile_source(SOURCE, tmp_path)
    assert run_preflight(ir, data_dir=tmp_path / "data").ready
    result = await execute_workflow(ir, data_dir=tmp_path / "data")
    assert result.outcome == "pass"
    assert result.latest_node_outputs["inspect"]["stdout"] == "[REDACTED]"


async def test_a_missing_secret_names_itself_and_how_to_add_it(
    tmp_path: Path, store: MemoryStore
) -> None:
    ir = compile_source(SOURCE, tmp_path)
    preflight = run_preflight(ir, data_dir=tmp_path / "data")

    assert not preflight.ready
    (diagnostic,) = [
        item
        for item in preflight.diagnostics
        if item.code == "RATTISH_PREFLIGHT_SECRET_UNAVAILABLE"
    ]
    assert "'DEPLOY_TOKEN'" in diagnostic.message
    assert "Workflow settings > Secrets" in diagnostic.message
    assert "Raticode rattish secrets set DEPLOY_TOKEN" in diagnostic.message
    assert diagnostic.details == {"node": "inspect", "secret": "DEPLOY_TOKEN"}

    result = await execute_workflow(ir, data_dir=tmp_path / "data")
    assert result.outcome == "failure"
    error = result.runs[-1].result.error
    assert error is not None
    assert "DEPLOY_TOKEN" in error.message
    assert "rattish secrets set DEPLOY_TOKEN" in error.message

    workflow_secrets.save_workflow_secret("DEPLOY_TOKEN", VALUE, tmp_path / "data")
    assert run_preflight(ir, data_dir=tmp_path / "data").ready


def test_a_locked_keychain_blocks_preflight_even_with_a_default(
    tmp_path: Path, store: MemoryStore
) -> None:
    source = SOURCE.replace(
        '    token: secret["DEPLOY_TOKEN"]\n',
        '    token:\n      from: secret["DEPLOY_TOKEN"]\n      default: none\n',
    )
    ir = compile_source(source, tmp_path)
    data = tmp_path / "data"
    assert run_preflight(ir, data_dir=data).ready

    workflow_secrets.save_workflow_secret("DEPLOY_TOKEN", VALUE, data)
    workflow_secrets._cache.clear()
    store.fail = True
    preflight = run_preflight(ir, data_dir=data)
    assert not preflight.ready
    assert any(
        item.code == "RATTISH_PREFLIGHT_SECRET_UNAVAILABLE" and "keychain" in item.message
        for item in preflight.diagnostics
    )


# Headless secrets folder ----------------------------------------------------------------

posix_only = pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
FOLDER_VALUE = "fake-folder-secret-value"


def put_secret(folder: Path, name: str, value: str, mode: int = 0o600) -> Path:
    path = folder / f"workflow-secret-{name}"
    path.write_text(value + "\n", encoding="utf-8")
    path.chmod(mode)
    return path


def test_the_secrets_folder_comes_after_the_keychain_and_before_the_environment(
    tmp_path: Path, store: MemoryStore, secrets_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = tmp_path / "data"
    monkeypatch.setenv("DEPLOY_TOKEN", "from-environment")
    assert workflow_secrets.secret_status("DEPLOY_TOKEN", data) == "environment"

    put_secret(secrets_dir, "DEPLOY_TOKEN", FOLDER_VALUE)
    assert workflow_secrets.workflow_secret_value("DEPLOY_TOKEN", data) == FOLDER_VALUE
    assert workflow_secrets.secret_status("DEPLOY_TOKEN", data) == "secrets folder"
    assert store.reads == 0, "an unsaved name never asks the keychain"

    # Read fresh each time, so replacing the file needs no restart.
    put_secret(secrets_dir, "DEPLOY_TOKEN", "rotated-folder-value")
    assert workflow_secrets.workflow_secret_value("DEPLOY_TOKEN", data) == "rotated-folder-value"

    workflow_secrets.save_workflow_secret("DEPLOY_TOKEN", VALUE, data)
    assert workflow_secrets.workflow_secret_value("DEPLOY_TOKEN", data) == VALUE
    assert workflow_secrets.secret_status("DEPLOY_TOKEN", data) == "saved"
    (entry,) = workflow_secrets.list_workflow_secrets(data)
    assert entry["inSecretsFolder"] is True and entry["inEnvironment"] is True

    # A saved value the keychain can't give falls back to the folder file.
    workflow_secrets._cache.clear()
    store.fail = True
    assert workflow_secrets.workflow_secret_value("DEPLOY_TOKEN", data) == "rotated-folder-value"


def test_no_folder_file_means_the_environment_as_before(
    tmp_path: Path, store: MemoryStore, secrets_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    put_secret(secrets_dir, "OTHER", FOLDER_VALUE)
    monkeypatch.setenv("DEPLOY_TOKEN", "from-environment")
    assert workflow_secrets.workflow_secret_value("DEPLOY_TOKEN", tmp_path) == "from-environment"
    monkeypatch.delenv("DEPLOY_TOKEN")
    assert workflow_secrets.workflow_secret_value("DEPLOY_TOKEN", tmp_path) is None


def test_without_a_configured_folder_nothing_is_read_from_it(
    tmp_path: Path, store: MemoryStore
) -> None:
    folder = tmp_path / "secrets"
    folder.mkdir(mode=0o700)
    put_secret(folder, "DEPLOY_TOKEN", FOLDER_VALUE)
    assert workflow_secrets.workflow_secret_value("DEPLOY_TOKEN", tmp_path) is None
    assert workflow_secrets.secret_status("DEPLOY_TOKEN", tmp_path) == "missing"
    assert workflow_secrets.list_folder_workflow_secrets(tmp_path) == []


@posix_only
@pytest.mark.parametrize("kind", ["shared", "symlink", "huge"])
def test_an_unsafe_folder_file_is_refused_not_skipped(
    tmp_path: Path,
    store: MemoryStore,
    secrets_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    from gofer.core.secrets_folder import MAX_SECRET_BYTES

    monkeypatch.setenv("DEPLOY_TOKEN", "from-environment")
    if kind == "shared":
        put_secret(secrets_dir, "DEPLOY_TOKEN", FOLDER_VALUE, mode=0o644)
    elif kind == "symlink":
        target = put_secret(tmp_path, "TARGET", FOLDER_VALUE)
        (secrets_dir / "workflow-secret-DEPLOY_TOKEN").symlink_to(target)
    else:
        put_secret(secrets_dir, "DEPLOY_TOKEN", "x" * (MAX_SECRET_BYTES + 1))

    with pytest.raises(workflow_secrets.WorkflowSecretError) as caught:
        workflow_secrets.workflow_secret_value("DEPLOY_TOKEN", tmp_path)
    assert caught.value.source == "secrets folder"
    assert "workflow-secret-DEPLOY_TOKEN" in str(caught.value)
    assert FOLDER_VALUE not in str(caught.value)

    ir = compile_source(SOURCE, tmp_path)
    preflight = run_preflight(ir, data_dir=tmp_path / "data")
    (diagnostic,) = [
        item
        for item in preflight.diagnostics
        if item.code == "RATTISH_PREFLIGHT_SECRET_UNAVAILABLE"
    ]
    assert diagnostic.details == {
        "node": "inspect",
        "secret": "DEPLOY_TOKEN",
        "source": "secrets folder",
    }
    assert FOLDER_VALUE not in diagnostic.message


async def test_runs_read_the_folder_value_and_redact_it(
    tmp_path: Path, store: MemoryStore, secrets_dir: Path
) -> None:
    put_secret(secrets_dir, "DEPLOY_TOKEN", FOLDER_VALUE)
    ir = compile_source(SOURCE, tmp_path)
    assert run_preflight(ir, data_dir=tmp_path / "data").ready
    result = await execute_workflow(ir, data_dir=tmp_path / "data")
    assert result.outcome == "pass"
    assert result.latest_node_outputs["inspect"]["stdout"] == "[REDACTED]"
    assert FOLDER_VALUE not in json.dumps(result.latest_node_outputs)


def test_a_missing_secret_names_the_folder_file_on_a_headless_backend(
    tmp_path: Path, store: MemoryStore, secrets_dir: Path
) -> None:
    ir = compile_source(SOURCE, tmp_path)
    preflight = run_preflight(ir, data_dir=tmp_path / "data")
    (diagnostic,) = [
        item
        for item in preflight.diagnostics
        if item.code == "RATTISH_PREFLIGHT_SECRET_UNAVAILABLE"
    ]
    assert "in the secrets folder" in diagnostic.message
    assert f"workflow-secret-DEPLOY_TOKEN file in the secrets folder {secrets_dir}" in (
        diagnostic.message
    )
    assert diagnostic.details == {"node": "inspect", "secret": "DEPLOY_TOKEN"}


def test_saving_stays_keychain_only_with_a_secrets_folder(
    tmp_path: Path, store: MemoryStore, secrets_dir: Path
) -> None:
    store.fail = True
    with pytest.raises(workflow_secrets.WorkflowSecretError) as caught:
        workflow_secrets.save_workflow_secret("DEPLOY_TOKEN", VALUE, tmp_path / "data")
    assert "--secrets-dir" in str(caught.value)
    assert "workflow-secret-DEPLOY_TOKEN" in str(caught.value)
    assert list(secrets_dir.iterdir()) == []
    assert VALUE not in str(caught.value)


def test_folder_file_names_follow_secret_names() -> None:
    from gofer.core.secrets_folder import workflow_secret_file

    assert workflow_secret_file("DEPLOY_TOKEN") == "workflow-secret-DEPLOY_TOKEN"
    assert workflow_secret_file("svc.api-key") == "workflow-secret-svc.api-key"
    assert workflow_secret_file("../etc") is None
    assert workflow_secret_file("a/b") is None


def test_settings_payload_shows_the_secrets_folder_source(
    tmp_path: Path, store: MemoryStore, secrets_dir: Path
) -> None:
    from gofer.rattish.workspaces import create_registered_workflow
    from gofer.ui import rattish_secrets

    project = tmp_path / "project"
    project.mkdir()
    workflow = create_registered_workflow(project, "Secret user", registry_dir=tmp_path)
    workflow.entrypoint.write_text(
        SOURCE + 'Node second:\n  type: bash-command\n  command: "true"\n  with:\n'
        '    a: secret["SAVED_TOO"]\n',
        encoding="utf-8",
    )
    put_secret(secrets_dir, "DEPLOY_TOKEN", FOLDER_VALUE)
    put_secret(secrets_dir, "SAVED_TOO", FOLDER_VALUE)
    (secrets_dir / "jev-key").write_text("not a workflow secret")
    workflow_secrets.save_workflow_secret("SAVED_TOO", VALUE, tmp_path)

    payload = rattish_secrets.secrets_payload(tmp_path, workflow.workflow_id)

    assert payload["used"] == [
        {"name": "DEPLOY_TOKEN", "status": "secrets folder"},
        {"name": "SAVED_TOO", "status": "saved"},
    ]
    assert payload["folder"] == [
        {"name": "DEPLOY_TOKEN", "file": "workflow-secret-DEPLOY_TOKEN", "alsoSaved": False},
        {"name": "SAVED_TOO", "file": "workflow-secret-SAVED_TOO", "alsoSaved": True},
    ]
    assert payload["secrets"][0]["inSecretsFolder"] is True
    assert FOLDER_VALUE not in json.dumps(payload) and VALUE not in json.dumps(payload)


# Routes ---------------------------------------------------------------------------------


def test_routes_save_list_and_remove_without_returning_values(
    tmp_path: Path, store: MemoryStore
) -> None:
    from tests.unit.test_ui_server import _request

    assert _request(tmp_path, "GET", "/api/rattish/secrets", authenticated=False).status == 401
    empty = cast(dict[str, Any], _request(tmp_path, "GET", "/api/rattish/secrets").json())
    assert empty == {"secrets": [], "used": None, "folder": []}

    saved = _request(
        tmp_path, "POST", "/api/rattish/secrets", body={"name": "DEPLOY_TOKEN", "value": VALUE}
    )
    assert saved.status == 200, saved.text()
    assert VALUE not in saved.text()
    payload = cast(dict[str, Any], saved.json())
    assert [item["name"] for item in payload["secrets"]] == ["DEPLOY_TOKEN"]
    assert payload["saved"]["name"] == "DEPLOY_TOKEN"
    assert VALUE not in _request(tmp_path, "GET", "/api/rattish/secrets").text()

    bad = _request(
        tmp_path, "POST", "/api/rattish/secrets", body={"name": "bad name", "value": VALUE}
    )
    assert bad.status == 400 and VALUE not in bad.text()
    extra = _request(
        tmp_path, "POST", "/api/rattish/secrets", body={"name": "A", "value": VALUE, "x": 1}
    )
    assert extra.status == 400 and VALUE not in extra.text()
    broken = _request(tmp_path, "POST", "/api/rattish/secrets", body=b'{"value": "' + b"x")
    assert broken.status == 400

    removed = _request(tmp_path, "DELETE", "/api/rattish/secrets/DEPLOY_TOKEN")
    assert removed.status == 200
    assert cast(dict[str, Any], removed.json())["secrets"] == []
    assert store.values == {}
    missing = _request(tmp_path, "DELETE", "/api/rattish/secrets/DEPLOY_TOKEN")
    assert missing.status == 400


def test_routes_report_which_secrets_a_workflow_uses(
    tmp_path: Path, store: MemoryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gofer.rattish.workspaces import create_registered_workflow
    from gofer.ui import rattish_secrets

    project = tmp_path / "project"
    project.mkdir()
    workflow = create_registered_workflow(project, "Secret user", registry_dir=tmp_path)
    workflow.entrypoint.write_text(
        SOURCE
        + 'Node second:\n  type: bash-command\n  command: "true"\n  with:\n'
        + '    a: secret["FROM_ENV"]\n    b: secret["NOT_SET"]\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("FROM_ENV", "x")
    monkeypatch.delenv("NOT_SET", raising=False)
    workflow_secrets.save_workflow_secret("DEPLOY_TOKEN", VALUE, tmp_path)

    payload = rattish_secrets.secrets_payload(tmp_path, workflow.workflow_id)

    assert payload["used"] == [
        {"name": "DEPLOY_TOKEN", "status": "saved"},
        {"name": "FROM_ENV", "status": "environment"},
        {"name": "NOT_SET", "status": "missing"},
    ]
    assert rattish_secrets.secrets_payload(tmp_path, "no-such-workflow")["used"] is None


def test_agent_tokens_can_list_but_not_change_secrets() -> None:
    from gofer.ui.headless import agent_request_refusal

    assert agent_request_refusal("GET", "/api/rattish/secrets") is None
    assert agent_request_refusal("POST", "/api/rattish/secrets")
    assert agent_request_refusal("DELETE", "/api/rattish/secrets/DEPLOY_TOKEN")


# CLI ------------------------------------------------------------------------------------


@pytest.fixture
def user_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    from gofer.cli.client import AGENT_SESSION_ENVS

    for name in AGENT_SESSION_ENVS:
        monkeypatch.delenv(name, raising=False)


def test_cli_sets_lists_and_removes_without_printing_values(
    tmp_path: Path, store: MemoryStore, user_shell: None
) -> None:
    from typer.testing import CliRunner

    from gofer.cli.main import app

    runner = CliRunner()
    data = ["--data-dir", str(tmp_path)]
    saved = runner.invoke(
        app, ["rattish", "secrets", "set", "DEPLOY_TOKEN", "--stdin", *data], input=VALUE + "\n"
    )
    assert saved.exit_code == 0, saved.output
    assert VALUE not in saved.output
    assert 'secret["DEPLOY_TOKEN"]' in saved.output
    assert workflow_secrets.workflow_secret_value("DEPLOY_TOKEN", tmp_path) == VALUE

    listed = runner.invoke(app, ["rattish", "secrets", "list", "--format", "json", *data])
    assert listed.exit_code == 0, listed.output
    body = json.loads(listed.output)
    assert [item["name"] for item in body["secrets"]] == ["DEPLOY_TOKEN"]
    assert VALUE not in listed.output
    text = runner.invoke(app, ["rattish", "secrets", "list", *data])
    assert text.output.startswith("DEPLOY_TOKEN") and VALUE not in text.output

    removed = runner.invoke(app, ["rattish", "secrets", "remove", "DEPLOY_TOKEN", *data])
    assert removed.exit_code == 0, removed.output
    assert store.values == {}
    again = runner.invoke(app, ["rattish", "secrets", "remove", "DEPLOY_TOKEN", *data])
    assert again.exit_code == 1
    bad = runner.invoke(app, ["rattish", "secrets", "set", "bad name", "--stdin", *data], input="x")
    assert bad.exit_code == 2


def test_cli_refuses_to_save_or_remove_inside_agent_turns(
    tmp_path: Path, store: MemoryStore, monkeypatch: pytest.MonkeyPatch, user_shell: None
) -> None:
    from typer.testing import CliRunner

    from gofer.cli.main import app

    runner = CliRunner()
    data = ["--data-dir", str(tmp_path)]
    workflow_secrets.save_workflow_secret("DEPLOY_TOKEN", VALUE, tmp_path)
    monkeypatch.setenv("RATICODE_AGENT_SESSION", "1")

    refused = runner.invoke(
        app, ["rattish", "secrets", "set", "OTHER", "--stdin", *data], input="x"
    )
    assert refused.exit_code == 5
    assert "Only you can" in refused.output
    removal = runner.invoke(app, ["rattish", "secrets", "remove", "DEPLOY_TOKEN", *data])
    assert removal.exit_code == 5
    assert [item["name"] for item in workflow_secrets.list_workflow_secrets(tmp_path)] == [
        "DEPLOY_TOKEN"
    ]
    listed = runner.invoke(app, ["rattish", "secrets", "list", *data])
    assert listed.exit_code == 0 and "DEPLOY_TOKEN" in listed.output


def test_set_never_takes_the_value_as_an_argument() -> None:
    from typer.main import get_command

    from gofer.cli.main import app

    group = get_command(app).commands["rattish"].commands["secrets"]  # type: ignore[attr-defined]
    assert set(group.commands) == {"set", "list", "remove"}
    positional = [p.name for p in group.commands["set"].params if not p.opts[0].startswith("-")]
    assert positional == ["name"]
