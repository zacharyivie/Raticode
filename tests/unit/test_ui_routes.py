"""The backend's route table: matching, Mount answers and where server.py runs it."""

from __future__ import annotations

import ast
import json
import logging
import threading
from pathlib import Path
from typing import Any

import pytest

from gofer.rattish.triggers import TriggerNotFound
from gofer.ui import server as server_module
from gofer.ui.routes import Mount, Route, RouteRequest, RouteTable
from gofer.ui.server import ROUTES, GoferUiRequestHandler
from tests.unit.test_ui_server import _fake_server, _request

SERVER = Path(server_module.__file__)


def _table() -> RouteTable:
    table = RouteTable()
    table.mount(Mount("/api/things", miss=(405, "Method not allowed")))
    # Declared before the literal routes on purpose: literals still win.
    table.add(Route("POST", "/api/things/{thing_id}", "_update"))
    table.add(Route("POST", "/api/things/pause", "_pause"))
    table.add(Route("GET", "/api/things/{thing_id}/files/{name}", "_file"))
    table.add(Route("GET", "/api/things/{thing_id}/files/latest", "_latest"))
    return table


def test_a_literal_segment_beats_a_parameter_whatever_the_declaration_order() -> None:
    table = _table()
    pause = table.match("POST", "/api/things/pause")
    assert pause is not None and pause.route is not None and pause.route.handler == "_pause"
    update = table.match("POST", "/api/things/trg_ab12")
    assert update is not None and update.route is not None and update.route.handler == "_update"
    assert update.params == {"thing_id": "trg_ab12"}
    latest = table.match("GET", "/api/things/a/files/latest")
    assert latest is not None and latest.route is not None and latest.route.handler == "_latest"


def test_parameters_are_one_segment_decoded_after_the_split() -> None:
    table = _table()
    found = table.match("GET", "/api/things/a%2Fb/files/n%20x")
    assert found is not None and found.route is not None
    assert found.params == {"thing_id": "a/b", "name": "n x"}
    # A raw slash is another segment, and an empty segment is no parameter.
    for path in ("/api/things/a/b", "/api/things/", "/api/things/a/files/x/y"):
        miss = table.match("POST", path)
        assert miss is not None and miss.route is None and miss.miss == (405, "Method not allowed")


def test_misses_outside_a_mount_and_options_fall_through() -> None:
    table = _table()
    assert table.match("GET", "/api/other") is None
    assert table.match("GET", "/api/thingsmore") is None
    assert table.match("OPTIONS", "/api/things/pause") is None
    wrong_method = table.match("DELETE", "/api/things/pause")
    assert wrong_method is not None and wrong_method.miss == (405, "Method not allowed")


@pytest.mark.parametrize(
    "route",
    [
        Route("GET", "api/x", "_h"),
        Route("GET", "/api/x/", "_h"),
        Route("GET", "/api//x", "_h"),
        Route("GET", "/api/x{y}", "_h"),
        Route("GET", "/api/{a}/{a}", "_h"),
        Route("OPTIONS", "/api/x", "_h"),
    ],
)
def test_the_table_refuses_a_route_it_cannot_match(route: Route) -> None:
    with pytest.raises(ValueError):
        RouteTable().add(route)


def test_the_table_refuses_overlapping_mounts() -> None:
    table = RouteTable()
    table.mount(Mount("/api/a", miss=(404, "x")))
    with pytest.raises(ValueError, match="overlaps"):
        table.mount(Mount("/api/a/b", miss=(404, "x")))


def test_errors_answer_in_order() -> None:
    table = RouteTable()
    table.mount(
        Mount("/api/a", miss=(404, "x"), errors=((KeyError, 404), (LookupError, "_answer")))
    )
    table.add(Route("GET", "/api/a", "_h"))
    found = table.match("GET", "/api/a")
    assert found is not None
    assert found.answer_for(KeyError()) == 404
    assert found.answer_for(IndexError()) == "_answer"
    assert found.answer_for(ValueError()) is None


# Where server.py runs the table ------------------------------------------------------


def _calls(node: ast.AST, name: str) -> list[ast.Call]:
    return [
        child
        for child in ast.walk(node)
        if isinstance(child, ast.Call)
        and isinstance(child.func, ast.Attribute)
        and child.func.attr == name
    ]


def test_the_table_runs_once_after_every_request_check() -> None:
    tree = ast.parse(SERVER.read_text())
    assert len(_calls(tree, "_answer_route")) == 1
    handler = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "GoferUiRequestHandler"
    )
    dispatch = next(
        node
        for node in handler.body
        if isinstance(node, ast.FunctionDef) and node.name == "_dispatch_request"
    )
    [call] = _calls(dispatch, "_answer_route")
    # The first statement inside the try that the central error handler wraps.
    guarded = [
        node
        for node in dispatch.body
        if isinstance(node, ast.Try) and node.handlers and node.finalbody
    ]
    assert len(guarded) == 1
    first = guarded[0].body[0]
    assert isinstance(first, ast.If) and first.test is call
    for check in ("_allow_host", "_allow_origin", "_authorize_ui_request", "acquire"):
        [checked] = _calls(dispatch, check)
        assert checked.lineno < guarded[0].lineno, f"{check} runs before the route table"


