from __future__ import annotations

import hashlib
import json
import sys
import threading
import time
import zipfile
from pathlib import Path
from typing import Any

import pytest

from gofer.ui.brain_documents import checked_archive, extract, source_path
from gofer.ui.brain_library import BrainLibrary


def wait_task(library: BrainLibrary, task: dict[str, Any]) -> dict[str, Any]:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        receipt = library.task(task["id"])
        if receipt["state"] != "running":
            return receipt
        time.sleep(0.02)
    pytest.fail("Second Brain task did not finish")


@pytest.fixture
def library(tmp_path: Path):
    service = BrainLibrary(tmp_path / "app", start=False)
    yield service
    service.close()


def opened(library: BrainLibrary, tmp_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    root = tmp_path / "brain"
    root.mkdir()
    (root / "notes.md").write_text(
        (
            "---\ntitle: Durable search\nauthor: Ada\ntags: [search, design]"
            "\n---\n# Findings\n\nThe index preserves exact evidence.\n"
        ),
        encoding="utf-8",
    )
    brain = library.open({"root": str(root)})
    for task in library.snapshot()["tasks"]:
        assert wait_task(library, task)["state"] == "done"
    return brain, library.entities("documents", brain["id"])[0]


def test_registry_extraction_and_fts_are_real_and_survive_restart(library, tmp_path):
    brain, doc = opened(library, tmp_path)
    assert doc["title"] == "Durable search"
    assert doc["author"] == "Ada"
    assert "design" in doc["tags"]
    assert (
        library.search({"brainId": brain["id"], "query": "evidence"})["hits"][0]["docId"]
        == doc["id"]
    )
    reopened = BrainLibrary(tmp_path / "app", start=False)
    try:
        assert reopened.brains()[0]["id"] == brain["id"]
        assert reopened.entities("documents")[0]["id"] == doc["id"]
    finally:
        reopened.close()


def test_html_indexing_removes_script_and_styles_and_preserves_unicode():
    value = extract(
        (
            b"<html><head><title>Evidence</title><style>fake claim</style>"
            b"</head><body><p>Caf\xc3\xa9 evidence</p><script>secret fake claim<"
            b"/script></body></html>"
        ),
        ".html",
    )
    text = " ".join(block["text"] for block in value["blocks"])
    assert "Café evidence" in text
    assert "fake claim" not in text


def test_rename_preserves_identity_and_edit_retains_source_version(library, tmp_path):
    brain, doc = opened(library, tmp_path)
    root = Path(brain["root"])
    (root / "notes.md").rename(root / "renamed.md")
    library.scan(brain["id"])
    current = library.entities("documents")[0]
    assert current["id"] == doc["id"]
    (root / "renamed.md").write_text("# Findings\n\nA changed conclusion.", encoding="utf-8")
    library.scan(brain["id"])
    edited = library.entities("documents")[0]
    assert edited["id"] == doc["id"]
    assert edited["versions"][0]["hash"] == doc["version"]
    assert "The index preserves exact evidence." in edited["versions"][0]["blocks"].values()


def test_curated_state_is_portable_and_citation_quotes_are_validated(library, tmp_path):
    brain, doc = opened(library, tmp_path)
    block = doc["blocks"][-1]
    citation = {
        "id": "c1",
        "brainId": brain["id"],
        "docId": doc["id"],
        "version": doc["version"],
        "blockId": block["id"],
        "quote": "exact evidence",
    }
    library.mutate({"kind": "citations", "id": "c1", "item": citation})
    stored = library.entity("citations", "c1")
    assert stored["selector"]["exact"] == "exact evidence"
    assert stored["snapshot"] == block["text"]
    with pytest.raises(ValueError, match="does not match"):
        library.mutate(
            {"kind": "citations", "id": "c2", "item": {**citation, "quote": "invented evidence"}}
        )
    portable = json.loads((Path(brain["root"]) / ".raticode/brain.json").read_text())
    assert portable["library"]["citations"][0]["id"] == "c1"
    assert "root" not in portable["brain"]


def test_export_import_roundtrip_remaps_citations_and_keeps_hashes(library, tmp_path):
    brain, doc = opened(library, tmp_path)
    block = doc["blocks"][-1]
    library.mutate(
        {
            "kind": "citations",
            "id": "c1",
            "item": {
                "id": "c1",
                "brainId": brain["id"],
                "docId": doc["id"],
                "version": doc["version"],
                "blockId": block["id"],
                "quote": "exact evidence",
            },
        }
    )
    target = tmp_path / "archive.zip"
    result = library.export_package(
        {"brainId": brain["id"], "destination": str(target), "verify": True},
        threading.Event(),
        lambda _: None,
    )
    assert result["verified"]
    imported = library.import_package(
        {"path": str(target), "destination": str(tmp_path / "copy"), "identity": "copy"},
        threading.Event(),
        lambda _: None,
    )
    for task in library.snapshot()["tasks"]:
        wait_task(library, task)
    copy_doc = library.entities("documents", imported["brainId"])[0]
    assert copy_doc["id"] != doc["id"]
    assert copy_doc["version"] == doc["version"]
    assert library.entities("citations", imported["brainId"])[0]["docId"] == copy_doc["id"]


@pytest.mark.parametrize(
    "name", ["../escape", "/absolute", "files/../../escape", "files\\escape", "files/C:/escape"]
)
def test_import_rejects_unsafe_archive_paths(tmp_path, name):
    target = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr(name, b"x")
    with pytest.raises(ValueError):
        checked_archive(target)


def test_symlink_sources_are_not_read_or_exported(library, tmp_path):
    brain, _ = opened(library, tmp_path)
    outside = tmp_path / "secret.md"
    outside.write_text("secret", encoding="utf-8")
    (Path(brain["root"]) / "alias.md").symlink_to(outside)
    with pytest.raises(ValueError, match="Symbolic"):
        source_path(Path(brain["root"]), "alias.md")
    library.scan(brain["id"])
    assert all(doc["path"] != "alias.md" for doc in library.entities("documents"))


def test_capture_writes_actual_file_and_discard_is_recoverable(library, tmp_path):
    brain, _ = opened(library, tmp_path)
    result = library.capture(
        {"brainId": brain["id"], "title": "Field finding", "excerpt": "Quoted observation."},
        threading.Event(),
    )
    doc = library.entity("documents", result["docId"])
    assert doc["inbox"] and doc["extraction"] == "ready"
    library.mutate({"kind": "documents", "id": doc["id"], "delete": True})
    assert not (Path(brain["root"]) / doc["path"]).exists()
    assert list((Path(brain["root"]) / ".raticode/trash").rglob("*.html"))


def test_report_uses_provider_and_validates_pinned_evidence(library, tmp_path):
    brain, doc = opened(library, tmp_path)
    library.update_brain({"id": brain["id"], "patch": {"settings": {"allowCloud": True}}})
    for task in library.snapshot()["tasks"]:
        wait_task(library, task)
    prompts = []

    def generate(prompt, provider, cancel):
        prompts.append(prompt)
        evidence = json.loads(prompt.split("Evidence:\n")[1])
        block = next(item for item in evidence if "exact evidence" in item["text"])
        return {
            "title": "Real findings",
            "claims": [
                {
                    "section": "Findings",
                    "text": "The index preserves evidence.",
                    "support": "paraphrased",
                    "citations": [{"evidence": block["id"], "quote": "exact evidence"}],
                }
            ],
        }

    library.generate = generate
    result = library.report(
        {
            "brainId": brain["id"],
            "docIds": [doc["id"]],
            "question": "What does the index preserve?",
            "provider": {"id": "codex", "model": "test"},
        },
        threading.Event(),
        lambda _: None,
    )
    report = library.entity("reports", result["reportId"])
    assert prompts
    assert report["claims"][0]["text"] == "The index preserves evidence."
    assert report["citations"][0]["state"] == "unreviewed"
    assert (
        report["outputHash"]
        == "sha256:" + hashlib.sha256(Path(result["path"]).read_bytes()).hexdigest()
    )
    assert "exact evidence" in Path(result["path"]).read_text()
    library.generate = lambda *args: {
        "claims": [{"text": "Bad", "citations": [{"evidence": "e1", "quote": "invented"}]}]
    }
    with pytest.raises(ValueError, match="does not match"):
        library.report(
            {
                "brainId": brain["id"],
                "docIds": [doc["id"]],
                "question": "Question",
                "provider": {"id": "codex"},
            },
            threading.Event(),
            lambda _: None,
        )
    assert len(library.entities("reports")) == 1


def test_failed_tasks_and_interrupted_receipts_are_honest(library, tmp_path):
    brain, doc = opened(library, tmp_path)
    task = library.start_task(
        "report", {"brainId": brain["id"], "docIds": [doc["id"]], "question": "Question"}
    )
    receipt = wait_task(library, task)
    assert receipt["state"] == "failed"
    assert "cloud processing" in receipt["error"]
    with library.connect() as db:
        db.execute(
            "INSERT INTO tasks VALUES(?,?)",
            ("interrupted", json.dumps({"id": "interrupted", "state": "running"})),
        )
    reopened = BrainLibrary(tmp_path / "app", start=False)
    try:
        assert reopened.task("interrupted")["state"] == "failed"
    finally:
        reopened.close()


def cited_output(prompt: str, provider: dict[str, Any], cancel: threading.Event) -> dict[str, Any]:
    evidence = json.loads(prompt.split("Evidence:\n")[1].split("\nIntermediate findings:")[0])
    source = evidence[-1]
    return {
        "title": "Verified finding",
        "claims": [
            {
                "section": "Findings",
                "text": "A finding.",
                "support": "quoted",
                "citations": [{"evidence": source["id"], "quote": source["text"]}],
            }
        ],
    }


def allow_research(library: BrainLibrary, brain: dict[str, Any]) -> None:
    library.update_brain({"id": brain["id"], "patch": {"settings": {"allowCloud": True}}})
    library.generate = cited_output


def test_provider_generation_uses_valid_tool_restricted_adapter_settings(library, monkeypatch):
    from gofer.core.agent import AgentResult
    from gofer.core.runner import _SUBSCRIPTIONS

    for name in ("codex", "claude_code"):
        subscription = _SUBSCRIPTIONS[name]
        calls = []

        async def execute(self, prompt, working_dir, tools, mcp_servers, env, **kwargs):
            settings = kwargs["provider_settings"]
            command = self._build_command(prompt, tools, mcp_servers, provider_settings=settings)
            calls.append(command)
            assert working_dir.name.startswith("raticode-research-")
            assert settings.tools == [] and settings.mcp_servers == []
            return AgentResult(
                agent_id="Rem",
                success=True,
                output='{"claims":[]}',
                exit_code=0,
                duration_seconds=0,
                usage_metadata={"model": "reported"},
            )

        monkeypatch.setattr(type(subscription), "execute", execute)
        result = library._provider_generate("Evidence only", {"id": name}, threading.Event())
        assert result["modelReported"] == "reported"
        assert len(calls) == 1
        if name == "codex":
            assert "read-only" in calls[0]
        else:
            assert "--strict-mcp-config" in calls[0] and "dontAsk" in calls[0]


def test_automatic_report_selects_all_relevant_sources_from_large_ai_written_library(
    library, tmp_path
):
    root = tmp_path / "large-brain"
    root.mkdir()
    for index in range(347):
        topic = "Index durability" if index < 115 else "Unrelated cooking"
        (root / f"note-{index:03}.md").write_text(
            f"---\nauthor: Rem\n---\n# {topic}\n\n"
            + ("The index preserves exact evidence." if index < 115 else "Fresh vegetables."),
            encoding="utf-8",
        )
    brain = library.open({"root": str(root)})
    for task in library.snapshot()["tasks"]:
        wait_task(library, task)
    params = {
        "brainId": brain["id"],
        "question": "Write a report about index durability",
        "sourceSelection": "auto",
        "allowCloudForTask": True,
    }
    preview = library.request("report_sources", params)
    assert preview["candidateCount"] == 347 and preview["matchedCount"] == 115
    assert len(preview["sources"]) == preview["selectedCount"] == 115
    assert preview["limit"] is None and preview["minimumRelevance"] == 0
    assert {doc["title"] for doc in preview["sources"]} == {"Index durability"}
    assert all(
        library.entity("documents", doc["id"])["author"] == "Rem" for doc in preview["sources"]
    )
    prompts = []

    def generate(prompt, provider, cancel):
        prompts.append(prompt)
        return cited_output(prompt, provider, cancel)

    library.generate = generate
    task = wait_task(library, library.start_task("report", params))
    assert task["state"] == "done", task.get("error")
    assert task["request"]["sourceSelection"] == "auto"
    report = library.entity("reports", task["result"]["reportId"])
    assert {source["docId"] for source in report["sourceVersions"]} == {
        source["id"] for source in preview["sources"]
    }
    assert report["sourceSelection"]["candidateCount"] == 347
    assert "other documents were not reviewed" in Path(task["result"]["path"]).read_text()
    evidence = json.loads(prompts[0].split("Evidence:\n")[1])
    assert len({item["docId"] for item in evidence}) == 115
    assert not any("vegetables" in item["text"] for item in evidence)
    assert report["citations"][0]["state"] == "unreviewed"


def test_automatic_report_threshold_and_optional_limit_apply_to_preview_and_generation(
    library, tmp_path
):
    brain, original = opened(library, tmp_path)
    original.update(title="Index durability", tags=["replication"])
    library.put("documents", original)
    root = Path(brain["root"])
    (root / "partial.md").write_text("# Observations\n\nIndex durability matters.")
    (root / "weak.md").write_text("# Observations\n\nAn index.")
    (root / "none.md").write_text("# Observations\n\nCooking vegetables.")
    library.scan(brain["id"])
    # Source selection must not depend on the capped passage-search endpoint.
    library.search = lambda _: pytest.fail("Report selection must examine all eligible documents")
    params = {
        "brainId": brain["id"],
        "question": "Index durability replication",
        "sourceSelection": "auto",
        "sourceRelevance": 66,
        "sourceLimit": 1,
        "allowCloudForTask": True,
    }
    broad = library.request("report_sources", {**params, "sourceRelevance": 0, "sourceLimit": None})
    assert broad["selectedCount"] == 3
    exact = library.request(
        "report_sources", {**params, "sourceRelevance": 100, "sourceLimit": None}
    )
    assert [source["id"] for source in exact["sources"]] == [original["id"]]
    preview = library.request("report_sources", params)
    assert preview["matchedCount"] == 2 and preview["selectedCount"] == 1
    assert preview["sources"][0]["id"] == original["id"]
    library.generate = cited_output
    task = wait_task(library, library.start_task("report", params))
    assert task["state"] == "done", task.get("error")
    assert task["request"]["sourceRelevance"] == 66 and task["request"]["sourceLimit"] == 1
    report = library.entity("reports", task["result"]["reportId"])
    assert report["sourceSelection"]["minimumRelevance"] == 66
    assert report["sourceSelection"]["limit"] == 1
    assert [source["docId"] for source in report["sourceVersions"]] == [original["id"]]
    assert "Top 1 matches requested" in Path(task["result"]["path"]).read_text()


@pytest.mark.parametrize(
    "options",
    [
        {"sourceRelevance": -1},
        {"sourceRelevance": 101},
        {"sourceRelevance": 2.5},
        {"sourceRelevance": True},
        {"sourceLimit": 0},
        {"sourceLimit": -1},
        {"sourceLimit": 1.5},
        {"sourceLimit": "12"},
        {"sourceLimit": True},
    ],
)
def test_automatic_source_options_reject_invalid_values(library, tmp_path, options):
    brain, _ = opened(library, tmp_path)
    with pytest.raises(ValueError):
        library.request("report_sources", {"brainId": brain["id"], "question": "Index", **options})


def test_automatic_selection_respects_scope_eligibility_and_previous_outputs(library, tmp_path):
    brain, doc = opened(library, tmp_path)
    allow_research(library, brain)
    generated = library.report(
        {"brainId": brain["id"], "docIds": [doc["id"]], "question": "Evidence"},
        threading.Event(),
        lambda _: None,
    )
    previous = next(
        item
        for item in library.entities("documents", brain["id"])
        if item["path"] == library.entity("reports", generated["reportId"])["artifactPath"]
    )
    assert previous["origin"] == "generated"
    for path, title in [("title-only.md", "Index evidence"), ("hidden.md", "Index evidence")]:
        (Path(brain["root"]) / path).write_text(f"# {title}\n\nDifferent contents.")
    library.scan(brain["id"])
    hidden = next(item for item in library.entities("documents") if item["path"] == "hidden.md")
    library.mutate({"kind": "documents", "id": hidden["id"], "item": {"excluded": True}})
    title_only = next(
        item for item in library.entities("documents") if item["path"] == "title-only.md"
    )
    params = {"brainId": brain["id"], "question": "Index evidence", "sourceSelection": "auto"}
    sources = library.request("report_sources", params)["sources"]
    assert {item["id"] for item in sources} == {doc["id"], title_only["id"]}
    sources = library.request("report_sources", {**params, "includeGenerated": True})["sources"]
    assert previous["id"] in {item["id"] for item in sources}
    scoped = library.request("report_sources", {**params, "docIds": [doc["id"], doc["id"]]})
    assert scoped["candidateCount"] == 1 and scoped["selectedCount"] == 1
    collection = {
        "id": "col-scope",
        "brainId": brain["id"],
        "name": "Only one source",
        "sections": [{"items": [{"docId": title_only["id"]}]}],
    }
    library.put("collections", collection)
    scoped = library.request("report_sources", {**params, "collectionId": collection["id"]})
    assert [item["id"] for item in scoped["sources"]] == [title_only["id"]]
    title_only["missing"] = True
    library.put("documents", title_only)
    scoped = library.request("report_sources", {**params, "collectionId": collection["id"]})
    assert scoped["sources"] == []


@pytest.mark.parametrize(
    "question",
    [
        "Please write a report for me",
        "Please create a detailed cited report about these documents",
        "Quantum bananas",
    ],
)
def test_automatic_report_with_no_matches_never_sends_all_sources(library, tmp_path, question):
    brain, _ = opened(library, tmp_path)
    library.generate = lambda *args: pytest.fail("Unmatched evidence must not reach the provider")
    params = {
        "brainId": brain["id"],
        "question": question,
        "sourceSelection": "auto",
        "allowCloudForTask": True,
    }
    assert library.request("report_sources", params)["sources"] == []
    with pytest.raises(ValueError, match="No eligible sources match"):
        library.report(params, threading.Event(), lambda _: None)


def test_automatic_cross_brain_selection_keeps_cloud_permission_checks(library, tmp_path):
    brain, _ = opened(library, tmp_path)
    root = tmp_path / "other-brain"
    root.mkdir()
    (root / "other.md").write_text("# Durable index\n\nIndependent evidence.")
    other = library.open({"root": str(root)})
    for task in library.snapshot()["tasks"]:
        wait_task(library, task)
    params = {
        "brainId": brain["id"],
        "otherBrains": [other["id"]],
        "sourceSelection": "auto",
        "question": "Durable index",
    }
    preview = library.request("report_sources", params)
    assert {source["brainId"] for source in preview["sources"]} == {brain["id"], other["id"]}
    library.generate = cited_output
    library.update_brain({"id": brain["id"], "patch": {"settings": {"allowCloud": True}}})
    with pytest.raises(ValueError, match="Allow cloud processing"):
        library.report(params, threading.Event(), lambda _: None)
    result = library.report(
        {**params, "allowCloudForTask": True}, threading.Event(), lambda _: None
    )
    report = library.entity("reports", result["reportId"])
    assert len(report["sourceVersions"]) == 2
    assert not library.brain(other["id"])["settings"]["allowCloud"]


@pytest.mark.parametrize("provider", ["codex", "claude_code"])
def test_tool_free_research_delivers_large_evidence_via_stdin(library, monkeypatch, provider):
    from gofer.core.runner import _SUBSCRIPTIONS

    subscription = _SUBSCRIPTIONS[provider]
    shared_transport = subscription.prompt_via_stdin
    adapter = type(subscription)
    build_command = adapter._build_command
    commands = []
    prompt = "Evidence café. " * 16000 + "END OF SELECTED DOCUMENT"
    # Exercise the actual subprocess transport without invoking a real LLM provider.
    child = (
        "import json, sys\n"
        "prompt = sys.stdin.read()\n"
        "assert 'Evidence café. ' * 16000 in prompt\n"
        "assert 'END OF SELECTED DOCUMENT' in prompt\n"
        "assert 'gofer-agent-prompt-' not in prompt\n"
        "print(json.dumps({'type': 'result', 'subtype': 'success', "
        "'result': json.dumps({'claims': []})}))\n"
    )

    def fake_command(self, *args, **kwargs):
        command = build_command(self, *args, **kwargs)
        commands.append(command)
        assert self.prompt_via_stdin
        return [sys.executable, "-c", child, *command[1:]]

    monkeypatch.setattr(adapter, "_build_command", fake_command)
    result = library._provider_generate(prompt, {"id": provider}, threading.Event())
    assert result["claims"] == []
    assert subscription.prompt_via_stdin == shared_transport
    assert len(commands) == 1
    command = commands[0]
    assert all("gofer-agent-prompt-" not in argument for argument in command)
    if provider == "codex":
        assert command[-1] == "-"
        assert "read-only" in command
        assert "features.shell_tool=false" in command
    else:
        assert "--print" in command
        assert "-p" not in command and "-" not in command
        assert command[command.index("--tools") + 1] == ""
        assert "--strict-mcp-config" in command


@pytest.mark.parametrize(
    "response", ["", "I cannot read the prompt file.", '```json\n{"claims":\n```']
)
def test_research_invalid_response_has_actionable_error(library, monkeypatch, response):
    from gofer.core.agent import AgentResult
    from gofer.core.runner import _SUBSCRIPTIONS

    async def execute(*args, **kwargs):
        return AgentResult(
            agent_id="Rem", success=True, output=response, exit_code=0, duration_seconds=0
        )

    monkeypatch.setattr(type(_SUBSCRIPTIONS["codex"]), "execute", execute)
    with pytest.raises(ValueError, match="provider .*codex.*valid report JSON") as error:
        library._provider_generate("Evidence only", {"id": "codex"}, threading.Event())
    assert "Try again or choose another provider" in str(error.value)
    assert not isinstance(error.value, json.JSONDecodeError)


def test_rename_and_edit_together_keep_identity(library, tmp_path):
    brain, doc = opened(library, tmp_path)
    source = Path(brain["root"]) / "notes.md"
    source.rename(source.with_name("renamed.md"))
    source.with_name("renamed.md").write_text("# Updated\n\nChanged while renaming.")
    library.scan(brain["id"])
    current = library.entities("documents")[0]
    assert current["id"] == doc["id"]
    assert current["versions"][0]["hash"] == doc["version"]


@pytest.mark.parametrize("provider", ["cursor", "copilot", "opencode"])
def test_additional_research_adapters_disable_writes_shell_and_web(library, monkeypatch, provider):
    from contextlib import contextmanager

    from gofer.subscriptions import cli_providers

    calls = []

    @contextmanager
    def invocation(name, command, resources, **kwargs):
        calls.append(name)
        assert not resources.shell and not resources.web and not resources.mcpServers
        assert kwargs["read_only"]
        yield command, kwargs["env"]

    async def events(*args, **kwargs):
        yield {"type": "result", "message": {"body": '{"claims":[]}'}, "exitCode": 0}

    async def plugins(*args, **kwargs):
        return None

    monkeypatch.setattr(cli_providers, "cli_command", lambda *args, **kwargs: [provider])
    monkeypatch.setattr(cli_providers, "cli_invocation", invocation)
    monkeypatch.setattr(cli_providers, "stream_cli", events)
    monkeypatch.setattr(cli_providers, "check_cursor_plugins", plugins)
    assert (
        library._provider_generate("Evidence only", {"id": provider}, threading.Event())["claims"]
        == []
    )
    assert calls == [provider]


def test_missing_cited_file_returns_to_search_and_review(library, tmp_path):
    brain, doc = opened(library, tmp_path)
    allow_research(library, brain)
    result = library.report(
        {"brainId": brain["id"], "docIds": [doc["id"]], "question": "Finding?"},
        threading.Event(),
        lambda _: None,
    )
    path = Path(brain["root"]) / doc["path"]
    content = path.read_bytes()
    path.unlink()
    library.scan(brain["id"])
    assert library.entity("documents", doc["id"])["missing"]
    assert library.entity("reports", result["reportId"])["citations"][0]["state"] == "unavailable"
    path.write_bytes(content)
    library.scan(brain["id"])
    assert not library.entity("documents", doc["id"])["excluded"]
    assert library.entity("reports", result["reportId"])["citations"][0]["state"] == "unreviewed"
    assert library.search({"brainId": brain["id"], "query": "evidence"})["hits"]


def test_reopening_or_renaming_brain_does_not_rebuild_index(library, tmp_path):
    brain, _ = opened(library, tmp_path)
    before = len(library.snapshot()["tasks"])
    library.update_brain({"id": brain["id"], "patch": {**brain, "lastOpened": "new"}})
    assert len(library.snapshot()["tasks"]) == before


def test_metadata_source_and_citation_removal_persist(library, tmp_path):
    brain, doc = opened(library, tmp_path)
    library.mutate(
        {
            "kind": "documents",
            "id": doc["id"],
            "item": {"source": {"url": "https://example.org/source", "doi": "10.1/test"}},
        }
    )
    library.scan(brain["id"])
    assert library.entity("documents", doc["id"])["source"]["doi"] == "10.1/test"
    block = doc["blocks"][-1]
    library.mutate(
        {
            "kind": "citations",
            "id": "saved",
            "item": {
                "brainId": brain["id"],
                "docId": doc["id"],
                "blockId": block["id"],
                "quote": "exact evidence",
            },
        }
    )
    assert library.entity("documents", doc["id"])["annotations"] == 1
    library.mutate({"kind": "citations", "id": "saved", "delete": True})
    assert library.entity("documents", doc["id"])["annotations"] == 0


def test_import_cannot_overwrite_foreign_record_ids(library, tmp_path):
    brain, doc = opened(library, tmp_path)
    portable = json.loads((Path(brain["root"]) / ".raticode/brain.json").read_text())
    root = tmp_path / "poisoned"
    (root / ".raticode").mkdir(parents=True)
    portable["brain"]["id"] = "different-brain"
    for item in portable["library"]["documents"]:
        item["brainId"] = "different-brain"
    (root / ".raticode/brain.json").write_text(json.dumps(portable))
    with pytest.raises(ValueError, match="record identity"):
        library.open({"root": str(root)})
    assert library.entity("documents", doc["id"])["brainId"] == brain["id"]
    assert len(library.brains()) == 1


def test_encoding_task_extracts_real_bytes(library, tmp_path):
    brain, _ = opened(library, tmp_path)
    (Path(brain["root"]) / "legacy.txt").write_bytes("Café evidence".encode("cp1252"))
    library.scan(brain["id"])
    doc = next(doc for doc in library.entities("documents") if doc["path"] == "legacy.txt")
    assert doc["extraction"] == "failed"
    receipt = wait_task(
        library,
        library.start_task(
            "encoding", {"brainId": brain["id"], "docId": doc["id"], "encoding": "windows-1252"}
        ),
    )
    assert receipt["state"] == "done"
    assert "Café evidence" in library.entity("documents", doc["id"])["blocks"][0]["text"]


def test_saved_search_jobs_honor_scope_budgets_and_review(library, tmp_path):
    brain, _ = opened(library, tmp_path)
    allow_research(library, brain)
    for name in ("a", "b", "c"):
        (Path(brain["root"]) / "Inbox").mkdir(exist_ok=True)
        (Path(brain["root"]) / f"Inbox/{name}.md").write_text(f"# {name}\n\nNew evidence {name}.")
    library.scan(brain["id"])
    job = {
        "id": "job-budget",
        "brainId": brain["id"],
        "name": "Budgeted summaries",
        "recipe": "summarize-new",
        "scope": "Inbox folder",
        "enabled": False,
        "trigger": {"kind": "manual"},
        "destination": "Summaries",
        "limits": {"documents": 3, "turns": 2},
        "review": True,
        "provider": {"id": "codex"},
    }
    library.mutate({"kind": "jobs", "id": job["id"], "item": job})
    result = library.run_job({"jobId": job["id"]}, threading.Event(), lambda _: None)
    assert len(result["outputs"]) == 2
    reports = library.entities("reports")
    assert len(reports) == 2 and all(report["draft"] for report in reports)
    assert all(report["artifactPath"].startswith("Summaries/") for report in reports)
    result = library.run_job({"jobId": job["id"]}, threading.Event(), lambda _: None)
    assert len(result["outputs"]) == 1
    assert library.run_job({"jobId": job["id"]}, threading.Event(), lambda _: None)["skipped"]


def test_local_api_profile_can_read_private_brain_but_remote_profile_cannot(library, tmp_path):
    from gofer.core.provider_profiles import ProviderProfile, save_provider_profiles

    brain, doc = opened(library, tmp_path)
    save_provider_profiles(
        {
            "local": ProviderProfile(
                name="local",
                subscription="openai_api",
                api_base_url="http://127.0.0.1:11434/v1",
                model="local-model",
            )
        },
        tmp_path / "app",
    )
    library.generate = cited_output
    result = library.report(
        {
            "brainId": brain["id"],
            "docIds": [doc["id"]],
            "question": "Finding?",
            "provider": {"id": "openai_api", "profile": "local"},
        },
        threading.Event(),
        lambda _: None,
    )
    assert result["citations"] == 1
    with pytest.raises(ValueError, match="cloud processing"):
        library.report(
            {
                "brainId": brain["id"],
                "docIds": [doc["id"]],
                "question": "Finding?",
                "provider": {"id": "codex"},
            },
            threading.Event(),
            lambda _: None,
        )


def test_saved_search_job_uses_semantic_results_from_its_selected_brains(
    library, tmp_path, monkeypatch
):
    brain, _ = opened(library, tmp_path)
    other_root = tmp_path / "semantic-brain"
    other_root.mkdir()
    (other_root / "source.md").write_text("# Finding\n\nA differently worded observation.")
    other = library.open({"root": str(other_root)})
    for task in library.snapshot()["tasks"]:
        wait_task(library, task)
    allow_research(library, brain)
    allow_research(library, other)
    other = library.brain(other["id"])
    other["settings"]["semantic"] = True
    library.put_brain(other)
    doc = library.entities("documents", other["id"])[0]
    library.mutate(
        {
            "kind": "savedSearches",
            "id": "meaning",
            "item": {
                "brainId": brain["id"],
                "name": "Related sources",
                "text": "alternate language",
                "mode": "semantic",
                "filters": {"brains": [other["id"]]},
            },
        }
    )
    library.mutate(
        {
            "kind": "jobs",
            "id": "semantic-job",
            "item": {
                "brainId": brain["id"],
                "name": "Digest",
                "recipe": "digest",
                "scope": "Saved search: Related sources",
                "enabled": False,
                "trigger": {"kind": "manual"},
                "destination": "Summaries",
                "limits": {"documents": 2, "turns": 1},
                "review": True,
                "provider": {"id": "codex"},
            },
        }
    )

    def related(params):
        assert params["mode"] == "semantic" and params["brainIds"] == [other["id"]]
        return {"hits": [{"docId": doc["id"]}]}

    monkeypatch.setattr(library, "search", related)
    result = library.run_job({"jobId": "semantic-job"}, threading.Event(), lambda _: None)
    report = library.entity("reports", result["reportId"])
    assert report["sourceVersions"] == [{"docId": doc["id"], "version": doc["version"]}]


def test_comparisons_use_quotes_and_do_not_overwrite_collection_edits(library, tmp_path):
    brain, doc = opened(library, tmp_path)
    allow_research(library, brain)
    (Path(brain["root"]) / "second.md").write_text("# Other\n\nA different observation.")
    library.scan(brain["id"])
    second = next(item for item in library.entities("documents") if item["id"] != doc["id"])
    collection = {
        "id": "collection",
        "brainId": brain["id"],
        "name": "Research",
        "brief": "Compare",
        "sections": [],
    }
    library.mutate({"kind": "collections", "id": collection["id"], "item": collection})

    def compare_output(prompt, provider, cancel):
        sources = json.loads(prompt.split("Evidence:\n")[1])
        cells = {
            item["docId"]: {"text": item["text"], "quote": item["text"], "evidence": item["id"]}
            for item in sources
        }
        current = library.entity("collections", collection["id"])
        library.mutate(
            {
                "kind": "collections",
                "id": collection["id"],
                "item": {**current, "brief": "Edited during comparison"},
            }
        )
        return {"rows": [{"aspect": "Observation", "cells": cells}], "conflicts": []}

    library.generate = compare_output
    library.compare(
        {
            "collectionId": collection["id"],
            "docIds": [doc["id"], second["id"]],
            "question": "Observations?",
            "review": True,
        },
        threading.Event(),
    )
    current = library.entity("collections", collection["id"])
    assert current["brief"] == "Edited during comparison"
    assert current["comparisonDraft"]["rows"][0]["cells"][doc["id"]]["version"] == doc["version"]


def test_bibliography_roundtrip_keeps_doi_and_dates(library, tmp_path):
    from gofer.ui.brain_bibliography import bibliography_operation, records

    brain, _ = opened(library, tmp_path)
    path = tmp_path / "references.json"
    path.write_text(
        json.dumps(
            [
                {
                    "id": "reference",
                    "title": "Evidence study",
                    "DOI": "10.123/test",
                    "issued": {"date-parts": [[2026, 3, 14]]},
                    "author": [{"family": "Researcher"}],
                }
            ]
        )
    )
    result = bibliography_operation(library, {"brainId": brain["id"], "path": str(path)})
    assert result["records"] == 1
    doc = next(item for item in library.entities("documents") if item["title"] == "Evidence study")
    assert doc["source"]["doi"] == "10.123/test"
    assert doc["dates"]["published"] == "2026-03-14"
    for suffix in (".ris", ".bib", ".json"):
        destination = tmp_path / ("exported-references" + suffix)
        bibliography_operation(
            library, {"brainId": brain["id"], "path": str(destination), "export": True}
        )
        exported = records(destination.read_text(), suffix)
        reference = next(item for item in exported if item.get("DOI") == "10.123/test")
        assert reference["issued"]["date-parts"] == [[2026, 3, 14]]


def test_link_repair_writes_relative_markdown_targets(library, tmp_path):
    brain, _ = opened(library, tmp_path)
    root = Path(brain["root"])
    (root / "Nested").mkdir()
    (root / "Nested/source.md").write_text(
        "# Source\n\nSee [target](missing.md), [[missing.md|Alias]] and [[missing.md#Heading]]."
    )
    (root / "target note.md").write_text("# Target\n\nAvailable evidence.")
    library.scan(brain["id"])
    source = next(doc for doc in library.entities("documents") if doc["path"] == "Nested/source.md")
    receipt = wait_task(
        library,
        library.start_task(
            "repair-link",
            {
                "brainId": brain["id"],
                "docId": source["id"],
                "old": "missing.md",
                "target": "target note.md",
            },
        ),
    )
    assert receipt["state"] == "done"
    assert "../target%20note.md" in (root / "Nested/source.md").read_text()
    assert "[[target note.md|Alias]]" in (root / "Nested/source.md").read_text()
    assert "[[target note.md#Heading]]" in (root / "Nested/source.md").read_text()
    current = library.entity("documents", source["id"])
    assert current["cites"]


def test_cross_brain_reports_respect_privacy_and_export_source_evidence(library, tmp_path):
    brain, _ = opened(library, tmp_path)
    other_root = tmp_path / "other"
    other_root.mkdir()
    (other_root / "source.md").write_text("# Outside source\n\nEvidence from another opened brain.")
    other = library.open({"root": str(other_root)})
    for task in library.snapshot()["tasks"]:
        wait_task(library, task)
    source = library.entities("documents", other["id"])[0]
    allow_research(library, brain)
    params = {"brainId": brain["id"], "docIds": [source["id"]], "question": "What evidence?"}
    with pytest.raises(ValueError, match="cloud processing"):
        library.report(params, threading.Event(), lambda _: None)
    allow_research(library, other)
    result = library.report(params, threading.Event(), lambda _: None)
    assert library.entity("reports", result["reportId"])["citations"][0]["state"] == "unreviewed"
    destination = tmp_path / "multi.zip"
    library.export_package(
        {"brainId": brain["id"], "destination": str(destination), "snapshots": False},
        threading.Event(),
        lambda _: None,
    )
    manifest, _ = checked_archive(destination)
    foreign = next(doc for doc in manifest["library"]["documents"] if doc["id"] == source["id"])
    assert foreign["brainId"] == brain["id"]
    assert foreign["path"].startswith("Sources/")
    library.request("remove", {"id": other["id"]})
    closed_export = tmp_path / "closed-source.zip"
    library.export_package(
        {"brainId": brain["id"], "destination": str(closed_export)},
        threading.Event(),
        lambda _: None,
    )
    retained, _ = checked_archive(closed_export)
    snapshot = next(doc for doc in retained["library"]["documents"] if doc["id"] == source["id"])
    assert snapshot["missing"] and snapshot["blocks"] == source["blocks"]


def test_curated_old_versions_survive_export_without_optional_history(library, tmp_path):
    brain, doc = opened(library, tmp_path)
    block = doc["blocks"][-1]
    library.mutate(
        {
            "kind": "citations",
            "id": "pinned",
            "item": {
                "brainId": brain["id"],
                "docId": doc["id"],
                "blockId": block["id"],
                "quote": "exact evidence",
            },
        }
    )
    (Path(brain["root"]) / doc["path"]).write_text("# Changed\n\nThe new text.")
    library.scan(brain["id"])
    destination = tmp_path / "pinned.zip"
    library.export_package(
        {"brainId": brain["id"], "destination": str(destination), "snapshots": False},
        threading.Event(),
        lambda _: None,
    )
    manifest, _ = checked_archive(destination)
    assert manifest["library"]["documents"][0]["versions"][0]["hash"] == doc["version"]


def test_semantic_search_uses_real_stored_vectors_and_excludes_old_models(
    library, tmp_path, monkeypatch
):
    brain, doc = opened(library, tmp_path)
    current = library.brain(brain["id"])
    current["settings"]["semantic"] = True
    library.put_brain(current)
    monkeypatch.setattr(library, "_embedding", lambda *args, **kwargs: [1.0, 0.0])
    library.embed(brain["id"], threading.Event())
    result = library.search(
        {"brainId": brain["id"], "query": "different wording", "mode": "semantic"}
    )
    assert result["hits"] and result["hits"][0]["docId"] == doc["id"]
    with library.connect() as db:
        db.execute("UPDATE vectors SET version='other-model'")
    assert (
        library.search({"brainId": brain["id"], "query": "different wording", "mode": "semantic"})[
            "hits"
        ]
        == []
    )


def test_cloud_embedding_uses_profile_and_validates_provider_response(
    library, tmp_path, monkeypatch
):
    from gofer.core.http import HttpResponse, UrllibHttpClient
    from gofer.core.provider_profiles import ProviderProfile, save_provider_profiles
    from gofer.ui.brain_embeddings import cloud_embedding

    save_provider_profiles(
        {
            "embedding": ProviderProfile(
                name="embedding",
                subscription="openai_api",
                api_key_env="BRAIN_TEST_KEY",
                api_base_url="https://example.org/v1",
            )
        },
        tmp_path / "app",
    )
    monkeypatch.setenv("BRAIN_TEST_KEY", "test-key")
    calls = []

    async def send(self, request):
        calls.append(request)
        return HttpResponse(status=200, headers={}, body=b'{"data":[{"embedding":[3,4]}]}')

    monkeypatch.setattr(UrllibHttpClient, "send", send)
    settings = {
        "allowCloud": True,
        "embeddingProfile": "embedding",
        "embeddingModel": "test-embedding",
    }
    vector = cloud_embedding("evidence", settings, tmp_path / "app", threading.Event())
    assert vector == [0.6, 0.8]
    assert calls[0].url == "https://example.org/v1/embeddings"
    assert json.loads(calls[0].body)["model"] == "test-embedding"
    with pytest.raises(ValueError, match="disabled"):
        cloud_embedding(
            "evidence", {**settings, "allowCloud": False}, tmp_path / "app", threading.Event()
        )
    assert len(calls) == 1


def test_pdf_extraction_keeps_page_locators():
    import io

    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=300)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
    )
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 72 72 Td (PDF evidence text) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    value = extract(output.getvalue(), ".pdf")
    assert value["extraction"] == "ready"
    assert value["blocks"][0]["page"] == 1
    assert "PDF evidence text" in value["blocks"][0]["text"]


