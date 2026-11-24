"""A stand-in for each provider CLI that checks what Raticode sends the way the real one does.

tests/unit/test_provider_wire_format.py installs it as claude, codex, cursor-agent,
copilot, opencode, grok and agy. Each copy:

- rejects an option the real CLI doesn't list (checked against the installed CLIs'
  --help on 2026-11-21: Claude Code 2.1.319, Codex 0.156.1, Cursor Agent 2026.11.05,
  Copilot 1.0.108, OpenCode 1.18.31, Grok 1.0.89, Antigravity 1.5.13);
- reads the prompt from where that CLI reads it (an argument, stdin, a prompt file or a
  stream-json message), and fails like the real CLI when the format is wrong;
- checks image files and image blocks, the system prompt file and MCP settings;
- fails like Codex 0.156 when its permission profile denies more than one file;
- answers with a JSON object saying what reached the model, in the CLI's own output
  format, and appends one record per call to the file named by FAKE_PROVIDER_LOG.

A prompt holding FAIL-WITH-COLOUR makes it fail with a red, terminal-coloured error.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import tomllib
from pathlib import Path
from typing import Any, NoReturn

# Each copy is installed under its CLI's binary name; runpy replaces argv[0] with this
# file's path, so the launcher passes the name along.
NAME = str(globals().get("LAUNCHED_AS") or os.path.basename(sys.argv[0]))

KNOWN = {
    "claude": {
        "--add-dir", "--agent", "--agents", "--allowedTools", "--allowed-tools",
        "--append-system-prompt", "--continue", "--dangerously-skip-permissions",
        "--disallowedTools", "--disallowed-tools", "--effort", "--fork-session",
        "--include-partial-messages", "--input-format", "--max-budget-usd", "--mcp-config",
        "--model", "--no-session-persistence", "--output-format", "--permission-mode",
        "--print", "--resume", "--session-id", "--settings", "--setting-sources",
        "--strict-mcp-config", "--system-prompt", "--system-prompt-file", "--tools",
        "--verbose", "--help", "--version",
    },
    "codex": {
        "--add-dir", "--cd", "--color", "--config", "--dangerously-bypass-approvals-and-sandbox",
        "--ephemeral", "--image", "--json", "--model", "--output-last-message",
        "--output-schema", "--profile", "--sandbox", "--skip-git-repo-check",
        "--ignore-user-config", "--help", "--version",
    },
    "cursor-agent": {
        "--add-dir", "--api-key", "--force", "--mode", "--model", "--output-format",
        "--plugin-dir", "--print", "--resume", "--sandbox", "--stream-partial-output",
        "--trust", "--workspace", "--disable-project-configs", "--allowed-tools",
        # Hidden, but listed by the installed headless parser.
        "--image", "--help", "--version",
    },
    "copilot": {
        "--add-dir", "--additional-mcp-config", "--allow-all-tools", "--allow-tool",
        "--attachment", "--available-tools", "--deny-tool", "--disable-builtin-mcps",
        "--excluded-tools", "--model", "--output-format", "--prompt", "--reasoning-effort",
        "--resume", "--session-id", "--silent", "--stream", "--help", "--version",
    },
    "opencode": {
        "--agent", "--continue", "--dir", "--file", "--fork", "--format", "--model",
        "--session", "--thinking", "--help", "--version",
    },
    "grok": {
        "--add-dir", "--allow", "--deny", "--disable-web-search", "--model",
        "--no-subagents", "--output-format", "--permission-mode", "--prompt-file",
        "--prompt-json", "--reasoning-effort", "--resume", "--tools", "--help", "--version",
    },
    "agy": {
        "--add-dir", "--agent", "--conversation", "--disable-slash-commands", "--effort",
        "--input-format", "--model", "--output-format", "--help", "--version",
    },
}  # fmt: skip
# Short options each CLI takes, and whether they take a value.
SHORT = {
    "claude": {"-p": True},
    "codex": {"-c": True, "-i": True, "-m": True, "-p": True, "-s": True, "-C": True},
    "cursor-agent": {"-p": True},
    "copilot": {"-p": True},
    "opencode": {"-m": True},
    "grok": {},
    "agy": {},
}
# Long options that take no value.
FLAGS = {
    "--print", "--verbose", "--include-partial-messages", "--strict-mcp-config",
    "--no-session-persistence", "--fork-session", "--continue", "--json", "--ephemeral",
    "--skip-git-repo-check", "--ignore-user-config", "--trust", "--force", "--silent",
    "--disable-project-configs", "--thinking", "--fork", "--no-subagents",
    "--disable-web-search", "--disable-slash-commands", "--dangerously-skip-permissions",
    "--dangerously-bypass-approvals-and-sandbox", "--stream-partial-output", "--help",
    "--version", "--allow-all-tools", "--disable-builtin-mcps",
}  # fmt: skip
# Options that take one or more values up to the next option (clap/commander arrays).
MULTI = {
    "claude": {"--allowedTools", "--allowed-tools", "--disallowedTools", "--add-dir",
               "--mcp-config", "--tools"},
    "codex": set(),
    "cursor-agent": set(),
    "copilot": set(),
    "opencode": set(),
    "grok": set(),
    "agy": set(),
}  # fmt: skip
IMAGE_MAGIC = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a")


class Refused(Exception):
    pass


def refuse(message: str) -> NoReturn:
    raise Refused(message)


def parse(args: list[str]) -> tuple[dict[str, list[str]], list[str]]:
    options: dict[str, list[str]] = {}
    positional: list[str] = []
    known = KNOWN[NAME]
    index = 0
    ended = False
    while index < len(args):
        arg = args[index]
        if ended or arg == "-" or not arg.startswith("-"):
            positional.append(arg)
            index += 1
            continue
        if arg == "--":
            ended = True
            index += 1
            continue
        if arg.startswith("--"):
            name, _, inline = arg.partition("=")
            if name not in known:
                refuse(f"error: unknown option '{name}'")
            if inline or "=" in arg:
                options.setdefault(name, []).append(inline)
                index += 1
                continue
            if name in FLAGS:
                options.setdefault(name, []).append("")
                index += 1
                continue
            if index + 1 >= len(args):
                refuse(f"error: option '{name}' argument missing")
            values = [args[index + 1]]
            index += 2
            if name in MULTI[NAME]:
                while index < len(args) and not args[index].startswith("-"):
                    values.append(args[index])
                    index += 1
            options.setdefault(name, []).extend(values)
            continue
        takes = SHORT[NAME].get(arg)
        if takes is None:
            refuse(f"error: unknown option '{arg}'")
        options.setdefault(arg, []).append(args[index + 1] if takes else "")
        index += 2 if takes else 1
    return options, positional


def last(options: dict[str, list[str]], *names: str) -> str | None:
    for name in names:
        if options.get(name):
            return options[name][-1]
    return None


def image_file(path: str) -> int:
    data = Path(path).read_bytes()
    if not data.startswith(IMAGE_MAGIC) and not (data[:4] == b"RIFF" and data[8:12] == b"WEBP"):
        refuse(f"{path} is not an image")
    return 1


def read_stdin() -> str:
    return sys.stdin.buffer.read().decode("utf-8") if not sys.stdin.isatty() else ""


def claude_stream_input(stdin: str) -> tuple[str, int]:
    """Claude Code's --input-format stream-json: every line a JSON user message."""
    texts: list[str] = []
    images = 0
    for line in stdin.split("\n"):
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except ValueError:
            refuse(
                f"Error parsing streaming input line (type=unknown, {len(line)} chars): SyntaxError"
            )
        if not isinstance(message, dict) or message.get("type") != "user":
            refuse("Error parsing streaming input line: expected a user message")
        content = (message.get("message") or {}).get("content")
        if isinstance(content, str):
            texts.append(content)
            continue
        if not isinstance(content, list):
            refuse("Error parsing streaming input line: content must be text or blocks")
        for block in content:
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                texts.append(block["text"])
            elif block.get("type") == "image":
                source = block.get("source") or {}
                if source.get("type") != "base64" or not str(
                    source.get("media_type", "")
                ).startswith("image/"):
                    refuse("Image blocks need base64 data and an image media type")
                image = base64.b64decode(source["data"], validate=True)
                if not image.startswith(IMAGE_MAGIC):
                    refuse("Image block data is not an image")
                images += 1
            else:
                refuse(f"Unsupported content block {block.get('type')!r}")
    if not texts:
        refuse("Error: Input must be provided either through stdin or as a prompt argument")
    return "".join(texts), images


