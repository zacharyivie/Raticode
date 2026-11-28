"""The node picker's list comes from the installed Rattish contracts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from gofer.rattish.contracts import contract_fingerprint, json_fingerprint
from gofer.ui.api import (
    create_registered_workflow_payload,
    mutate_rattish_document_payload,
    open_rattish_document_payload,
)

PROJECT_ROOT = Path(__file__).parents[2]
CONTRACTS = sorted((PROJECT_ROOT / "rattish" / "contracts").glob("*.json"))
PICKER_CATEGORIES = {"code", "files", "ai", "web", "approvals", "flow", "workflows"}

_SOURCE = """Rattish: 1

Workflow:
  name: Picker

Node prepare:
  type: loop
  source:
    type: count
    count: 2
  to: review

Node review:
  type: bash-command
  command: echo review
  to: done

Node done:
  type: bash-command
  command: echo done
"""


def _open(tmp_path: Path, source: str = _SOURCE) -> dict[str, Any]:
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    registered = create_registered_workflow_payload("Picker", project, registry_dir=tmp_path)
    Path(registered["sourcePath"]).write_text(source, encoding="utf-8")
    return open_rattish_document_payload(registered["id"], tmp_path)


def test_every_built_in_contract_has_a_picker_entry_with_a_docs_page() -> None:
    assert CONTRACTS
    for path in CONTRACTS:
        document = json.loads(path.read_text(encoding="utf-8"))
        catalog = document.get("catalog")
        assert isinstance(catalog, dict), path.name
        assert catalog["category"] in PICKER_CATEGORIES, path.name
        assert catalog["summary"].strip() and "\n" not in catalog["summary"], path.name
        page = PROJECT_ROOT / "website" / f"{catalog['docs_path'].removeprefix('/')}.md"
        assert page.is_file(), (path.name, page)


def test_catalog_text_does_not_change_the_contract_fingerprint() -> None:
    document = json.loads((PROJECT_ROOT / "rattish/contracts/read-file.json").read_text())
    reworded = {**document, "catalog": {**document["catalog"], "summary": "Something else"}}
    without = {key: value for key, value in document.items() if key != "catalog"}
    for field in without["configuration_schema"]["properties"].values():
        field.pop("description", None)

    assert contract_fingerprint(document) == contract_fingerprint(reworded)
    assert contract_fingerprint(document) == json_fingerprint(without)


def test_editor_document_lists_each_contract_with_category_summary_and_docs(
    tmp_path: Path,
) -> None:
    opened = _open(tmp_path)
    contracts = {item["nodeType"]: item for item in opened["nodeContracts"]}

    assert len(contracts) == len(CONTRACTS)
    assert contracts["read-file"]["category"] == "files"
    assert contracts["read-file"]["summary"]
    assert contracts["read-file"]["docsUrl"] == "https://raticode.dev/docs/nodes/read-file"
    assert contracts["workflow"]["category"] == "workflows"
    assert {item["category"] for item in contracts.values()} == PICKER_CATEGORIES


def test_picking_any_node_after_a_selected_node_routes_it_and_still_compiles(
    tmp_path: Path,
) -> None:
    opened = _open(tmp_path)
    assert opened["compilation"]["state"] == "valid"
    child = create_registered_workflow_payload("Child", tmp_path / "project", registry_dir=tmp_path)
    child_document = open_rattish_document_payload(child["id"], tmp_path)
    mutate_rattish_document_payload(
        child["id"],
        [
            {
                "kind": "set_field",
                "target": "workflow",
                "field": "interface-version",
                "valueSource": "1",
            }
        ],
        child_document["sourceRevision"],
        tmp_path,
    )
    from gofer.ui.rem_home_models import Recipe
    from gofer.ui.rem_home_store import RemHomeStore

    rem = RemHomeStore(tmp_path)
    with rem.connect() as db:
        rem.add_task(
            db,
            Recipe.model_validate(
                {
                    "title": "Picker task",
                    "type": "errand",
                    "prompt": "Check the graph.",
                    "schedule": {"kind": "once", "at": "2099-01-01T00:00:00+00:00"},
                }
            ),
        )

    workflow_id = opened["workflowId"]
    revision = opened["sourceRevision"]
    routes = ["done"]
    for index, contract in enumerate(opened["nodeContracts"], start=1):
        node_id = f"picked-{index}"
        # The picker sends the route first, so a Break node can find the loop it follows.
        routes = [*routes, node_id]
        mutated = mutate_rattish_document_payload(
            workflow_id,
            [
                {"kind": "set_routes", "node": "review", "routes": routes},
                {"kind": "add_node", "node": node_id, "node_type": contract["nodeType"]},
            ],
            revision,
            tmp_path,
        )
        assert mutated["compilation"]["state"] == "valid", (
            contract["nodeType"],
            mutated["diagnostics"],
        )
        added = next(node for node in mutated["graph"]["nodes"] if node["id"] == node_id)
        assert added["type"] == contract["nodeType"]
        assert any(
            edge["from"] == "review" and edge["to"] == node_id for edge in mutated["graph"]["edges"]
        )
        revision = mutated["sourceRevision"]


def test_replacing_a_one_line_route_or_need_with_a_list_nests_the_list(tmp_path: Path) -> None:
    opened = _open(tmp_path)
    mutated = mutate_rattish_document_payload(
        opened["workflowId"],
        [
            {"kind": "set_routes", "node": "review", "routes": ["done", "extra"]},
            {"kind": "add_node", "node": "extra", "node_type": "bash-command"},
            {"kind": "set_needs", "node": "done", "nodes": ["review", "prepare"]},
        ],
        opened["sourceRevision"],
        tmp_path,
    )

    assert mutated["compilation"]["state"] == "valid", mutated["diagnostics"]
    assert "  to:\n    - done\n    - extra\n" in mutated["source"]
    assert "  needs:\n    - review\n    - prepare\n" in mutated["source"]