def test_registered_mcp_reads_only_selected_brains_and_rechecks_privacy(
    library, tmp_path, monkeypatch
):
    from gofer.ui import second_brain

    brain, doc = opened(library, tmp_path)
    allow_research(library, brain)

    def serve(call, definitions, rules, name):
        assert brain["id"] in rules
        assert call("search", {"query": "evidence"})[0]["documentId"] == doc["id"]
        assert call("read_note", {"documentId": doc["id"]})["blocks"] == doc["blocks"]
        saved = call(
            "save_note",
            {
                "path": "Reports/thread-note.md",
                "content": "# Thread report\n\nThe index preserves exact evidence.",
                "format": "md",
                "title": "Thread report",
                "evidence": [
                    {
                        "docId": doc["id"],
                        "version": doc["version"],
                        "blockId": next(
                            block["id"]
                            for block in doc["blocks"]
                            if "exact evidence" in block["text"]
                        ),
                        "quote": "The index preserves exact evidence.",
                    }
                ],
            },
        )
        assert Path(saved["path"]).is_file()
        report = library.entities("reports")[0]
        assert report["external"] and report["format"] == "md"
        assert report["citations"][0]["state"] == "unreviewed"
        generated = next(
            item
            for item in library.entities("documents")
            if item["path"] == "Reports/thread-note.md"
        )
        assert generated["origin"] == "generated"
        with pytest.raises(ValueError, match="not selected"):
            call("search", {"brainId": "not-selected"})
        library.update_brain({"id": brain["id"], "patch": {"settings": {"allowCloud": False}}})
        with pytest.raises(ValueError, match="revoked"):
            call("read_note", {"documentId": doc["id"]})

    monkeypatch.setattr(second_brain, "serve_stdio", serve)
    second_brain.serve_registered_brains(tmp_path / "app", [brain["id"]], "html", "auto")


