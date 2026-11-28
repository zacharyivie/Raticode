"""Files left out of search stay out of agent tokens' document reads and of citations."""

from __future__ import annotations

import http.client
import io
import json
import threading
from pathlib import Path
from typing import Any

import pytest

from gofer.ui.brain_folders import LEFT_OUT_REFUSAL
from gofer.ui.server import GoferUiServer
from tests.helpers.brain_library import wait_idle, write
from tests.unit.test_brain_agent_tokens import OWNER, call


@pytest.fixture
def api(tmp_path: Path):
    server = GoferUiServer(("127.0.0.1", 0), tmp_path / "app", api_token=OWNER)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


@pytest.fixture
def brain(api, tmp_path):
    """A shared brain with a plan, its notes and a diary entry left out in Edit metadata.

    The plan links to the diary entry and the entry links back, so the plan's
    connections would name it as cited and citing.
    """
    root = tmp_path / "brain"
    harbor = "The harbor rollout plan covers the pier, the cranes and the night shift."
    write(root, "plan.md", f"# Plan\n\n{harbor}\n\nSee [notes](notes.md) and [entry](diary.md).\n")
    write(root, "notes.md", f"# Notes\n\n{harbor} Notes on it.\n\nBack to [plan](plan.md).\n")
    write(root, "diary.md", f"# Diary\n\n{harbor} My private worry.\n\nSee [plan](plan.md).\n")
    library = api.second_brains
    opened = library.open({"root": str(root)})
    wait_idle(library)
    library.update_brain({"id": opened["id"], "patch": {"settings": {"allowCloud": True}}})
    docs = {doc["path"]: doc for doc in library.entities("documents", opened["id"])}
    library.mutate({"kind": "documents", "id": docs["diary.md"]["id"], "item": {"excluded": True}})
    return library.brain(opened["id"]), docs


def test_agent_tokens_cant_open_relate_or_connect_a_left_out_document(api, brain):
    _brain, docs = brain
    left_out = docs["diary.md"]["id"]
    plan = docs["plan.md"]["id"]
    # Before the change: what the owner's Library sees, so the test proves something.
    owner_links = call(api, OWNER, "connections", {"id": plan})[1]["result"]["links"]
    assert left_out in owner_links["cites"] and left_out in owner_links["citedBy"]
    # The Library's own window still opens the file for the user.
    status, value = call(api, OWNER, "original", {"id": left_out})
    assert status == 200 and "private worry" in value["result"]["content"]

    reader = api.agent_tokens.create("reader")
    local = api.agent_tokens.create("local-script", private_brains=True)
    for token in (reader, local):
        for action in ("original", "related", "connections"):
            status, value = call(api, token, action, {"id": left_out})
            assert status == 403, (action, value)
            assert value == {"error": LEFT_OUT_REFUSAL, "code": "document_left_out"}
        status, value = call(api, token, "related", {"id": plan})
        assert status == 200 and left_out not in value["result"]
        status, value = call(api, token, "connections", {"id": plan})
        assert status == 200
        connections = value["result"]
        assert left_out not in json.dumps(connections)
        assert docs["notes.md"]["id"] in connections["links"]["cites"]
        assert docs["notes.md"]["id"] in connections["links"]["citedBy"]
        status, value = call(api, token, "original", {"id": plan})
        assert status == 200


