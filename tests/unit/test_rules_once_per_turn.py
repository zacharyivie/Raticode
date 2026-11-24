"""A turn carries the Second Brain and report rules once, never in its tool servers.

This test builds the prompt without the tool servers attached, the case where no rules
tool returns the report rules, so the prompt carries them itself.
test_report_guidance_once covers workers whose prompt points to a rules tool instead.
"""

from __future__ import annotations

import io
import json
import re
import time
from pathlib import Path
from typing import Any

import pytest

from gofer.ui import second_brain
from gofer.ui.brain_library import BrainLibrary
from gofer.ui.brain_summaries import SUMMARY_RULE
from gofer.ui.chat import build_chat_prompt
from gofer.ui.rem_home import RemHome
from gofer.ui.report_outputs import serve_reports
from gofer.ui.report_themes import report_theme_rules
from tests.unit.test_rem_home import home, recipe  # noqa: F401 - the home fixture
from tests.unit.test_swarm_tools import request

THEME = {"format": "html", "theme": "blueprint"}
SERVE_STDIO = second_brain.serve_stdio


def wait_idle(library: BrainLibrary) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if all(task["state"] != "running" for task in library.snapshot()["tasks"]):
            return
        time.sleep(0.02)
    pytest.fail("Second Brain tasks did not finish")


def stdio_replies(monkeypatch: pytest.MonkeyPatch, serve: Any) -> list[dict[str, Any]]:
    """Run a stdio tool server for an initialize and a tools/list request."""
    lines = "".join(
        json.dumps({"jsonrpc": "2.0", "id": number, "method": method}) + "\n"
        for number, method in enumerate(("initialize", "tools/list"), 1)
    )
    output = io.StringIO()

    def captured(call: Any, definitions: Any, instructions: str, name: str) -> None:
        SERVE_STDIO(
            call,
            definitions,
            instructions,
            name,
            input_stream=io.StringIO(lines),
            output_stream=output,
        )

    monkeypatch.setattr(second_brain, "serve_stdio", captured)
    serve()
    return [json.loads(line)["result"] for line in output.getvalue().splitlines()]


def served_text(replies: list[dict[str, Any]]) -> list[str]:
    """What a model reads from a server: its instructions and each tool description."""
    initialize, listed = replies
    return [initialize["instructions"], *(tool["description"] for tool in listed["tools"])]


def sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]