def _concrete(route: Route) -> str:
    return "/".join("x1" if part is None else part for part in route.segments)


@pytest.mark.parametrize("route", ROUTES.routes, ids=lambda route: route.key)
def test_every_table_route_checks_the_host_first(tmp_path: Path, route: Route) -> None:
    result = _request(
        tmp_path, route.method, _concrete(route), body={}, headers={"Host": "evil.example"}
    )
    assert result.status == 403
    assert json.loads(result.body) == {"error": "Host is not allowed"}


# Answers that stayed the same when the routes moved into the table ------------------


def test_trigger_pause_reaches_pause_not_update(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def answer(name: str) -> Any:
        return lambda self, request: {"handler": name, "params": request.params}

    monkeypatch.setattr(GoferUiRequestHandler, "_triggers_set_pause", answer("pause"))
    monkeypatch.setattr(GoferUiRequestHandler, "_trigger_update", answer("update"))
    pause = _request(tmp_path, "POST", "/api/rattish/triggers/pause", body={})
    assert json.loads(pause.body) == {"handler": "pause", "params": {}}
    update = _request(tmp_path, "POST", "/api/rattish/triggers/trg_ab12", body={})
    assert json.loads(update.body) == {"handler": "update", "params": {"trigger_id": "trg_ab12"}}


def test_mount_misses_keep_their_old_answers(tmp_path: Path) -> None:
    put = _request(tmp_path, "PUT", "/api/rattish/triggers", body={})
    assert (put.status, json.loads(put.body)) == (405, {"error": "Method not allowed"})
    deep = _request(tmp_path, "GET", "/api/workflow-shortcuts/x/y/z")
    assert (deep.status, json.loads(deep.body)) == (
        404,
        {"error": "Unknown workflow shortcut request"},
    )
    setup = _request(tmp_path, "DELETE", "/api/worktree-setup/approve")
    assert (setup.status, json.loads(setup.body)) == (
        404,
        {"error": "Unknown worktree setup request"},
    )
    patch = _request(tmp_path, "PATCH", "/api/rattish/secrets/API_KEY", body={})
    assert (patch.status, json.loads(patch.body)) == (405, {"error": "Method not allowed"})


def test_a_bad_secret_body_is_never_echoed(tmp_path: Path) -> None:
    raw = b'{"name": "API_KEY", "value": "hunter2-private'
    result = _request(tmp_path, "POST", "/api/rattish/secrets", body=raw)
    assert result.status == 400
    assert json.loads(result.body) == {"error": "Send a secret name and value as JSON."}
    assert b"hunter2" not in result.body


def test_mount_errors_answer_and_others_reach_the_central_handler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def missing(self: GoferUiRequestHandler, request: RouteRequest) -> Any:
        raise TriggerNotFound(f"No trigger {request.params['trigger_id']}")

    monkeypatch.setattr(GoferUiRequestHandler, "_trigger_changes", missing)
    found = _request(tmp_path, "GET", "/api/rattish/triggers/trg_1/changes")
    assert (found.status, json.loads(found.body)) == (404, {"error": "No trigger trg_1"})

    def broken(self: GoferUiRequestHandler, request: RouteRequest) -> Any:
        raise KeyError("bug")

    monkeypatch.setattr(GoferUiRequestHandler, "_trigger_changes", broken)
    caplog.set_level(logging.DEBUG, logger="gofer")
    failed = _request(tmp_path, "GET", "/api/rattish/triggers/trg_1/changes")
    assert failed.status == 500
    assert [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR] == [
        "Unexpected error answering GET /api/rattish/triggers/trg_1/changes"
    ]


def test_options_still_gets_the_preflight_answer(tmp_path: Path) -> None:
    for path in ("/api/rattish/triggers", "/api/worktree-setup", "/api/workflow-shortcuts/x"):
        assert _request(tmp_path, "OPTIONS", path).status == 204


def test_running_a_shortcut_still_takes_an_expensive_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = _fake_server(tmp_path)
    server._expensive_slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr("tests.unit.test_ui_server._fake_server", lambda *a, **kw: server)
    held: list[bool] = []

    def run(self: GoferUiRequestHandler, request: RouteRequest) -> Any:
        held.append(not server._expensive_slots.acquire(blocking=False))
        return {"ran": request.params["shortcut_id"]}

    monkeypatch.setattr(GoferUiRequestHandler, "_shortcut_run", run)
    assert server._expensive_slots.acquire(blocking=False)
    busy = _request(tmp_path, "POST", "/api/workflow-shortcuts/abc/run", body={})
    assert busy.status == 503
    server._expensive_slots.release()
    ran = _request(tmp_path, "POST", "/api/workflow-shortcuts/abc/run", body={})
    assert json.loads(ran.body) == {"ran": "abc"}
    assert held == [True], "the handler ran while the request held the only slot"
    assert server._expensive_slots.acquire(blocking=False), "the slot is released afterwards"
    server._expensive_slots.release()


def test_patch_without_a_route_answers_404_not_500(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger="gofer")
    result = _request(tmp_path, "PATCH", "/api/workflows", body={})
    assert (result.status, json.loads(result.body)) == (404, {"error": "Not found"})
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]
