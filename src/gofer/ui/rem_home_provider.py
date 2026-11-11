"""Tool-free structured conversation decisions, isolated from coding workspaces."""

from __future__ import annotations

import asyncio
import base64
import functools
import json
import os
import re
import subprocess
import tempfile
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gofer.core.agent import Agent, AgentConfig
from gofer.core.prompt_envelope import AgentResources, resource_cli_args
from gofer.core.provider_capabilities import (
    CLI_PROVIDERS,
    NO_IMAGE_ATTACHMENTS,
    resolve_provider_executable,
)
from gofer.core.provider_profiles import resolve_provider_settings
from gofer.subscriptions.antigravity import AntigravitySubscription
from gofer.subscriptions.base import Subscription
from gofer.subscriptions.claude_code import ClaudeCodeSubscription
from gofer.subscriptions.cli_providers import CliSubscription
from gofer.subscriptions.codex import CodexSubscription
from gofer.subscriptions.grok_headless import GrokConversationSubscription

REPLY_KEY = re.compile(r'"reply"\s*:\s*"')

# Image formats that every image-capable chat model reads, recognised by their first
# bytes rather than by the type or name the upload claimed.
IMAGE_SIGNATURES: tuple[tuple[bytes, str, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
    (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
    (b"GIF87a", "image/gif", ".gif"),
    (b"GIF89a", "image/gif", ".gif"),
)
# Claude's limit for one image; the other CLIs accept at least this much.
CHAT_IMAGE_LIMIT = 5 * 1024 * 1024
CHAT_IMAGE_BUDGET = 20 * 1024 * 1024
# How each provider's tool-free chat call receives images. Claude Code takes image
# blocks on stdin; the others attach image files with a command-line option.
IMAGE_OPTIONS = {"codex": "--image", "cursor": "--image", "copilot": "--attachment"}
CHAT_IMAGE_PROVIDERS = frozenset({"claude_code", *IMAGE_OPTIONS, "opencode"})
# Why a provider's chat call can't take images. Checked against the installed CLIs:
# agy 1.5.4 answers 'stream input content block type "image" is not supported (only
# "text")', and grok 1.0.77 takes images only through --prompt-json, a command-line
# argument that also has to hold the whole prompt and can't be used with --prompt-file.
NO_CHAT_IMAGES = {
    "antigravity": NO_IMAGE_ATTACHMENTS["antigravity"],
    "grok": "Grok can only receive images inside one command-line argument, which is "
    "too small for Rem's request",
}


@dataclass(frozen=True)
class ChatImage:
    """An image to show the chat model, read from a checked chat attachment."""

    name: str
    media_type: str
    suffix: str
    data: bytes


def image_kind(data: bytes) -> tuple[str, str] | None:
    """The media type and file suffix of a supported image, from its first bytes."""
    for signature, media_type, suffix in IMAGE_SIGNATURES:
        if data.startswith(signature):
            return media_type, suffix
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", ".webp"
    return None


def chat_image_refusal(provider: str) -> str | None:
    """Why this provider's chat replies can't see images, or None when they can."""
    if provider in CHAT_IMAGE_PROVIDERS:
        return None
    return NO_CHAT_IMAGES.get(provider, f"{provider} can't receive images in Raticode")


_ESCAPES = {"n": "\n", "t": "\t", "r": "", "b": "", "f": "", '"': '"', "\\": "\\", "/": "/"}


def draft_reply(text: str) -> str:
    """The part of the decision's reply string that has arrived so far.

    The model writes one JSON object; this reads its "reply" value up to the
    last complete character, so a half-received escape is never shown.
    """
    match = REPLY_KEY.search(text)
    if not match:
        return ""
    out: list[str] = []
    index = match.end()
    while index < len(text):
        char = text[index]
        if char == '"':
            break
        if char == "\\":
            if index + 1 >= len(text):
                break
            escape = text[index + 1]
            if escape == "u":
                digits = text[index + 2 : index + 6]
                if len(digits) < 4:
                    break
                try:
                    out.append(chr(int(digits, 16)))
                except ValueError:
                    break
                index += 6
                continue
            out.append(_ESCAPES.get(escape, escape))
            index += 2
            continue
        out.append(char)
        index += 1
    return "".join(out)


# Rem's chat prompts carry its memories, so they shouldn't stay behind in a
# provider CLI's saved sessions. Each option is used only when the installed
# CLI lists it, so an older CLI keeps working.
NO_SAVED_SESSION = {
    "claude_code": ((), "--no-session-persistence"),
    "codex": (("exec",), "--ephemeral"),
}


@functools.lru_cache(maxsize=16)
def _lists_option(
    executable: str, modified: float, subcommand: tuple[str, ...], option: str
) -> bool:
    try:
        result = subprocess.run(
            [executable, *subcommand, "--help"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return re.search(re.escape(option) + r"(?![\w-])", result.stdout + result.stderr) is not None


def no_saved_session(provider: str) -> list[str]:
    """The option that keeps this provider from saving Rem's chat session, if any."""
    known = NO_SAVED_SESSION.get(provider)
    if known is None:
        return []
    executable = resolve_provider_executable(provider)  # type: ignore[arg-type]
    if not executable:
        return []
    try:
        modified = os.stat(executable).st_mtime
    except OSError:
        return []
    subcommand, option = known
    return [option] if _lists_option(executable, modified, subcommand, option) else []


def claude_user_message(prompt: str, images: Sequence[ChatImage]) -> bytes:
    """One stream-json user message: each image with its label, then the prompt.

    Claude Code's --input-format stream-json reads this from stdin and gives the
    model the images as image content, the way a pasted screenshot arrives.
    """
    content: list[dict[str, Any]] = []
    for number, image in enumerate(images, start=1):
        content.append({"type": "text", "text": f"Image {number}: {image.name}"})
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": image.media_type,
                    "data": base64.b64encode(image.data).decode("ascii"),
                },
            }
        )
    content.append({"type": "text", "text": prompt})
    message = {"type": "user", "message": {"role": "user", "content": content}}
    return (json.dumps(message) + "\n").encode("utf-8")


class _RemClaude(ClaudeCodeSubscription):
    """Claude Code that can report answer text as it is written and receive images."""

    def __init__(
        self,
        on_text: Callable[[str], None] | None = None,
        images: Sequence[ChatImage] = (),
    ) -> None:
        super().__init__()
        self._on_text = on_text
        self._images = tuple(images)

    def _stdin_bytes(self, prompt: str) -> bytes:
        if not self._images:
            return super()._stdin_bytes(prompt)
        return claude_user_message(prompt, self._images)

    def _parse_provider_output(self, stdout: str, stderr: str) -> tuple[str, dict[str, object]]:
        if self._on_text is None:
            return super()._parse_provider_output(stdout, stderr)
        try:
            payload = json.loads(stdout)
        except ValueError:
            payload = None
        if isinstance(payload, dict) and payload.get("type") == "stream_event":
            event = payload.get("event") or {}
            delta = event.get("delta") or {} if isinstance(event, dict) else {}
            if (
                isinstance(delta, dict)
                and event.get("type") == "content_block_delta"
                and delta.get("type") == "text_delta"
            ):
                self._on_text(str(delta.get("text") or ""))
        return super()._parse_provider_output(stdout, stderr)


def generate_decision(
    prompt: str,
    config: dict[str, Any],
    cancel: threading.Event,
    data_dir: Path,
    on_reply: Callable[[str], None] | None = None,
    images: Sequence[ChatImage] = (),
) -> dict[str, Any]:
    """Ask the chat model for a decision. on_reply sees the reply text as it grows.

    Only Claude Code reports partial text; other providers answer all at once.
    """
    return parse_decision(generate_text(prompt, config, cancel, data_dir, on_reply, images=images))


def generate_text(
    prompt: str,
    config: dict[str, Any],
    cancel: threading.Event,
    data_dir: Path,
    on_reply: Callable[[str], None] | None = None,
    *,
    system_prompt: str = "",
    timeout: float = 180,
    images: Sequence[ChatImage] = (),
) -> str:
    """Run one tool-free, sessionless turn in an empty folder and return its text.

    Claude Code takes system_prompt in place of its own instructions, which keeps
    short calls short. Other providers read it at the top of the prompt. images are
    shown to the model; callers check chat_image_refusal first.
    """
    name = config.get("provider", "codex")
    if name not in CLI_PROVIDERS:
        raise ValueError(f"Unknown provider '{name}'")
    if images and (refusal := chat_image_refusal(name)):
        raise ValueError(refusal)
    settings = resolve_provider_settings(
        agent_subscription=name,
        agent_model=config.get("model") if config.get("model") != "cli-default" else None,
        agent_effort=config.get("effort") or None,
        profile_name=None,
        data_dir=data_dir,
    )
    if system_prompt and name != "claude_code":
        prompt = f"{system_prompt}\n\n{prompt}"
    with tempfile.TemporaryDirectory(prefix="raticode-rem-conversation-") as directory:
        root = Path(directory)
        # The CLIs that attach image files read these private copies in the empty
        # folder, never the user's own files, so no tool or folder grant is needed.
        image_paths: list[Path] = []
        if name != "claude_code":
            for number, image in enumerate(images, start=1):
                path = root / f"image-{number}{image.suffix}"
                path.write_bytes(image.data)
                image_paths.append(path)
        extra: list[str] = []
        subscription: Subscription
        if name == "codex":
            # Image options come first: --image takes several values, so an option
            # must follow it before the prompt argument.
            extra = [f"--image={path}" for path in image_paths]
            extra += resource_cli_args(name, AgentResources.none(), root)
            extra += [
                "-c",
                'approval_policy="never"',
                # resource_cli_args turns image viewing on with file reading.
                "-c",
                "tools.view_image=false",
                "-c",
                "features.plugins=false",
                "-c",
                "features.hooks=false",
                "-c",
                "features.goals=false",
                "-c",
                "features.skill_mcp_dependency_install=false",
                *no_saved_session(name),
            ]
            subscription = CodexSubscription()
            subscription.prompt_via_stdin = True
        elif name == "claude_code":
            extra = [
                "--tools",
                "",
                "--strict-mcp-config",
                "--mcp-config",
                '{"mcpServers":{}}',
                *no_saved_session(name),
            ]
            if system_prompt:
                extra += ["--system-prompt", system_prompt]
            if images:
                extra += ["--input-format", "stream-json"]
            grow: Callable[[str], None] | None = None
            if on_reply is not None:
                written: list[str] = []
                shown = [""]

                def grow(text: str) -> None:
                    written.append(text)
                    draft = draft_reply("".join(written))
                    if draft != shown[0]:
                        shown[0] = draft
                        on_reply(draft)

                extra.append("--include-partial-messages")
            subscription = (
                _RemClaude(grow, images) if grow is not None or images else ClaudeCodeSubscription()
            )
            subscription.prompt_via_stdin = True
        elif name == "antigravity":
            subscription = AntigravitySubscription(tool_free=True)
        elif name == "grok":
            subscription = GrokConversationSubscription()
        else:
            subscription = CliSubscription(
                name,
                resources=AgentResources.none(),
                read_only=True,
                tool_free=True,
                image_paths=image_paths,
            )
        settings = settings.model_copy(
            update={
                "tools": [],
                "mcp_servers": [],
                "extra_args": extra,
                "sandbox_mode": "read-only" if name == "codex" else None,
                "approval_mode": "dontAsk"
                if name == "claude_code"
                else "cli-managed"
                if name in {"antigravity", "grok"}
                else None,
            }
        )
        result = asyncio.run(
            Agent(
                AgentConfig(agent_id="Rem", subscription=name, working_dir=root), subscription
            ).run(
                prompt_override=prompt,
                provider_settings=settings,
                cancel_event=cancel,
                timeout=timeout,
                # Partial-message events repeat the text many times over.
                max_output_bytes=4_000_000 if on_reply is not None else 100000,
            )
        )
    if cancel.is_set():
        raise InterruptedError("Rem stopped")
    if not result.success:
        raise ValueError(result.message or result.output or "Rem could not reply")
    return result.message or result.output or ""


class MalformedDecision(ValueError):
    """The model replied, but not with the JSON object Rem asked for."""

    def __init__(self, text: str) -> None:
        super().__init__("Rem's reply wasn't in the expected format")
        self.text = text


# Top-level fields of the objects Rem asks its models for: decisions, recall requests,
# answers to workers, task updates and review verdicts.
ANSWER_KEYS = frozenset(
    {"reply", "recall", "next_message", "follow_up", "summary", "needs_user", "criteria"}
)
# Records a provider CLI writes about its own turn. When a turn ends without an answer,
# its raw output is all that's left, and none of these records is Rem's answer.
PROTOCOL_RECORDS = frozenset(
    {
        "system",
        "assistant",
        "user",
        "result",
        "stream_event",
        "thread.started",
        "turn.started",
        "turn.completed",
        "turn.failed",
        "item.started",
        "item.updated",
        "item.completed",
        "error",
    }
)


def parse_decision(text: str) -> dict[str, Any]:
    """Read the decision object, tolerating code fences or prose around it.

    Models sometimes explain themselves before or after the JSON, and that prose
    can quote a small JSON example of its own. An object with one of the fields Rem
    asked for wins over one without; provider protocol records never count. Any
    reply without such an object, including an empty one, raises MalformedDecision
    so the caller can ask again instead of showing a raw parser error.
    """
    stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        candidates = [json.loads(stripped)]
    except json.JSONDecodeError:
        candidates = []
        decoder = json.JSONDecoder()
        resume = 0
        for match in re.finditer(r"\{", stripped):
            # Objects nested inside one already read are part of it, not answers.
            if match.start() < resume:
                continue
            try:
                candidate, resume = decoder.raw_decode(stripped, match.start())
            except json.JSONDecodeError:
                continue
            candidates.append(candidate)
    objects = [
        c
        for c in candidates
        if isinstance(c, dict)
        and not (c.get("type") in PROTOCOL_RECORDS and not ANSWER_KEYS & c.keys())
    ]
    value = next((c for c in objects if ANSWER_KEYS & c.keys()), objects[0] if objects else None)
    if not isinstance(value, dict):
        raise MalformedDecision(text)
    return value