def test_second_brain_and_report_rules_reach_a_turn_once(
    home: RemHome,  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gofer.ui.rem_home_artifacts import artifact_tools

    app = tmp_path / "app"
    root = tmp_path / "brain"
    (root / "Notes").mkdir(parents=True)
    (root / "Notes" / "orbit.md").write_text("# Orbit\n\nThe orbit note.\n", encoding="utf-8")
    library = BrainLibrary(app, start=False)
    try:
        brain = library.open({"root": str(root)})
        wait_idle(library)
        library.update_brain({"id": brain["id"], "patch": {"settings": {"allowCloud": True}}})
    finally:
        library.close()
    workflow = {
        "remSecondBrain": {
            "enabled": True,
            "root": str(root),
            "brainIds": [brain["id"]],
            "libraryDataDir": str(app),
        },
        "remReportTheme": THEME,
    }
    prompt = build_chat_prompt("codex", "cli-default", [{"role": "user", "body": "Hi"}], workflow)

    # with_second_brain passes the theme as "none" when the turn has a report theme.
    brain_server = stdio_replies(
        monkeypatch,
        lambda: second_brain.serve_registered_brains(app, [brain["id"]], "html", "none"),
    )
    reports_root = tmp_path / "reports"
    reports_root.mkdir()
    reports_server = stdio_replies(monkeypatch, lambda: serve_reports(reports_root, "html"))

    task = home.create_task({"request_id": "create", **recipe(tmp_path, type="research")})["task"]
    run = task["active_run"]
    run["config"]["report_theme"] = THEME
    with artifact_tools(home, run, "execute") as granted:
        assert granted is not None
        listed = request(granted["url"], {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        research_tools = listed[1]["result"]["tools"]

    servers = [
        *served_text(brain_server),
        *served_text(reports_server),
        *(tool["description"] for tool in research_tools),
    ]
    # The servers only name their brains and folders.
    assert brain_server[0]["instructions"] == (
        f"Second Brain brains: {brain['name']} (brainId {brain['id']}). "
        + second_brain.RULES_TOOL_NOTE
    )
    assert len(sentences(reports_server[0]["instructions"])) <= 2
    research_save = next(tool for tool in research_tools if tool["name"] == "save_report")
    # What the tool does, its arguments and default format, then how its own summary
    # argument works. The full rules come from its rules tool.
    about, _, rest = research_save["description"].partition(SUMMARY_RULE)
    assert "Default format: standalone HTML" in about and not rest
    assert "Blueprint:" not in about and "Default report output" not in about

    rules = [
        # The full Second Brain rules, as the rules tool returns them.
        *sentences(second_brain.second_brain_rules(root, "html", "none")),
        *sentences(second_brain.REGISTERED_BRAIN_RULES),
        *sentences(report_theme_rules(THEME)),
    ]
    assert "Blueprint:" in prompt and brain["id"] in prompt
    everything = "\n".join([prompt, *servers])
    repeated = {sentence: everything.count(sentence) for sentence in set(rules)}
    assert {s: n for s, n in repeated.items() if n != 1} == {}
    assert "Call rules for these instructions" not in everything

    # The rules tool stays for older clients and still returns the full guidance.
    returned: list[str] = []

    def keep(handler: Any, definitions: Any, instructions: str, name: str) -> None:
        returned.append(handler("rules", {}))

    monkeypatch.setattr(second_brain, "serve_stdio", keep)
    second_brain.serve_registered_brains(app, [brain["id"]], "html", "none")
    [full] = returned
    assert second_brain.REGISTERED_BRAIN_RULES in full
    assert "Default report output: standalone HTML" in full


def brain_workflow(tmp_path: Path) -> dict[str, Any]:
    root = tmp_path / "brain"
    root.mkdir(exist_ok=True)
    return {
        "remSecondBrain": {
            "enabled": True,
            "root": str(root),
            "brainIds": ["brain-a", "brain-b"],
            "libraryDataDir": str(tmp_path / "app"),
        },
        "remManagedTask": {"unattended": True},
    }


TOOL_TEXT = [
    *sentences(second_brain.BRAIN_TOOL_USE_RULES),
    *sentences(second_brain.BRAIN_SERIES_RULES),
]


def test_brain_rules_leave_out_what_the_brain_tools_describe(tmp_path: Path) -> None:
    from gofer.ui.report_outputs import CITATION_RULES
    from gofer.ui.second_brain import with_second_brain

    workflow = with_second_brain(brain_workflow(tmp_path), tmp_path / "Raticode")
    assert workflow is not None
    prompt = build_chat_prompt("codex", "cli-default", [{"role": "user", "body": "Hi"}], workflow)

    assert "Selected brain IDs: brain-a, brain-b." in prompt
    assert CITATION_RULES in prompt and second_brain.BRAIN_CITING_RULES in prompt
    assert "when the reply has nextCursor, pass it as cursor" not in prompt
    assert [sentence for sentence in TOOL_TEXT if sentence in prompt] == []
    # The tool descriptions the server sends carry what the prompt left out.
    described = (
        second_brain.REGISTERED_SEARCH_DESCRIPTION + second_brain.REGISTERED_READ_DESCRIPTION
    )
    for word in ("nextCursor", "summaryOnly", "nextBlockId", "maxChars", "[blockId]", "series"):
        assert word in described


def test_brain_rules_stay_whole_without_the_brain_tools(tmp_path: Path) -> None:
    prompt = build_chat_prompt(
        "codex", "cli-default", [{"role": "user", "body": "Hi"}], brain_workflow(tmp_path)
    )

    assert "Selected brain IDs: brain-a, brain-b. " + second_brain.REGISTERED_BRAIN_RULES in prompt
    assert "when the reply has nextCursor, pass it as cursor" in prompt
