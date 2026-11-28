"""The backend's route table: matching, Mount answers and where server.py runs it."""

from __future__ import annotations

import ast
import json
import logging
import re
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from gofer.rattish.triggers import TriggerNotFound
from gofer.ui import server as server_module
from gofer.ui.api import WorkflowCreateError, WorkflowUpdateError
from gofer.ui.chat import ChatChangeError
from gofer.ui.chat_media import ChatMediaError
from gofer.ui.chat_steering import ChatSteeringConflict
from gofer.ui.organization_store import OrganizationConflict
from gofer.ui.rem_kanban import KanbanRefused
from gofer.ui.routes import ANSWERED, Mount, Route, RouteRequest, RouteTable
from gofer.ui.server import ROUTES, GoferUiRequestHandler
from tests.unit.test_rem_home_api import api as api
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


def test_the_innermost_mount_answers_and_a_repeated_prefix_is_refused() -> None:
    table = RouteTable()
    table.mount(Mount("/api/a/b", miss=(404, "inner"), errors=((KeyError, 409),)))
    table.mount(Mount("/api/a", miss=(404, "outer"), errors=((KeyError, 404),)))
    table.add(Route("GET", "/api/a/b/c", "_h"))
    table.add(Route("GET", "/api/a/c", "_h2"))
    for path, miss in (("/api/a/b", "inner"), ("/api/a/b/x", "inner"), ("/api/a/bx", "outer")):
        found = table.match("POST", path)
        assert found is not None and found.miss == (404, miss), path
    inner = table.match("GET", "/api/a/b/c")
    outer = table.match("GET", "/api/a/c")
    assert inner is not None and inner.answer_for(KeyError()) == 409
    assert outer is not None and outer.answer_for(KeyError()) == 404
    with pytest.raises(ValueError, match="Two mounts"):
        table.mount(Mount("/api/a", miss=(404, "again")))


def test_a_routes_own_errors_come_before_its_mounts() -> None:
    table = RouteTable()
    table.mount(Mount("/api/a", miss=(404, "x"), errors=((LookupError, 404),)))
    table.add(Route("GET", "/api/a/{id}", "_h", errors=((KeyError, "_answer"),)))
    table.add(Route("GET", "/api/a/other", "_h2"))
    found = table.match("GET", "/api/a/1")
    assert found is not None
    assert found.answer_for(KeyError()) == "_answer"
    assert found.answer_for(IndexError()) == 404
    other = table.match("GET", "/api/a/other")
    assert other is not None and other.answer_for(KeyError()) == 404


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


# The Rem home and kanban routes ------------------------------------------------------


def test_kanban_poll_through_the_table_keeps_its_known_answers(api) -> None:
    request, _service = api
    found = ROUTES.match("GET", "/api/rem/kanban")
    assert found is not None and found.route is not None and found.route.handler == "_kanban"
    first = request("GET", "/api/rem/kanban")
    assert first.status == 200
    full = json.loads(first.body)
    assert {"board", "self_work", "version"} <= set(full)
    version = full["version"]
    # The version the client has, from the header or, for older clients, the query.
    for options in ({"headers": {"X-Gofer-Known": version}}, {"path": f"?known={version}"}):
        path = "/api/rem/kanban" + str(options.pop("path", ""))
        same = request("GET", path, **options)
        assert (same.status, json.loads(same.body)) == (
            200,
            {"unchanged": True, "version": version},
        )
    # A stale version gets the whole board again, the same body as the first poll.
    for options in ({"headers": {"X-Gofer-Known": "stale"}}, {"path": "?known=stale"}):
        path = "/api/rem/kanban" + str(options.pop("path", ""))
        stale = request("GET", path, **options)
        assert (stale.status, json.loads(stale.body)) == (200, full)
    # The header wins over the query value, as the old dispatcher read them.
    both = request("GET", "/api/rem/kanban?known=stale", headers={"X-Gofer-Known": version})
    assert json.loads(both.body) == {"unchanged": True, "version": version}


