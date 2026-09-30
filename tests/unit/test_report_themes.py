from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import pytest

from gofer.ui.chat import ChatProviderError, build_chat_prompt
from gofer.ui.report_theme_generation import generate_report_theme, validate_theme_draft
from gofer.ui.second_brain import SecondBrain, with_second_brain


@pytest.mark.parametrize("brain_enabled", [False, True])
@pytest.mark.parametrize("theme_enabled", [False, True])
def test_report_theme_and_knowledge_are_independent(
    tmp_path: Path, brain_enabled: bool, theme_enabled: bool
) -> None:
    workflow = {
        "remSecondBrain": {"enabled": brain_enabled, "root": str(tmp_path), "format": "html"},
        "remReportTheme": {"enabled": theme_enabled, "theme": "blueprint"},
    }
    prompt = build_chat_prompt("codex", "cli-default", [], workflow)
    assert ("Knowledge root:" in prompt) is brain_enabled
    assert ("Blueprint:" in prompt) is theme_enabled
    assert "System: design" not in prompt
    if brain_enabled:
        configured = with_second_brain(workflow, Path("/trusted/gof"))
        assert configured is not None
        assert configured["remResources"]["mcpServers"][0]["args"][-1] == "none"
        assert "design direction" not in SecondBrain(tmp_path, "html", "none").call("rules", {})


def test_custom_theme_supplies_reusable_guidance_without_demo_markup() -> None:
    prompt = build_chat_prompt(
        "codex",
        "cli-default",
        [],
        {
            "remReportTheme": {"theme": "custom-ocean", "instructions": "Ocean ink #123456"},
        },
    )
    assert "Ocean ink #123456" in prompt
    assert "Knowledge root:" not in prompt


@pytest.mark.parametrize(
    "text", ["no JSON", "[]", "{}", '{"label":"A","instructions":"B","html":"<p>C</p>"}']
)
def test_rejects_incomplete_demo(text: str) -> None:
    with pytest.raises(ChatProviderError):
        validate_theme_draft(text)


def test_accepts_fenced_theme_draft() -> None:
    draft = {
        "label": "Ocean",
        "instructions": "Use navy ink.",
        "html": "<html><body>Demo</body></html>",
    }
    assert validate_theme_draft("```json\n" + json.dumps(draft) + "\n```") == draft


@pytest.mark.asyncio
async def test_theme_generation_uses_selection_and_isolated_screenshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    draft = {
        "label": "Ocean",
        "instructions": "Use navy ink.",
        "html": "<html><body>Demo</body></html>",
    }
    roots = []

    async def fake_chat(**kwargs: Any) -> AsyncGenerator[dict[str, Any], None]:
        roots.append(kwargs["working_dir"])
        assert kwargs["provider"] == "codex"
        assert kwargs["model"] == "chosen-model"
        assert kwargs["effort"] == "high"
        assert kwargs["workflow"]["remResources"] == {"shell": False, "web": False}
        assert "remSecondBrain" not in kwargs["workflow"]
        attachment = kwargs["messages"][0]["attachments"][0]
        assert (
            kwargs["data_dir"] / "chat-attachments/theme-preview" / attachment["storageName"]
        ).read_bytes() == b"image"
        yield {"type": "final", "message": {"body": json.dumps(draft)}}

    monkeypatch.setattr("gofer.ui.report_theme_generation.stream_workflow_chat", fake_chat)
    result = await generate_report_theme(
        {
            "provider": "codex",
            "model": "chosen-model",
            "effort": "high",
            "screenshot": {"name": "report.png", "type": "image/png", "data": "aW1hZ2U="},
        }
    )
    assert result == draft
    assert not roots[0].exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {},
        {"description": "x" * 100001},
        {"provider": "grok", "screenshot": {"type": "image/png"}},
        {"screenshot": {"type": "text/html"}},
    ],
)
async def test_generation_rejects_invalid_requests_before_running_provider(
    body: dict[str, Any],
) -> None:
    with pytest.raises(ValueError):
        await generate_report_theme(body)


