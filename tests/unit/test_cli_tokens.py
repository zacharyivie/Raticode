"""Failure paths of `Raticode tokens` (agent tokens).

test_cli_headless.py covers the happy paths against a running backend; this file checks
what each command does when something goes wrong, without starting one.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from gofer.cli import client
from gofer.cli.client import AGENT_SESSION_ENVS
from gofer.cli.commands import tokens
from gofer.ui import headless

runner = CliRunner()


@pytest.fixture(autouse=True)
def _clean_client_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Tests run inside Rem sessions too; each starts as the user's own terminal.
    for name in ("RATICODE_DATA_DIR", "GOFER_DATA_DIR", *AGENT_SESSION_ENVS):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


def _invoke(data_dir: Path, *args: str) -> Any:
    return runner.invoke(tokens.app, [*args, "--data-dir", str(data_dir)])


def _names(data_dir: Path) -> list[str]:
    return [token.name for token in headless.AgentTokenStore(data_dir).list()]


def _create(data_dir: Path, name: str, output: Path, *extra: str) -> Any:
    return _invoke(data_dir, "create", name, "--output", str(output), *extra)


def test_create_refuses_an_existing_output_file(tmp_path: Path, data_dir: Path) -> None:
    output = tmp_path / "ci.token"
    output.write_text("keep me\n", encoding="utf-8")

    result = _create(data_dir, "ci", output)

    assert result.exit_code == 2, result.output
    assert "already exists; choose a new file" in result.stderr
    assert output.read_text(encoding="utf-8") == "keep me\n"
    assert _names(data_dir) == []


def test_create_exits_2_when_the_store_refuses(tmp_path: Path, data_dir: Path) -> None:
    first = _create(data_dir, "ci", tmp_path / "first.token")
    assert first.exit_code == 0, first.output

    bad_name = _create(data_dir, "Not A Name", tmp_path / "bad.token")
    duplicate = _create(data_dir, "ci", tmp_path / "second.token")

    assert bad_name.exit_code == 2, bad_name.output
    assert "Token names use lowercase letters" in bad_name.stderr
    assert duplicate.exit_code == 2, duplicate.output
    assert "exists; revoke it first" in duplicate.stderr
    # Neither refusal wrote a token file or touched the existing token.
    assert not (tmp_path / "bad.token").exists()
    assert not (tmp_path / "second.token").exists()
    assert _names(data_dir) == ["ci"]


def test_create_revokes_the_token_when_its_file_cant_be_written(
    tmp_path: Path, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    written: list[str] = []

    def fail(path: Path, text: str) -> None:
        written.append(text)
        raise OSError("disk full")

    monkeypatch.setattr(headless, "create_private_file", fail)
    output = tmp_path / "ci.token"

    result = _create(data_dir, "ci", output)

    assert result.exit_code == 1, result.output
    assert f"couldn't write {output}: disk full" in result.stderr
    assert written and written[0].startswith(headless.AGENT_TOKEN_PREFIX)
    # A token nobody holds is never left working.
    assert _names(data_dir) == []
    assert headless.AgentTokenStore(data_dir).grant(written[0].strip()) is None
    listed = _invoke(data_dir, "list")
    assert listed.exit_code == 0, listed.output
    assert listed.stdout == "No agent tokens.\n"


def test_private_brains_prints_the_warning(tmp_path: Path, data_dir: Path) -> None:
    plain = _create(data_dir, "plain", tmp_path / "plain.token")
    private = _create(data_dir, "reader", tmp_path / "reader.token", "--private-brains")

    assert plain.exit_code == 0, plain.output
    assert private.exit_code == 0, private.output
    assert "It can read private Second Brains" not in plain.stderr
    assert "It can read private Second Brains" in private.stderr
    assert "output stays on this computer" in private.stderr


def test_text_list_shows_name_state_dates_but_never_the_token(
    tmp_path: Path, data_dir: Path
) -> None:
    empty = _invoke(data_dir, "list")
    assert empty.exit_code == 0, empty.output
    assert empty.stdout == "No agent tokens.\n"

    assert _create(data_dir, "ci", tmp_path / "ci.token").exit_code == 0
    nightly = ("--expires-days", "7", "--private-brains")
    assert _create(data_dir, "nightly", tmp_path / "nightly.token", *nightly).exit_code == 0
    secrets = [
        (tmp_path / name).read_text(encoding="utf-8").strip()
        for name in ("ci.token", "nightly.token")
    ]
    stored = {token.name: token for token in headless.AgentTokenStore(data_dir).list()}

    result = _invoke(data_dir, "list")

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [
        f"ci\tactive\tcreated {stored['ci'].created_at}\texpires never",
        f"nightly\tactive\tcreated {stored['nightly'].created_at}"
        f"\texpires {stored['nightly'].expires_at}\tprivate brains",
    ]
    for secret in secrets:
        assert secret not in result.output
    assert headless.AGENT_TOKEN_PREFIX not in result.output


def test_text_list_marks_an_expired_token(tmp_path: Path, data_dir: Path) -> None:
    assert _create(data_dir, "old", tmp_path / "old.token", "--expires-days", "1").exit_code == 0
    store_file = headless.agent_tokens_path(data_dir)
    document = json.loads(store_file.read_text(encoding="utf-8"))
    document["tokens"][0]["expires_at"] = "2020-01-01T00:00:00+00:00"
    store_file.write_text(json.dumps(document), encoding="utf-8")

    result = _invoke(data_dir, "list")

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines()[0].startswith("old\texpired\tcreated ")
    assert result.stdout.splitlines()[0].endswith("\texpires 2020-01-01T00:00:00+00:00")


def test_list_exits_1_when_the_store_cant_be_read(tmp_path: Path, data_dir: Path) -> None:
    assert _create(data_dir, "ci", tmp_path / "ci.token").exit_code == 0
    store_file = headless.agent_tokens_path(data_dir)
    store_file.write_text('{"version": 1, "tokens": "nope"}\n', encoding="utf-8")

    result = _invoke(data_dir, "list")
    as_json = _invoke(data_dir, "list", "--format", "json")

    for listed in (result, as_json):
        assert listed.exit_code == 1, listed.output
        assert "is malformed" in listed.stderr
        assert listed.stdout == ""


def test_revoke_of_an_unknown_name_exits_3(tmp_path: Path, data_dir: Path) -> None:
    assert _create(data_dir, "ci", tmp_path / "ci.token").exit_code == 0

    result = _invoke(data_dir, "revoke", "missing")

    assert result.exit_code == 3, result.output
    assert "no agent token named 'missing'" in result.stderr
    assert _names(data_dir) == ["ci"]


def test_revoke_exits_1_when_the_store_cant_be_read(tmp_path: Path, data_dir: Path) -> None:
    assert _create(data_dir, "ci", tmp_path / "ci.token").exit_code == 0
    headless.agent_tokens_path(data_dir).write_text("not json\n", encoding="utf-8")

    result = _invoke(data_dir, "revoke", "ci")

    assert result.exit_code == 1, result.output
    assert result.stderr.startswith("Error: ")


def test_agent_sessions_cant_create_or_revoke(
    tmp_path: Path, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _create(data_dir, "ci", tmp_path / "ci.token").exit_code == 0
    monkeypatch.setattr(client, "in_agent_session", lambda env=None: True)

    created = _create(data_dir, "sneaky", tmp_path / "sneaky.token")
    revoked = _invoke(data_dir, "revoke", "ci")

    for result in (created, revoked):
        assert result.exit_code == 5, result.output
        assert "can't be created or revoked from inside a Raticode agent session" in result.stderr
    assert not (tmp_path / "sneaky.token").exists()
    assert _names(data_dir) == ["ci"]
    # Listing reads names only, so it still works from an agent session.
    assert _invoke(data_dir, "list").exit_code == 0