def test_rem_routes_keep_their_miss_and_error_answers(api, monkeypatch: pytest.MonkeyPatch) -> None:
    request, _service = api
    unknown = [("GET", "/api/rem/"), ("GET", "/api/rem/bogus"), ("PUT", "/api/rem/tasks")]
    for method, path in unknown:
        answer = request(method, path, body={} if method == "PUT" else None)
        assert (answer.status, json.loads(answer.body)) == (
            404,
            {"error": "Unknown Rem endpoint"},
        ), path
    # Under kanban, a request no route matches got the missing-record answer.
    for method, path in [("GET", "/api/rem/kanban/bogus"), ("PUT", "/api/rem/kanban")]:
        answer = request(method, path, body={} if method == "PUT" else None)
        assert (answer.status, json.loads(answer.body)) == (
            404,
            {"error": "Rem record was not found"},
        ), path
    missing = request("GET", "/api/rem/tasks/gone")
    assert (missing.status, json.loads(missing.body)) == (
        404,
        {"error": "Rem record was not found"},
    )
    page = request("GET", "/api/rem/history?before=gone")
    assert (page.status, json.loads(page.body)) == (
        404,
        {"error": "That part of Rem's chat is no longer stored."},
    )
    paging = request("GET", "/api/rem/objectives?limit=a")
    assert (paging.status, json.loads(paging.body)) == (
        400,
        {"error": "limit and offset are whole numbers."},
    )

    def refuse(code: str) -> Any:
        def handler(self: GoferUiRequestHandler, request: RouteRequest) -> Any:
            raise KanbanRefused(code, f"refused {code}")

        return handler

    for code, status in (("conflict", 409), ("not_found", 404), ("unauthorized", 403)):
        monkeypatch.setattr(GoferUiRequestHandler, "_kanban_window", refuse(code))
        refused = request("POST", "/api/rem/kanban/window", body={})
        assert (refused.status, json.loads(refused.body)) == (
            status,
            {"error": f"refused {code}", "code": code},
        )
    # Decision scans change nothing, so only not_found has its own status there.
    scan = request("POST", "/api/rem/decisions/scan", body={})
    assert (scan.status, json.loads(scan.body)["code"]) == (400, "invalid")


def test_rem_options_still_gets_the_preflight_answer(tmp_path: Path) -> None:
    for path in ("/api/rem/tasks", "/api/rem/kanban", "/api/rem/kanban/ticket", "/api/rem/x/y"):
        assert _request(tmp_path, "OPTIONS", path).status == 204


# The workflow routes -----------------------------------------------------------------


def test_a_parity_key_must_cover_the_routes_own_path() -> None:
    table = RouteTable()
    events = Route("GET", "/api/w/{w}/logs/{run}/events", "_h", parity_key="GET /api/w/*/events")
    assert table.add(events).key == "GET /api/w/*/events"
    assert events.path_key == "GET /api/w/*/logs/*/events"
    for wrong in ("GET /api/w/*/logs", "POST /api/w/*/events", "GET /api/other/*/events"):
        with pytest.raises(ValueError, match="doesn't cover"):
            table.add(Route("GET", "/api/w/{w}/logs/{run}/events", "_h", parity_key=wrong))


def test_no_workflow_route_is_left_in_the_if_chains() -> None:
    tree = ast.parse(SERVER.read_text())
    handler = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "GoferUiRequestHandler"
    )
    for node in handler.body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith("_dispatch_"):
            assert "/api/workflows" not in ast.unparse(node), node.name
    manifest = json.loads((SERVER.parents[3] / "docs/cli-parity.json").read_text())
    keys = {
        key
        for key in manifest["routes"]
        if key.split(" ")[0] in {"GET", "POST", "DELETE"}
        and key.split(" ")[1].split("/")[1:3] == ["api", "workflows"]
    }
    assert len(keys) == 33
    assert keys <= {route.key for route in ROUTES.routes}


def _workflow_handler(path: str, method: str = "GET") -> str:
    found = ROUTES.match(method, path)
    assert found is not None and found.route is not None, path
    return found.route.handler


def test_workflow_literals_win_over_ids() -> None:
    assert _workflow_handler("/api/workflows/convert-toml", "POST") == "_workflow_convert_toml"
    # convert-toml is only a literal for POST: deleting a workflow of that name still works.
    assert _workflow_handler("/api/workflows/convert-toml", "DELETE") == "_workflow_delete"
    assert _workflow_handler("/api/workflows/w1/logs/latest") == "_workflow_log_latest"
    assert _workflow_handler("/api/workflows/w1/logs/run-1") == "_workflow_log"
    assert _workflow_handler("/api/workflows/w1/stop", "POST") == "_workflow_stop"
    assert _workflow_handler("/api/workflows/w1/runs/r1/stop", "POST") == "_workflow_run_stop"
    assert (
        _workflow_handler("/api/workflows/w1/tests/results/t1/stop", "POST")
        == "_workflow_test_result_stop"
    )