def test_document_export_is_plain_zip_and_html_preview_blocks_navigation(library, tmp_path):
    from gofer.ui.brain_documents import preview_html

    brain, _ = opened(library, tmp_path)
    (Path(brain["root"]) / "attachment.png").write_bytes(b"attached image bytes")
    destination = tmp_path / "documents.zip"
    library.export_package(
        {"brainId": brain["id"], "destination": str(destination), "purpose": "documents"},
        threading.Event(),
        lambda _: None,
    )
    with zipfile.ZipFile(destination) as archive:
        assert set(archive.namelist()) == {"notes.md", "attachment.png"}
        assert "exact evidence" in archive.read("notes.md").decode()
    html = preview_html(
        '<html><head><meta http-equiv="refresh" content="0;url=https://example.org">'
        '<style>body{color:teal}</style></head><body onload="alert(1)">'
        '<script>alert(1)</script><a href="https://example.org">Source</a>'
        "<h1>Report</h1></body></html>"
    )
    assert "http-equiv" not in html and "onload" not in html and "<script" not in html
    assert 'href="https' not in html and "color:teal" in html and "Report" in html


def test_saved_job_search_keeps_location_dates_and_annotations(library, tmp_path):
    from gofer.ui.brain_queries import saved_matches

    brain, doc = opened(library, tmp_path)
    doc["dates"]["modified"] = "2026-06-15T12:00:00Z"
    doc["reading"]["annotations"] = 1
    scope = {"brains": [brain], "collections": [], "reports": [], "citations": []}
    saved = {
        "text": "Durable",
        "searchIn": "title",
        "match": "phrase",
        "filters": {"authors": ["ad"], "from": "2026", "to": "2026", "reading": ["annotated"]},
    }
    assert saved_matches(doc, saved, scope)
    assert not saved_matches(doc, {**saved, "searchIn": "content"}, scope)
    assert saved_matches(doc, {"text": f'brain:"{brain["name"]}"'}, scope)