def test_save_note_rejects_a_citation_of_a_left_out_document(api, brain, monkeypatch, tmp_path):
    from gofer.ui import second_brain

    shared, docs = brain
    library = api.second_brains
    evidence = [
        {"docId": docs["notes.md"]["id"], "quote": "Notes on it."},
        {"docId": docs["diary.md"]["id"], "quote": "My private worry."},
        # A quote the file doesn't hold gets the same answer, so nothing is learned.
        {"docId": docs["diary.md"]["id"], "quote": "Not in the file at all."},
    ]
    requests = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "save_note",
                "arguments": {
                    "path": "Reports/harbor.md",
                    "content": "# Harbor\n\nNotes [1], worry [2], guess [3].",
                    "format": "md",
                    "evidence": evidence,
                },
            },
        }
    ]
    output = io.StringIO()
    real_serve = second_brain.serve_stdio

    def serve(call_tool: Any, definitions: Any, rules: Any, name: Any) -> None:
        source = io.StringIO("".join(json.dumps(item) + "\n" for item in requests))
        real_serve(call_tool, definitions, rules, name, input_stream=source, output_stream=output)

    monkeypatch.setattr(second_brain, "serve_stdio", serve)
    second_brain.serve_registered_brains(tmp_path / "app", [shared["id"]], "html", "auto")
    reply = json.loads(output.getvalue().splitlines()[0])["result"]
    assert not reply.get("isError"), reply
    saved = json.loads(reply["content"][0]["text"])
    assert Path(saved["path"]).is_file()
    marks = saved["evidence"]
    assert [mark["status"] for mark in marks] == ["cited", "rejected", "rejected"]
    assert [mark.get("error") for mark in marks[1:]] == [LEFT_OUT_REFUSAL, LEFT_OUT_REFUSAL]
    report = next(
        item for item in library.entities("reports") if item["artifactPath"] == "Reports/harbor.md"
    )
    assert [(c["n"], c["docId"]) for c in report["citations"]] == [(1, docs["notes.md"]["id"])]


def test_agent_passages_answer_a_left_out_document_as_missing(api, brain):
    _brain, docs = brain
    left_out, plan = docs["diary.md"]["id"], docs["plan.md"]["id"]
    owner = call(api, OWNER, "passages", {"ids": [plan, left_out]})[1]["result"]
    assert [doc["id"] for doc in owner["documents"]] == [plan, left_out]
    reader = api.agent_tokens.create("reader")
    local = api.agent_tokens.create("local-script", private_brains=True)
    for token in (reader, local):
        status, value = call(api, token, "passages", {"ids": [plan, left_out], "history": True})
        assert status == 200, value
        assert [doc["id"] for doc in value["result"]["documents"]] == [plan]
        assert value["result"]["missing"] == [left_out]
        assert "private worry" not in json.dumps(value)
    # The snapshot no longer carries passages, so it can't leak the entry's text either.
    snapshot = call(api, reader, "snapshot")[1]["result"]
    assert "private worry" not in json.dumps(snapshot)


def get_snapshot(api: GoferUiServer, token: str, query: str) -> Any:
    connection = http.client.HTTPConnection(
        str(api.server_address[0]), int(api.server_address[1]), timeout=10
    )
    connection.request(
        "GET", f"/api/second-brains?{query}", headers={"Authorization": f"Bearer {token}"}
    )
    response = connection.getresponse()
    value = json.loads(response.read())
    connection.close()
    assert response.status == 200, value
    return value["result"]


def test_agent_tokens_never_get_the_full_snapshot(api, brain):
    """The full snapshot is the view's fallback; it would carry left-out documents' text."""
    _brain, docs = brain
    left_out = docs["diary.md"]["id"]
    # Before the change: the owner's full snapshot carries the entry's text, so the
    # agent checks below prove something.
    owner = call(api, OWNER, "snapshot", {"passages": True})[1]["result"]
    assert "private worry" in json.dumps(owner)
    assert "private worry" in json.dumps(get_snapshot(api, OWNER, "passages=1"))
    reader = api.agent_tokens.create("reader")
    local = api.agent_tokens.create("local-script", private_brains=True)
    for token in (reader, local):
        posted = call(api, token, "snapshot", {"passages": True})
        assert posted[0] == 200, posted
        fetched = get_snapshot(api, token, "passages=1")
        for snapshot in (posted[1]["result"], fetched):
            assert "private worry" not in json.dumps(snapshot)
            listed = {doc["id"]: doc for doc in snapshot["documents"]}
            assert "blocks" not in listed[left_out] and "versions" not in listed[left_out]