def test_workflow_misses_answer_not_found(tmp_path: Path) -> None:
    for method, path in (
        ("GET", "/api/workflows/w1/events"),
        ("GET", "/api/workflows/w1/logs/r1/extra"),
        ("POST", "/api/workflows/w1"),
        ("PUT", "/api/workflows/w1"),
        ("DELETE", "/api/workflows"),
    ):
        answer = _request(tmp_path, method, path, body={} if method != "GET" else None)
        assert (answer.status, json.loads(answer.body)) == (404, {"error": "Not found"}), path


def _raises(exc: Exception) -> Any:
    def payload(*_args: Any, **_kwargs: Any) -> Any:
        raise exc

    return payload


def test_workflow_routes_keep_their_error_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gofer.rattish.editor import RattishRevisionConflict
    from gofer.rattish.test_sets import WorkflowTestError
    from gofer.ui.api import (
        WorkflowAlreadyExistsError,
        WorkflowLogError,
        WorkflowUpdateError,
    )

    def answer(method: str, path: str) -> tuple[int, Any]:
        result = _request(tmp_path, method, path, body=None if method == "GET" else {})
        return result.status, json.loads(result.body)

    # The same error is a missing record on some routes and a bad request on others.
    monkeypatch.setattr(server_module, "workflow_tests_payload", _raises(WorkflowTestError("no")))
    monkeypatch.setattr(
        server_module, "save_workflow_tests_payload", _raises(WorkflowTestError("bad"))
    )
    assert answer("GET", "/api/workflows/w1/tests") == (404, {"error": "no"})
    assert answer("POST", "/api/workflows/w1/tests") == (400, {"error": "bad"})
    missing = WorkflowUpdateError("gone")
    monkeypatch.setattr(server_module, "delete_workflow_payload", _raises(missing))
    monkeypatch.setattr(server_module, "workflow_run_worktrees_payload", _raises(missing))
    monkeypatch.setattr(server_module, "stop_workflow_run_payload", _raises(missing))
    assert answer("DELETE", "/api/workflows/w1") == (404, {"error": "gone"})
    assert answer("GET", "/api/workflows/w1/run-worktrees") == (404, {"error": "gone"})
    assert answer("POST", "/api/workflows/w1/stop") == (400, {"error": "gone"})
    log_error = WorkflowLogError("no log")
    monkeypatch.setattr(server_module, "workflow_run_events_payload", _raises(log_error))
    monkeypatch.setattr(server_module, "latest_workflow_log_payload", _raises(log_error))
    assert answer("GET", "/api/workflows/w1/logs/r1/events") == (404, {"error": "no log"})
    assert answer("GET", "/api/workflows/w1/logs/latest") == (400, {"error": "no log"})
    exists = WorkflowAlreadyExistsError("taken")
    monkeypatch.setattr(server_module, "duplicate_workflow_payload", _raises(exists))
    assert answer("POST", "/api/workflows/w1/duplicate") == (409, {"error": "taken"})
    conflict = RattishRevisionConflict("source", "r1", "r2")
    monkeypatch.setattr(server_module, "save_rattish_metadata_payload", _raises(conflict))
    result = _request(
        tmp_path,
        "POST",
        "/api/workflows/w1/metadata",
        body={"metadata": {}, "expectedRevision": "r1"},
    )
    assert (result.status, json.loads(result.body)) == (409, conflict.to_payload())
    unread = _request(tmp_path, "POST", "/api/workflows/w1/document/analyze", body={})
    assert (unread.status, json.loads(unread.body)) == (
        400,
        {"error": "Rattish document analysis requires string source."},
    )


def test_workflow_routes_keep_their_success_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    decided: list[tuple[Any, ...]] = []

    def decide(workflow_id: str, run_id: str, node_id: str, decision: str, *_a: Any, **kw: Any):
        decided.append((workflow_id, run_id, node_id, decision, kw["notes"]))
        return {"ok": True}

    monkeypatch.setattr(server_module, "decide_workflow_approval_payload", decide)
    for action in ("approve", "reject"):
        result = _request(
            tmp_path,
            "POST",
            f"/api/workflows/w1/approvals/run-1/review%20step/{action}",
            body={"notes": "fine"},
        )
        assert (result.status, json.loads(result.body)) == (200, {"ok": True})
    assert decided == [
        ("w1", "run-1", "review step", "approved", "fine"),
        ("w1", "run-1", "review step", "rejected", "fine"),
    ]
    monkeypatch.setattr(server_module, "start_workflow_tests_payload", lambda *_a: {"result": "t1"})
    started = _request(tmp_path, "POST", "/api/workflows/w1/tests/run", body={})
    assert (started.status, json.loads(started.body)) == (202, {"result": "t1"})


