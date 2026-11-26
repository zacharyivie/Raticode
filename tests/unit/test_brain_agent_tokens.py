"""Agent tokens read only brains with Allow cloud processing on, unless opted in."""

from __future__ import annotations

import http.client
import json
import threading
from pathlib import Path
from typing import Any

import pytest

from gofer.ui.server import GoferUiServer
from tests.helpers.brain_library import wait_idle

OWNER = "brain-agent-owner-token"
NOTE = "# Harbor survey\n\nThe harbor survey found two risks near the pier.\n"


@pytest.fixture
def api(tmp_path: Path):
    server = GoferUiServer(("127.0.0.1", 0), tmp_path / "app", api_token=OWNER)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def call(
    server: GoferUiServer, token: str, action: str, params: dict[str, Any] | None = None
) -> tuple[int, Any]:
    connection = http.client.HTTPConnection(
        str(server.server_address[0]), int(server.server_address[1]), timeout=10
    )
    connection.request(
        "POST",
        "/api/second-brains",
        json.dumps({"action": action, "params": params or {}}),
        {"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    response = connection.getresponse()
    value = json.loads(response.read())
    connection.close()
    return response.status, value


def make_brain(server: GoferUiServer, root: Path, name: str, *, cloud: bool) -> dict[str, Any]:
    root.mkdir(parents=True)
    (root / "harbor.md").write_text(NOTE, encoding="utf-8")
    library = server.second_brains
    brain = library.open({"root": str(root)})
    wait_idle(server.second_brains)
    library.update_brain(
        {"id": brain["id"], "patch": {"name": name, "settings": {"allowCloud": cloud}}}
    )
    return library.brain(brain["id"])


@pytest.fixture
def brains(api, tmp_path):
    shared = make_brain(api, tmp_path / "shared", "Shared", cloud=True)
    private = make_brain(api, tmp_path / "diary", "Diary", cloud=False)
    library = api.second_brains
    docs = {
        brain["id"]: library.entities("documents", brain["id"])[0] for brain in (shared, private)
    }
    return shared, private, docs


def reads(brain_id: str, doc_id: str) -> list[tuple[str, dict[str, Any]]]:
    """One of each Second Brain read an agent token may send, aimed at one brain."""
    return [
        ("search", {"brainIds": [brain_id], "query": "harbor"}),
        ("search", {"brainId": brain_id, "query": "harbor"}),
        ("projects", {"brainId": brain_id}),
        ("topics", {"brainId": brain_id}),
        ("report_sources", {"brainId": brain_id, "question": "harbor risks"}),
        ("report_sources", {"brainId": brain_id, "docIds": [doc_id]}),
        (
            "job_sources",
            {"job": {"brainId": brain_id, "recipe": "summarize-new", "sources": {}}},
        ),
        ("original", {"id": doc_id}),
        ("related", {"id": doc_id}),
        ("connections", {"id": doc_id}),
    ]


def test_agent_token_is_refused_private_brains_and_reads_allowed_ones(api, brains):
    shared, private, docs = brains
    agent = api.agent_tokens.create("reader")
    for action, params in reads(private["id"], docs[private["id"]]["id"]):
        status, value = call(api, agent, action, params)
        assert status == 403, (action, params, value)
        assert value["code"] == "brain_private"
        assert "Diary" in value["error"] and "--private-brains" in value["error"]
    for action, params in reads(shared["id"], docs[shared["id"]]["id"]):
        status, value = call(api, agent, action, params)
        assert status == 200, (action, params, value)
    # Naming an allowed brain doesn't let a search or report reach a private one too.
    status, value = call(
        api, agent, "search", {"brainIds": [shared["id"], private["id"]], "query": "harbor"}
    )
    assert status == 403 and value["code"] == "brain_private"
    status, value = call(
        api, agent, "report_sources", {"brainId": shared["id"], "otherBrains": [private["id"]]}
    )
    assert status == 403 and value["code"] == "brain_private"
    status, value = call(
        api,
        agent,
        "report_sources",
        {"brainId": shared["id"], "docIds": [docs[private["id"]]["id"]]},
    )
    assert status == 403 and value["code"] == "brain_private"
    found = call(api, agent, "search", {"brainId": shared["id"], "query": "harbor"})[1]
    assert {hit["docId"] for hit in found["result"]["hits"]} == {docs[shared["id"]]["id"]}


def test_agent_snapshot_lists_private_brains_without_their_contents(api, brains):
    shared, private, docs = brains
    agent = api.agent_tokens.create("reader")
    status, value = call(api, agent, "snapshot")
    assert status == 200
    snapshot = value["result"]
    listed = {brain["id"]: brain for brain in snapshot["brains"]}
    assert listed[private["id"]] == {"id": private["id"], "name": "Diary", "state": "private"}
    assert listed[shared["id"]]["state"] == "available"
    assert listed[shared["id"]]["root"] == shared["root"]
    assert [doc["id"] for doc in snapshot["documents"]] == [docs[shared["id"]]["id"]]
    for task in snapshot["tasks"]:
        assert private["id"] not in [task.get("brainId"), *task.get("sourceBrainIds", [])]
    assert any(task.get("brainId") == shared["id"] for task in snapshot["tasks"])
    text = json.dumps(snapshot)
    assert private["root"] not in text
    assert docs[private["id"]]["id"] not in text


def test_private_brains_token_and_owner_token_read_every_brain(api, brains):
    _shared, private, docs = brains
    local = api.agent_tokens.create("local-script", private_brains=True)
    assert [token.private_brains for token in api.agent_tokens.list()] == [True]
    for token in (local, OWNER):
        for action, params in reads(private["id"], docs[private["id"]]["id"]):
            status, value = call(api, token, action, params)
            assert status == 200, (token == OWNER, action, params, value)
        snapshot = call(api, token, "snapshot")[1]["result"]
        listed = {brain["id"]: brain for brain in snapshot["brains"]}
        assert listed[private["id"]]["state"] == "available"
        assert listed[private["id"]]["root"] == private["root"]
        assert docs[private["id"]]["id"] in {doc["id"] for doc in snapshot["documents"]}
    # The opt-in token is still an agent token: it can't change the library.
    status, value = call(api, local, "remove", {"id": private["id"]})
    assert status == 403 and value["code"] == "agent_token_forbidden"


def test_report_sources_from_a_collection_leave_out_private_documents(api, brains):
    """A read that only names allowed brains still can't list a private document."""
    from gofer.ui.brain_privacy import agent_brain_result

    shared, private, docs = brains
    result = {
        "mode": "auto",
        "sources": [
            {"id": docs[shared["id"]]["id"], "brainId": shared["id"]},
            {"id": docs[private["id"]]["id"], "brainId": private["id"]},
        ],
    }
    kept = agent_brain_result(api.second_brains, "report_sources", result)
    assert [item["id"] for item in kept["sources"]] == [docs[shared["id"]]["id"]]
    jobs = {"sources": [{"id": doc["id"], "title": "t", "path": "p"} for doc in docs.values()]}
    kept = agent_brain_result(api.second_brains, "job_sources", jobs)
    assert [item["id"] for item in kept["sources"]] == [docs[shared["id"]]["id"]]


def test_agent_passages_answer_private_documents_as_missing(api, brains):
    shared, private, docs = brains
    shared_id, private_id = docs[shared["id"]]["id"], docs[private["id"]]["id"]
    agent = api.agent_tokens.create("reader")
    status, value = call(api, agent, "passages", {"ids": [shared_id, private_id]})
    assert status == 200, value
    result = value["result"]
    assert [doc["id"] for doc in result["documents"]] == [shared_id]
    assert result["missing"] == [private_id]
    assert "harbor survey" in json.dumps(result["documents"])
    # The owner reads both.
    owner = call(api, OWNER, "passages", {"ids": [shared_id, private_id]})[1]["result"]
    assert [doc["id"] for doc in owner["documents"]] == [shared_id, private_id]