def test_failed_portable_open_rolls_back_registry_and_app_metadata_can_reopen(
    library, tmp_path, monkeypatch
):
    root = tmp_path / "readonly"
    root.mkdir()
    portable = library._portable

    def denied(brain_id):
        raise PermissionError("Read-only folder")

    monkeypatch.setattr(library, "_portable", denied)
    with pytest.raises(PermissionError):
        library.open({"root": str(root)})
    assert library.brains() == []
    monkeypatch.setattr(library, "_portable", portable)
    brain = library.open({"root": str(root), "metadata": "app"})
    assert brain["settings"]["metadata"] == "app"
    assert not (root / ".raticode/brain.json").exists()


@pytest.mark.parametrize("provider", ["grok", "antigravity"])
def test_native_research_uses_selected_model_effort_and_cancellation(
    library, monkeypatch, provider
):
    from gofer.subscriptions import acp_providers, antigravity

    cancel = threading.Event()
    calls = []

    async def events(*args, **kwargs):
        calls.append((args, kwargs))
        assert kwargs["cwd"].name.startswith("raticode-research-")
        assert kwargs["model"] == "chosen-model" and kwargs["effort"] == "high"
        assert kwargs["permission_mode"] == "cli-managed"
        assert kwargs["cancel_event"] is cancel
        assert not kwargs["resources"].shell and not kwargs["resources"].web
        assert not kwargs["resources"].mcpServers
        yield {
            "type": "final",
            "message": {"body": '{"claims":[]}'},
            "exitCode": 0,
            "usage": {"model": "reported-model"},
        }

    monkeypatch.setattr(acp_providers, "stream_acp", events)
    monkeypatch.setattr(antigravity, "stream_antigravity", events)
    result = library._provider_generate(
        "Evidence only", {"id": provider, "model": "chosen-model", "effort": "high"}, cancel
    )
    assert result == {"claims": [], "modelReported": "reported-model"}
    assert len(calls) == 1