# Rem's chat routes ------------------------------------------------------------------


def test_the_dispatchers_have_no_chat_if_blocks() -> None:
    tree = ast.parse(SERVER.read_text())
    handler = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "GoferUiRequestHandler"
    )
    for function in handler.body:
        # _dispatch_request reads the chat path only to pick which POSTs take a slot.
        if (
            isinstance(function, ast.FunctionDef)
            and function.name.startswith("_dispatch_")
            and function.name != "_dispatch_request"
        ):
            for node in ast.walk(function):
                if isinstance(node, ast.If):
                    assert "/api/chat" not in ast.unparse(node.test), function.name


def test_chat_stream_still_takes_an_expensive_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = _fake_server(tmp_path)
    server._expensive_slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr("tests.unit.test_ui_server._fake_server", lambda *a, **kw: server)
    held: list[bool] = []

    def stream(self: GoferUiRequestHandler, request: RouteRequest) -> Any:
        held.append(not server._expensive_slots.acquire(blocking=False))
        self._send_stream_headers()
        self._write_stream_event({"type": "final", "turn": (request.body or {})["turnId"]})
        return ANSWERED

    monkeypatch.setattr(GoferUiRequestHandler, "_chat_stream", stream)
    assert server._expensive_slots.acquire(blocking=False)
    busy = _request(tmp_path, "POST", "/api/chat/stream", body={"turnId": "t1"})
    assert (busy.status, json.loads(busy.body)) == (
        503,
        {"error": "Too many active requests; retry shortly"},
    )
    assert held == [], "the handler never ran without a slot"
    server._expensive_slots.release()
    ran = _request(tmp_path, "POST", "/api/chat/stream", body={"turnId": "t1"})
    # The handler's own stream is the whole answer: nothing is sent after it.
    assert (ran.status, ran.body) == (200, b'{"type": "final", "turn": "t1"}\n')
    assert held == [True], "the handler ran while the request held the only slot"
    assert server._expensive_slots.acquire(blocking=False), "the slot is released afterwards"
    server._expensive_slots.release()


def test_steer_and_stop_never_wait_for_a_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = _fake_server(tmp_path)
    server._expensive_slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr("tests.unit.test_ui_server._fake_server", lambda *a, **kw: server)
    for handler in ("_chat_steer", "_chat_stop"):
        monkeypatch.setattr(GoferUiRequestHandler, handler, lambda self, request: {"ok": True})
    assert server._expensive_slots.acquire(blocking=False)
    for path in ("/api/chat/steer", "/api/chat/stop"):
        answer = _request(tmp_path, "POST", path, body={})
        assert (answer.status, json.loads(answer.body)) == (200, {"ok": True}), path
    server._expensive_slots.release()


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/chat"),
        ("GET", "/api/chat/nope"),
        ("GET", "/api/chat/stream"),
        ("GET", "/api/chat/events/x"),
        ("POST", "/api/chat/nope"),
        ("POST", "/api/chat/threads/x"),
        ("PUT", "/api/chat"),
        ("PATCH", "/api/chat/stream"),
        ("DELETE", "/api/chat/nope"),
    ],
)
def test_an_unknown_chat_path_keeps_its_not_found_answer(
    tmp_path: Path, method: str, path: str
) -> None:
    answer = _request(tmp_path, method, path, body=None if method == "GET" else {})
    assert (answer.status, json.loads(answer.body)) == (404, {"error": "Not found"})


def test_chat_options_still_gets_the_preflight_answer(tmp_path: Path) -> None:
    for path in ("/api/chat", "/api/chat/stream", "/api/chat/nope"):
        assert _request(tmp_path, "OPTIONS", path).status == 204


def test_chat_events_for_a_missing_turn_and_a_bad_cursor(tmp_path: Path) -> None:
    missing = _request(tmp_path, "GET", "/api/chat/events?conversationId=c1&turnId=t1")
    assert (missing.status, json.loads(missing.body)) == (404, {"error": "Rem turn was not found"})
    cursor = _request(tmp_path, "GET", "/api/chat/events?conversationId=c1&turnId=t1&after=x")
    assert cursor.status == 400