def codex_profile(options: dict[str, list[str]]) -> None:
    """Codex 0.156 can't start when its profile denies a second file (one bwrap fd)."""
    for value in options.get("-c", []) + options.get("--config", []):
        key, _, raw = value.partition("=")
        if key.strip() != "permissions.raticode.filesystem":
            continue
        rules = tomllib.loads(f"v = {raw}")["v"]
        files = [p for p, access in rules.items() if access == "deny" and Path(p).is_file()]
        missing = [p for p, access in rules.items() if access == "deny" and not Path(p).exists()]
        if missing:
            refuse(f"bwrap: Can't write data to file {missing[0]}: Bad file descriptor")
        if len(files) > 1:
            refuse(f"bwrap: Can't write data to file {files[1]}: Bad file descriptor")


def help_text() -> str:
    lines = {
        "cursor-agent": "Start the Cursor Agent\n",
    }.get(NAME, "")
    return lines + "\n".join(sorted(KNOWN[NAME])) + "\n"


def run(args: list[str]) -> tuple[str, str, int, dict[str, Any]]:
    """What reached the model: the prompt, the system prompt and the image count."""
    if "--help" in args or args[-1:] == ["--help"]:
        return "help", "", 0, {}
    if "--version" in args:
        return "version", "", 0, {}
    record: dict[str, Any] = {"name": NAME, "args": args, "cwd": os.getcwd()}
    system = ""
    images = 0
    if NAME == "claude":
        options, positional = parse(args)
        if "--print" not in options:
            refuse("Raticode always runs Claude Code in print mode")
        if positional:
            refuse(f"error: too many arguments: {positional}")
        stdin = read_stdin()
        record["stdin"] = stdin
        if last(options, "--input-format") == "stream-json":
            prompt, images = claude_stream_input(stdin)
        elif last(options, "--input-format") not in (None, "text"):
            refuse("--input-format must be text or stream-json")
        else:
            prompt = last(options, "-p") or stdin
            if options.get("-p") and stdin.strip():
                refuse("A prompt argument and stdin were both given")
        rules = last(options, "--system-prompt-file")
        if rules is not None:
            system = Path(rules).read_text(encoding="utf-8")
        system += last(options, "--system-prompt", "--append-system-prompt") or ""
        for config in options.get("--mcp-config", []):
            json.loads(config if config.lstrip().startswith("{") else Path(config).read_text())
        if (tools := options.get("--tools")) is not None and tools != [""]:
            record["tools"] = tools
    elif NAME == "codex":
        if not args or args[0] != "exec":
            refuse("Raticode runs codex exec")
        rest = args[1:]
        options, positional = parse(rest)
        for sub in ("resume", "fork"):
            if positional[:1] == [sub]:
                positional = positional[2:]
        codex_profile(options)
        if len(positional) != 1:
            refuse(f"codex exec takes one prompt argument, got {positional}")
        stdin = read_stdin()
        record["stdin"] = stdin
        if positional[0] == "-":
            prompt = stdin
        else:
            if stdin.strip():
                refuse("A prompt argument and stdin were both given")
            prompt = positional[0]
        pointer = "Read the complete Raticode agent prompt from this file, then follow it exactly: "
        if prompt.startswith(pointer):
            # Workflow steps hand Codex a file; its model reads it with a tool.
            prompt = Path(prompt[len(pointer) :].strip()).read_text(encoding="utf-8")
            record["prompt_file"] = True
        for path in options.get("--image", []) + options.get("-i", []):
            images += image_file(path)
    elif NAME == "cursor-agent":
        options, positional = parse(args)
        if "--print" not in options:
            refuse("headless runs need --print")
        plugin = last(options, "--plugin-dir")
        if plugin is not None and not (Path(plugin) / ".cursor-plugin" / "plugin.json").is_file():
            refuse("--plugin-dir needs .cursor-plugin/plugin.json")
        stdin = read_stdin()
        record["stdin"] = stdin
        prompt = last(options, "-p") or " ".join(positional) or stdin
        for path in options.get("--image", []):
            images += image_file(path)
    elif NAME == "copilot":
        options, positional = parse(args)
        stdin = read_stdin()
        record["stdin"] = stdin
        prompt = last(options, "-p", "--prompt") or stdin
        if options.get("-p") and stdin.strip():
            refuse("A prompt argument and stdin were both given")
        for path in options.get("--attachment", []):
            images += image_file(path)
    elif NAME == "opencode":
        if not args or args[0] != "run":
            refuse("Raticode runs opencode run")
        options, positional = parse(args[1:])
        stdin = read_stdin()
        record["stdin"] = stdin
        prompt = " ".join(positional) or stdin
        if positional and stdin.strip():
            refuse("A prompt argument and stdin were both given")
        for path in options.get("--file", []):
            images += image_file(path)
        json.loads(os.environ.get("OPENCODE_CONFIG_CONTENT", "{}"))
        json.loads(os.environ.get("OPENCODE_PERMISSION", "{}"))
    elif NAME == "grok":
        options, positional = parse(args)
        if positional:
            refuse("grok headless mode takes its prompt from --prompt-file")
        if options.get("--prompt-file") and options.get("--prompt-json"):
            refuse("--prompt-file can't be used with --prompt-json")
        prompt_file = last(options, "--prompt-file")
        if prompt_file is None:
            refuse("grok headless mode needs --prompt-file")
        prompt = Path(prompt_file).read_text(encoding="utf-8")
    elif NAME == "agy":
        options, positional = parse(args)
        if last(options, "--input-format") != "stream-json":
            refuse("Raticode sends agy stream-json input")
        agent = last(options, "--agent")
        if agent is not None and not (Path(".agents/agents") / f"{agent}.md").is_file():
            refuse(f"agent {agent!r} isn't defined in this workspace")
        stdin = read_stdin()
        record["stdin"] = stdin
        lines = [line for line in stdin.split("\n") if line.strip()]
        if len(lines) != 1:
            refuse("agy reads one stream-json message")
        try:
            message = json.loads(lines[0])
        except ValueError:
            refuse("stream input line is not JSON")
        content = (message.get("message") or {}).get("content")
        if message.get("event") != "user" or not isinstance(content, str):
            refuse('stream input content block type "image" is not supported (only "text")')
        prompt = content
    else:  # pragma: no cover - the launcher only installs the names above
        refuse(f"unknown fake {NAME}")
    record.update(prompt=prompt, system=system, images=images)
    return prompt, system, images, record