def test_research_default_and_job_selection_survive_reload(library, tmp_path):
    brain, doc = opened(library, tmp_path)
    provider = {"id": "opencode", "model": "chosen-model", "effort": "high", "profile": None}
    library.update_brain({"id": brain["id"], "patch": {"settings": {"researchProvider": provider}}})
    job = {
        "id": "provider-job",
        "brainId": brain["id"],
        "name": "Selected provider",
        "recipe": "summarize-new",
        "enabled": False,
        "scope": "Inbox folder",
        "trigger": {"kind": "manual"},
        "limits": {"documents": 2, "turns": 1},
        "destination": "Summaries",
        "provider": provider,
    }
    library.mutate({"kind": "jobs", "item": job, "id": job["id"]})
    assert library.entity("jobs", job["id"])["provider"] == provider
    reopened = BrainLibrary(library.folder.parent, start=False)
    try:
        assert reopened.brain(brain["id"])["settings"]["researchProvider"] == provider
        assert reopened.entity("jobs", job["id"])["provider"] == provider
    finally:
        reopened.close()


@pytest.mark.parametrize(
    "selection",
    ["codex", {"id": "codex", "env": {"TOKEN": "secret"}}, {"id": "codex", "effort": 3}],
)
def test_research_defaults_reject_unknown_or_invalid_fields(library, tmp_path, selection):
    brain, _ = opened(library, tmp_path)
    with pytest.raises(ValueError, match="Invalid research provider"):
        library.update_brain(
            {"id": brain["id"], "patch": {"settings": {"researchProvider": selection}}}
        )