@pytest.mark.parametrize(
    ("handler", "method", "path", "error", "status", "body"),
    [
        ("_chat_turn", "POST", "/api/chat", RuntimeError("down"), 500, "Rem failed: down"),
        ("_chat_turn", "POST", "/api/chat", ValueError("bad turn"), 400, "bad turn"),
        ("_chat_stream", "POST", "/api/chat/stream", FileExistsError("taken"), 400, "taken"),
        (
            "_chat_steer",
            "POST",
            "/api/chat/steer",
            ChatSteeringConflict("too late"),
            409,
            "too late",
        ),
        (
            "_chat_stop",
            "POST",
            "/api/chat/stop",
            OSError("disk"),
            500,
            "Could not persist steering receipt",
        ),
        (
            "_chat_steering_receipts",
            "GET",
            "/api/chat/steering",
            OSError("disk"),
            500,
            "Could not read or persist steering receipts",
        ),
        (
            "_chat_attachment_locate",
            "GET",
            "/api/chat/attachments/locate",
            ChatMediaError("gone"),
            404,
            "gone",
        ),
        ("_chat_changes_undo", "POST", "/api/chat/changes/undo", ChatChangeError("x"), 409, "x"),
        (
            "_chat_transcribe_chunk",
            "POST",
            "/api/chat/transcribe/chunk",
            RuntimeError("engine"),
            502,
            "Transcription failed: engine",
        ),
        (
            "_chat_transcribe_cancel",
            "POST",
            "/api/chat/transcribe/cancel",
            ChatMediaError("no session"),
            400,
            "no session",
        ),
        (
            "_chat_speech_model_download",
            "POST",
            "/api/chat/transcribe/model",
            OSError(""),
            400,
            "Downloading the offline speech model failed",
        ),
        (
            "_chat_commit_message",
            "POST",
            "/api/chat/commit-message",
            TimeoutError("slow"),
            400,
            "slow",
        ),
        (
            "_chat_thread_delete",
            "DELETE",
            "/api/chat/threads/t1",
            WorkflowUpdateError("Invalid chat thread id"),
            400,
            "Invalid chat thread id",
        ),
    ],
)
def test_chat_routes_keep_their_error_answers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    handler: str,
    method: str,
    path: str,
    error: Exception,
    status: int,
    body: str,
) -> None:
    monkeypatch.setattr(GoferUiRequestHandler, handler, _raises(error))
    answer = _request(tmp_path, method, path, body=None if method == "GET" else {})
    assert (answer.status, json.loads(answer.body)) == (status, {"error": body})


def test_a_bad_chat_body_keeps_each_routes_status(tmp_path: Path) -> None:
    # The body is read before the handler runs, and its error still answers as the
    # route's old if-block did: undo and redo answered 409, the rest 400.
    for path, status in (
        ("/api/chat/changes/undo", 409),
        ("/api/chat/changes/redo", 409),
        ("/api/chat/transcribe/chunk", 400),
        ("/api/chat/steer", 400),
        ("/api/chat", 400),
    ):
        answer = _request(tmp_path, "POST", path, body=b"{not json")
        assert answer.status == status, path
        assert "error" in json.loads(answer.body)


def test_deleting_a_chat_thread_reads_its_id_from_the_path(tmp_path: Path) -> None:
    attachments = tmp_path / "chat-attachments" / "t1"
    attachments.mkdir(parents=True)
    answer = _request(tmp_path, "DELETE", "/api/chat/threads/t1")
    assert (answer.status, json.loads(answer.body)) == (
        200,
        {"workflowId": "workflow-assistant:t1", "deleted": True},
    )
    assert not attachments.exists()


# Rattish runs, cache and starters, MCP tools and organization webhooks ----------------

INLINE_PATH_CHECK = re.compile(
    r'parsed\.path( == |\.startswith\()"/api/(rattish/(runs|cache|starters)'
    r"|rem-resources/mcp-tools|organization-webhooks)"
)