@pytest.mark.parametrize("provider", ["codex", "claude_code"])
@pytest.mark.parametrize("blocks", [False, True])
def test_long_theme_survives_provider_final_message(provider: str, blocks: bool) -> None:
    from gofer.ui.chat import _provider_final_message, _trace_text

    draft = {
        "label": "Ocean",
        "instructions": "Navy ink.",
        "html": "<html><body>" + "<p>Illustrative findings</p>" * 700 + "</body></html>",
    }
    answer = json.dumps(draft)
    if provider == "codex":
        item = {
            "type": "agent_message",
            "content" if blocks else "text": [{"type": "text", "text": answer}]
            if blocks
            else answer,
        }
        payload = {"type": "item.completed", "item": item}
    else:
        payload = (
            {"message": {"content": [{"type": "text", "text": answer}]}}
            if blocks
            else {"type": "result", "result": answer}
        )
    result = _provider_final_message(provider, [payload])
    assert result == answer
    assert validate_theme_draft(result) == draft
    assert len(_trace_text(answer) or "") < len(answer)


@pytest.mark.asyncio
async def test_theme_documents_are_available_and_cleaned_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import base64

    text = "Use navy and generous margins. " * 1000
    roots = []

    async def fake_chat(**kwargs: Any) -> AsyncGenerator[dict[str, Any], None]:
        root = kwargs["data_dir"]
        roots.append(root)
        assert kwargs["workflow"]["remResources"] == {"shell": True, "web": False}
        stored = kwargs["messages"][0]["attachments"]
        assert len(stored) == 2
        assert (
            root / "chat-attachments/theme-preview" / stored[0]["storageName"]
        ).read_text() == text
        yield {
            "type": "final",
            "message": {
                "body": json.dumps(
                    {
                        "label": "Navy",
                        "instructions": "Navy ink",
                        "html": "<html><body>Demo</body></html>",
                    }
                )
            },
        }

    monkeypatch.setattr("gofer.ui.report_theme_generation.stream_workflow_chat", fake_chat)
    result = await generate_report_theme(
        {
            "attachments": [
                {
                    "name": "pasted-text.txt",
                    "type": "text/plain",
                    "data": base64.b64encode(text.encode()).decode(),
                },
                {"name": "reference.png", "type": "image/png", "data": "aW1hZ2U="},
            ]
        }
    )
    assert result["label"] == "Navy"
    assert not roots[0].exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "attachments", ["invalid", [None], [{}] * 6, [{"name": "bad.txt", "data": "not base64!"}]]
)
async def test_invalid_theme_attachments_fail_before_provider(attachments: Any) -> None:
    with pytest.raises(ValueError):
        await generate_report_theme({"description": "Ocean", "attachments": attachments})


@pytest.mark.asyncio
async def test_theme_progress_has_no_generation_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    draft = {"label": "Ocean", "instructions": "Navy", "html": "<html>Demo</html>"}
    progress: list[dict[str, Any]] = []

    async def fake_chat(**kwargs: Any) -> AsyncGenerator[dict[str, Any], None]:
        assert kwargs["include_agent_messages"] is True
        yield {"type": "thought", "text": "Choosing the palette"}
        assert progress == [{"type": "progress", "text": "Choosing the palette"}]
        yield {"type": "thought", "text": "Composing the sample"}
        yield {"type": "final", "message": {"body": json.dumps(draft)}}

    def no_deadline(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("Theme generation must not impose a deadline")

    monkeypatch.setattr(asyncio, "wait_for", no_deadline)
    monkeypatch.setattr("gofer.ui.report_theme_generation.stream_workflow_chat", fake_chat)
    assert await generate_report_theme({"description": "Ocean"}, progress.append) == draft
    assert progress[-1]["text"] == "Composing the sample"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["provider", "incomplete", "cancel"])
async def test_theme_stream_cleanup(monkeypatch: pytest.MonkeyPatch, failure: str) -> None:
    import asyncio

    roots = []
    closed = []

    async def fake_chat(**kwargs: Any) -> AsyncGenerator[dict[str, Any], None]:
        roots.append(kwargs["working_dir"])
        try:
            if failure == "cancel":
                raise asyncio.CancelledError()
            if failure == "provider":
                yield {"type": "error", "error": "Provider unavailable"}
        finally:
            closed.append(True)

    monkeypatch.setattr("gofer.ui.report_theme_generation.stream_workflow_chat", fake_chat)
    with pytest.raises(asyncio.CancelledError if failure == "cancel" else ChatProviderError):
        await generate_report_theme({"description": "Ocean"})
    assert closed == [True]
    assert not roots[0].exists()


def test_theme_stream_includes_codex_messages_only_when_requested() -> None:
    from gofer.ui.chat import _provider_trace_entries

    event = {
        "type": "item.completed",
        "item": {"type": "agent_message", "text": "Creating the layout"},
    }
    assert _provider_trace_entries("codex", event) == []
    assert (
        _provider_trace_entries("codex", event, include_agent_messages=True)[0]["body"]
        == "Creating the layout"
    )