@pytest.mark.parametrize("template", ["summary", "brief"])
def test_manual_research_authorizes_only_that_request(library, tmp_path, template):
    brain, doc = opened(library, tmp_path)
    (Path(brain["root"]) / "unselected.md").write_text("Never send this unselected source.")
    library.scan(brain["id"])
    calls = []

    def generate(prompt, provider, cancel):
        calls.append(prompt)
        assert "Never send this unselected source" not in prompt
        return cited_output(prompt, provider, cancel)

    library.generate = generate
    task = library.start_task(
        "report",
        {
            "brainId": brain["id"],
            "docIds": [doc["id"]],
            "question": "Selected evidence?",
            "provider": {"id": "cursor"},
            "template": template,
            "allowCloudForTask": True,
        },
    )
    receipt = wait_task(library, task)
    assert receipt["state"] == "done", receipt.get("error")
    assert receipt["request"]["allowCloudForTask"] is True
    assert len(calls) == 1
    assert library.brain(brain["id"])["settings"]["allowCloud"] is False
    with pytest.raises(ValueError, match="cloud processing"):
        library.report(
            {
                "brainId": brain["id"],
                "docIds": [doc["id"]],
                "question": "Unapproved background research?",
                "provider": {"id": "cursor"},
            },
            threading.Event(),
            lambda _: None,
        )
    assert len(calls) == 1


def test_manual_consent_does_not_override_brain_availability(library, tmp_path):
    brain, doc = opened(library, tmp_path)
    Path(brain["root"]).rename(tmp_path / "unavailable")
    library.generate = lambda *args: pytest.fail("Must not invoke the provider")
    with pytest.raises(ValueError, match="unavailable"):
        library.report(
            {
                "brainId": brain["id"],
                "docIds": [doc["id"]],
                "question": "Evidence?",
                "provider": {"id": "cursor"},
                "allowCloudForTask": True,
            },
            threading.Event(),
            lambda _: None,
        )