def test_dispatch_request_has_no_inline_rattish_mcp_or_webhook_routes() -> None:
    assert not INLINE_PATH_CHECK.search(SERVER.read_text())
    for method, path, handler in (
        ("GET", "/api/rattish/runs", "_rattish_runs"),
        ("POST", "/api/rattish/runs/r1/rerun", "_rattish_run_rerun"),
        ("GET", "/api/rattish/runs/r1/failure", "_rattish_run_failure"),
        ("GET", "/api/rattish/runs/r1/changes", "_rattish_run_changes"),
        ("POST", "/api/rattish/runs/r1/land", "_rattish_run_land"),
        ("POST", "/api/rattish/runs/r1/discard", "_rattish_run_discard"),
        ("GET", "/api/rattish/cache", "_rattish_cache_size"),
        ("DELETE", "/api/rattish/cache", "_rattish_cache_clear"),
        ("GET", "/api/rattish/starters", "_rattish_starters"),
        ("GET", "/api/rem-resources/mcp-tools", "_mcp_server_tools"),
        ("POST", "/api/organization-webhooks/o1/w1", "_organization_webhook"),
    ):
        assert _workflow_handler(path, method) == handler, path


def test_mcp_tools_keeps_its_method_and_error_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gofer.rattish import mcp_tool

    calls: list[tuple[str, Any, bool]] = []

    def server_tools(_data_dir: Path, name: str, *, network: Any, sandbox: bool) -> Any:
        calls.append((name, network, sandbox))
        if name == "gone":
            raise mcp_tool.McpToolError("config", "RATTISH_MCP_SERVER_UNKNOWN", "No server gone")
        if name == "broken":
            raise mcp_tool.McpToolError("runtime", "RATTISH_MCP_START_FAILED", "It crashed")
        return {"tools": [{"name": "search"}]}

    monkeypatch.setattr(mcp_tool, "server_tools", server_tools)
    path = "/api/rem-resources/mcp-tools"
    listed = _request(tmp_path, "GET", f"{path}?name=docs&sandbox=off")
    assert (listed.status, json.loads(listed.body)) == (200, {"tools": [{"name": "search"}]})
    assert calls[-1][0] == "docs" and calls[-1][2] is False
    gone = _request(tmp_path, "GET", f"{path}?name=gone")
    assert (gone.status, json.loads(gone.body)) == (
        404,
        {"error": "No server gone", "code": "RATTISH_MCP_SERVER_UNKNOWN"},
    )
    broken = _request(tmp_path, "GET", f"{path}?name=broken")
    assert (broken.status, json.loads(broken.body)) == (
        502,
        {"error": "It crashed", "code": "RATTISH_MCP_START_FAILED"},
    )
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        refused = _request(tmp_path, method, path, body={})
        assert (refused.status, json.loads(refused.body)) == (
            405,
            {"error": "Method not allowed"},
        ), method
    assert _request(tmp_path, "OPTIONS", path).status == 204
    assert len(calls) == 3


def _organizations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, enabled: bool) -> None:
    server = _fake_server(tmp_path)
    setattr(server, "organizations", SimpleNamespace(enabled=enabled))
    monkeypatch.setattr("tests.unit.test_ui_server._fake_server", lambda *a, **kw: server)


def test_organization_webhooks_keep_their_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import gofer.ui.organization_webhooks as webhooks

    received: list[tuple[str, str, bytes]] = []

    def receive(_manager: Any, oid: str, rid: str, _headers: Any, raw: bytes) -> Any:
        received.append((oid, rid, raw))
        if rid == "forged":
            raise ValueError("Webhook signature is invalid")
        if rid == "reused":
            raise OrganizationConflict("Webhook delivery ID reused with another body")
        return {"accepted": True}

    monkeypatch.setattr(webhooks, "receive", receive)

    def post(path: str, body: bytes = b'{"event": "push"}') -> tuple[int, Any]:
        # The webhook needs no UI token: its receiver checks a signature instead.
        answer = _request(tmp_path, "POST", path, body=body, authenticated=False)
        return answer.status, json.loads(answer.body)

    _organizations(tmp_path, monkeypatch, enabled=False)
    off = (403, {"error": "Experimental organizations is disabled"})
    assert post("/api/organization-webhooks/org/routine") == off
    assert post("/api/organization-webhooks/org/routine", b"{not json") == off
    assert received == []

    _organizations(tmp_path, monkeypatch, enabled=True)
    assert post("/api/organization-webhooks/org/routine") == (202, {"accepted": True})
    assert received == [("org", "routine", b'{"event": "push"}')]
    assert post("/api/organization-webhooks/org/forged") == (
        403,
        {"error": "Webhook signature is invalid"},
    )
    # Every ValueError from receive is a refusal, a conflict included, not the central 400.
    assert post("/api/organization-webhooks/org/reused") == (
        403,
        {"error": "Webhook delivery ID reused with another body"},
    )
    assert post("/api/organization-webhooks/org/routine", b"{not json")[0] == 400
    bad = (400, {"error": "Invalid organization webhook URL"})
    for path in (
        "/api/organization-webhooks/org",
        "/api/organization-webhooks/org/",
        "/api/organization-webhooks//routine",
        "/api/organization-webhooks/org/routine/extra",
    ):
        assert post(path) == bad, path
    assert len(received) == 3
    unauthenticated = _request(
        tmp_path, "GET", "/api/organization-webhooks/org/routine", authenticated=False
    )
    assert unauthenticated.status == 401