def answer(prompt: str, system: str, images: int) -> str:
    return json.dumps(
        {
            "reply": "wire ok",
            "marker": "WIRE-MARKER" in prompt,
            "rules": "WIRE-RULES" in system + prompt,
            "images": images,
        }
    )


def emit(text: str) -> None:
    if NAME in {"claude", "grok"}:
        print(json.dumps({"type": "system", "subtype": "init", "session_id": "s-1"}))
        print(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "id": "m-1",
                        "role": "assistant",
                        "content": [{"type": "text", "text": text}],
                    },
                }
            )
        )
        print(
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "result": text,
                    "session_id": "s-1",
                    "usage": {"input_tokens": 3, "output_tokens": 2},
                }
            )
        )
    elif NAME == "codex":
        print(json.dumps({"type": "thread.started", "thread_id": "t-1"}))
        print(
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"id": "i-1", "type": "agent_message", "text": text},
                }
            )
        )
        print(
            json.dumps({"type": "turn.completed", "usage": {"input_tokens": 3, "output_tokens": 2}})
        )
    elif NAME == "cursor-agent":
        print(json.dumps({"type": "system", "subtype": "init", "session_id": "s-1"}))
        print(
            json.dumps(
                {
                    "type": "assistant",
                    "session_id": "s-1",
                    "message": {
                        "id": "m-1",
                        "role": "assistant",
                        "content": [{"type": "text", "text": text}],
                    },
                }
            )
        )
        print(
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "result": text,
                    "session_id": "s-1",
                }
            )
        )
    elif NAME == "copilot":
        if "--output-format" in sys.argv and sys.argv[sys.argv.index("--output-format") + 1] == (
            "json"
        ):
            print(json.dumps({"type": "session.start", "data": {"sessionId": "s-1"}}))
            print(
                json.dumps(
                    {
                        "type": "assistant.message",
                        "id": "e-1",
                        "data": {"messageId": "m-1", "content": text},
                    }
                )
            )
            print(json.dumps({"type": "session.idle", "data": {}}))
        else:
            print(text)
    elif NAME == "opencode":
        print(json.dumps({"type": "step_start", "sessionID": "s-1", "part": {"id": "p-0"}}))
        print(json.dumps({"type": "text", "sessionID": "s-1", "part": {"id": "p-1", "text": text}}))
        print(
            json.dumps(
                {
                    "type": "step_finish",
                    "sessionID": "s-1",
                    "part": {"id": "p-2", "messageID": "m-1", "tokens": {"input": 3, "output": 2}},
                }
            )
        )
    elif NAME == "agy":
        print(json.dumps({"event": "init", "conversation_id": "c-1"}))
        print(
            json.dumps(
                {
                    "event": "result",
                    "result": {"status": "SUCCESS", "response": text, "conversation_id": "c-1"},
                }
            )
        )


def main() -> None:
    args = sys.argv[1:]
    log = os.environ.get("FAKE_PROVIDER_LOG")
    record: dict[str, Any] = {"name": NAME, "args": args, "cwd": os.getcwd()}
    try:
        prompt, system, images, record = run(args)
    except Refused as exc:
        record["refused"] = str(exc)
        if log:
            with open(log, "a", encoding="utf-8") as out:
                out.write(json.dumps(record) + "\n")
        sys.stderr.write(f"\x1b[0m\x1b[31m{exc}\x1b[0m\n")
        sys.exit(1)
    if prompt == "help":
        print(help_text())
        return
    if prompt == "version":
        print(f"{NAME} 0.0.0-fake")
        return
    if log:
        with open(log, "a", encoding="utf-8") as out:
            out.write(json.dumps(record) + "\n")
    if "FAIL-WITH-COLOUR" in prompt:
        sys.stderr.write("\x1b[0m\x1b[31mError: the model is \x1b[1moverloaded\x1b[0m\n")
        sys.exit(1)
    emit(answer(prompt, system, images))


if __name__ == "__main__":
    main()