def test_rattish_run_routes_keep_their_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import gofer.ui.api as api_module

    calls: list[tuple[str, str, dict[str, Any]]] = []

    def record(name: str) -> Any:
        def payload(run_id: str, _base: Path, **kwargs: Any) -> Any:
            calls.append((name, run_id, kwargs))
            return {"done": name}

        return payload

    for name in ("run_changes_payload", "land_run_payload", "discard_run_payload"):
        monkeypatch.setattr(api_module, name, record(name))

    def answer(method: str, path: str, body: Any = None) -> tuple[int, Any]:
        result = _request(tmp_path, method, path, body=body)
        return result.status, json.loads(result.body)

    runs = "/api/rattish/runs"
    assert answer("GET", f"{runs}/r%2F1/changes?workflowId=wf") == (
        200,
        {"done": "run_changes_payload"},
    )
    assert calls[-1] == ("run_changes_payload", "r/1", {"workflow_id": "wf"})
    assert answer("POST", f"{runs}/r1/land?workflowId=q", {"expectedTip": "abc"})[0] == 200
    assert calls[-1] == ("land_run_payload", "r1", {"workflow_id": "q", "expected_tip": "abc"})
    assert answer("POST", f"{runs}/r1/discard", {"workflowId": "wf"})[0] == 200
    assert calls[-1] == ("discard_run_payload", "r1", {"workflow_id": "wf"})
    assert answer("POST", f"{runs}/r1/land", {"expectedTip": 3}) == (
        400,
        {"error": "expectedTip must be a commit hash"},
    )
    assert answer("POST", f"{runs}/r1/discard", {"workflowId": 3}) == (
        400,
        {"error": "workflowId must be a string"},
    )
    for action in ("rerun", "land", "discard"):
        assert answer("POST", f"{runs}/r1/{action}", b"[1]") == (
            400,
            {"error": "Request body must be a JSON object"},
        ), action
    assert answer("GET", f"{runs}?limit=many")[0] == 400
    assert len(calls) == 3


def test_rattish_cache_and_starters_keep_their_error_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gofer.ui import rattish_cache

    monkeypatch.setattr(rattish_cache, "cache_payload", _raises(PermissionError("denied")))
    unreadable = _request(tmp_path, "GET", "/api/rattish/cache?workflowId=demo")
    assert (unreadable.status, json.loads(unreadable.body)) == (
        500,
        {"error": "Could not read or clear the result cache: denied"},
    )
    unknown = _request(tmp_path, "DELETE", "/api/rattish/cache")
    assert unknown.status == 400
    put = _request(tmp_path, "PUT", "/api/rattish/cache", body={})
    assert (put.status, json.loads(put.body)) == (404, {"error": "Not found"})
    monkeypatch.setattr(
        server_module, "starter_workflows_payload", _raises(WorkflowCreateError("no starters"))
    )
    starters = _request(tmp_path, "GET", "/api/rattish/starters")
    assert (starters.status, json.loads(starters.body)) == (500, {"error": "no starters"})


# The last routes the method dispatchers answered ---------------------------------------


@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "PATCH", "DELETE"])
def test_an_api_request_nothing_matches_still_gets_not_found(tmp_path: Path, method: str) -> None:
    body: dict[str, object] | None = None if method == "GET" else {}
    for path in (
        "/api/no-such-thing",
        "/api/provider/no-such-setting",
        "/api/usage",
        "/api/classifiers/jev/key/extra",
        "/api/rattish/workflows/w1/export/extra",
    ):
        answer = _request(tmp_path, method, path, body=body)
        assert (answer.status, json.loads(answer.body)) == (404, {"error": "Not found"}), path
    # A moved route asked with another method gets the same answer.
    other = "PUT" if method != "PUT" else "PATCH"
    wrong = _request(tmp_path, other, "/api/health", body={})
    assert (wrong.status, json.loads(wrong.body)) == (404, {"error": "Not found"})


def test_options_gets_the_preflight_answer_on_every_path(tmp_path: Path) -> None:
    for path in (
        "/api/health",
        "/api/no-such-thing",
        "/api/organizations",
        "/api/second-brains",
        "/api/rem-resources",
        "/api/social/posts",
        "/api/swarms/s1",
    ):
        answer = _request(tmp_path, "OPTIONS", path)
        assert answer.status == 204, path
        assert answer.header("Access-Control-Allow-Methods") == (
            "GET, POST, PUT, PATCH, DELETE, OPTIONS"
        ), path


def test_moved_routes_answer_from_the_table() -> None:
    for method, path, handler in (
        ("GET", "/api/health", "_health"),
        ("GET", "/api/devices", "_devices_status"),
        ("POST", "/api/devices", "_device_action"),
        ("POST", "/api/provider/profiles/local-1/check", "_provider_profile_check"),
        ("DELETE", "/api/provider/profiles/local-1", "_provider_profile_delete"),
        ("POST", "/api/rattish/workflows/import/preview", "_rattish_import_preview"),
        ("POST", "/api/rattish/workflows/w1/export", "_rattish_export"),
        ("GET", "/api/provider/claude-accounts", "_claude_accounts"),
        ("POST", "/api/generation-jobs/dismiss", "_generation_job_dismiss"),
    ):
        assert _workflow_handler(path, method) == handler, path


def test_moved_routes_keep_their_answers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from gofer.ui.link_previews import PreviewError

    def answer(method: str, path: str, body: Any = None) -> tuple[int, Any]:
        result = _request(tmp_path, method, path, body=body)
        return result.status, json.loads(result.body)

    assert answer("GET", "/api/health") == (200, {"ok": True})
    assert answer("GET", "/api/instance/proof?nonce=short") == (
        400,
        {"error": "nonce must be 32 random bytes as 64 lowercase hex characters"},
    )
    assert answer("GET", "/api/provider/capabilities?provider=nope") == (
        400,
        {"error": "Unknown provider"},
    )
    monkeypatch.setattr(server_module, "chat_image", _raises(PreviewError("blocked address")))
    assert answer("GET", "/api/media-image?url=x") == (422, {"error": "blocked address"})
    monkeypatch.setattr(server_module, "usage_credentials_payload", _raises(ValueError("secret")))
    assert answer("GET", "/api/usage/credentials?provider=x") == (
        400,
        {"error": "Invalid usage reporting account."},
    )
    broken = b'{"values": '
    assert answer("POST", "/api/usage/credentials", broken) == (
        400,
        {"error": "Invalid reporting settings."},
    )
    assert answer("POST", "/api/usage/alerts", broken) == (
        400,
        {"error": "Invalid alert settings."},
    )
    assert answer("POST", "/api/classifiers/jev/key", broken) == (
        400,
        {"error": "Invalid Jev settings."},
    )
    assert answer("POST", "/api/classifiers/jev/settings", {"enabled": "maybe"})[0] == 400
    missing = answer("DELETE", "/api/provider/profiles/no-such-profile")
    assert missing == (404, {"error": "Provider profile 'no-such-profile' not found"})
    assert answer("POST", "/api/rattish/workflows/import", {}) == (
        400,
        {"error": "Choose a project folder for the imported workflow"},
    )
    assert answer("POST", "/api/projects/open", {}) == (
        400,
        {"error": "Project folder is required"},
    )
    assert answer("POST", "/api/provider/commit-message/clean", {"message": 3}) == (
        400,
        {"error": "Send the commit message as text"},
    )
    assert answer("POST", "/api/provider/auth", {"action": "explode"}) == (
        400,
        {"error": "Unknown authentication action"},
    )
    # Dismissing a job nobody started answers like a job that couldn't start.
    assert answer("POST", "/api/generation-jobs/dismiss", {"id": "none"})[0] == 400


def test_devices_answer_503_before_reading_the_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = _fake_server(tmp_path)
    server.devices = None
    monkeypatch.setattr("tests.unit.test_ui_server._fake_server", lambda *a, **kw: server)
    status = _request(tmp_path, "GET", "/api/devices")
    assert json.loads(status.body)["enabled"] is False
    action = _request(tmp_path, "POST", "/api/devices", body=b"not json")
    assert (action.status, json.loads(action.body)) == (
        503,
        {"error": "Device service dependencies unavailable"},
    )
