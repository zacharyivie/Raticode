#!/usr/bin/env python3
"""Write the website's CLI command index from the CLI's own command tree.

website/docs/reference/cli-commands.md lists every visible ``Raticode`` command with
its summary, arguments and options, read from the Typer app, and labels each one with
its kind from docs/cli-parity.json. tests/unit/test_cli_parity.py fails when the page
and the CLI disagree, so a new command, option or changed help text reaches the
website in the same branch.

Usage:
    python3 scripts/generate-cli-reference.py          # rewrite the page
    python3 scripts/generate-cli-reference.py --check  # exit 1 if it is out of date
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "website/docs/reference/cli-commands.md"
MANIFEST = ROOT / "docs/cli-parity.json"

KIND_LABELS = {
    "backend": "Backend: needs a running Raticode backend",
    "offline": "Offline: works without a backend",
    "legacy": "Legacy: older TOML workflows",
}

_DESCRIPTION = (
    "Every Raticode CLI command with its arguments and options, generated from the CLI itself."
)
HEADER = "\n".join(
    [
        "---",
        "title: CLI command index",
        f"description: {_DESCRIPTION}",
        "pageClass: docs-layout",
        "---",
        "",
        "# CLI command index",
        "",
        "::: info Generated page",
        "`scripts/generate-cli-reference.py` writes this page from the CLI's command tree, and a "
        "test fails when the two differ. It describes the development branch: commands marked "
        "Backend need Raticode 0.5.3 or later. Run "
        "`Raticode <command> --help` to see what your installed version offers.",
        ":::",
        "",
        "Each command is labelled with its kind:",
        "",
        "- **Backend** commands send requests to a running backend. See "
        "[Headless CLI](./cli-headless.md), [Rem and tickets from the CLI](./cli-rem.md) and "
        "[talking to a running backend](./cli.md#talk-to-a-running-backend).",
        "- **Offline** commands work on files and local stores without a backend.",
        "- **Legacy** commands work only with older TOML workflows. For Rattish workflows use "
        "`Raticode rattish`.",
        "",
        "The [CLI reference](./cli.md) explains how the commands fit together.",
        "",
    ]
)

_SPECIAL = re.compile(r"([\\*\[\]<>{}|])")


def _escape(text: str) -> str:
    """Plain text that VitePress shows as written: no Markdown, HTML or Vue syntax."""
    parts = text.split("`")
    out = []
    for index, part in enumerate(parts):
        if index % 2 and index < len(parts) - 1 and "{{" not in part:
            out.append(f"`{part}`")
        else:
            escaped = _SPECIAL.sub(lambda m: f"&#{ord(m.group(1))};", part)
            out.append(escaped.replace("`", "&#96;"))
    return "".join(out)


def _summary(text: str | None) -> str:
    """The first paragraph of a help text, on one line."""
    if not text:
        return ""
    paragraph = re.split(r"\n\s*\n", text.strip().replace("\b", ""), maxsplit=1)[0]
    return " ".join(paragraph.split())


def _param_line(param: Any) -> str | None:
    # Typer may ship its own copy of click, so read parameters by duck type.
    if getattr(param, "hidden", False):
        return None
    help_text = _escape(_summary(getattr(param, "help", None)))
    if param.param_type_name == "argument":
        name = (param.human_readable_name or param.name or "").upper()
        required = "required" if param.required else "optional"
        many = ", repeatable" if param.nargs == -1 else ""
        line = f"- `{name}` ({required}{many})"
    else:
        names = [*param.opts, *param.secondary_opts]
        line = "- " + " / ".join(f"`{name}`" for name in names)
        if getattr(param, "is_flag", False) or getattr(param, "count", False):
            pass
        elif getattr(param.type, "choices", None):
            line += " " + " / ".join(f"`{choice}`" for choice in param.type.choices)
        elif param.type is not None:
            line += f" `{param.type.name.upper()}`"
        if param.required:
            line += " (required)"
        if param.multiple:
            line += " (repeatable)"
    return f"{line}: {help_text}" if help_text else line


def _leaves(command: Any, path: list[str], hidden: bool) -> list[tuple[list[str], Any]]:
    hidden = hidden or bool(getattr(command, "hidden", False))
    children = getattr(command, "commands", None)
    if isinstance(children, dict):
        found = []
        for name in sorted(children):
            found.extend(_leaves(children[name], [*path, name], hidden))
        return found
    return [] if hidden else [(path, command)]


def render() -> str:
    import typer

    from gofer.cli.main import app

    kinds: dict[str, str] = json.loads(MANIFEST.read_text(encoding="utf-8"))["commands"]
    root = typer.main.get_command(app)
    groups: dict[str, list[tuple[list[str], Any]]] = {}
    for path, command in _leaves(root, [], False):
        groups.setdefault(path[0] if len(path) > 1 else "", []).append((path, command))

    lines = [HEADER]
    for group in sorted(groups, key=lambda name: (name != "", name)):
        if group:
            lines.append(f"## Raticode {group}\n")
            group_help = _summary(getattr(root.commands[group], "help", None))  # type: ignore[attr-defined]
            if group_help:
                lines.append(_escape(group_help) + "\n")
        else:
            lines.append("## Top-level commands\n")
        for path, command in groups[group]:
            name = " ".join(path)
            lines.append(f"### Raticode {name}\n")
            kind = kinds.get(name, "")
            details = [f"*{KIND_LABELS[kind]}.*"] if kind in KIND_LABELS else []
            summary = _summary(command.help or command.short_help)
            if summary:
                details.append(_escape(summary))
            if details:
                lines.append(" ".join(details) + "\n")
            params = [line for line in map(_param_line, command.params) if line]
            if params:
                lines.append("\n".join(params) + "\n")
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="fail if the page is out of date")
    args = parser.parse_args(argv)
    sys.path.insert(0, str(ROOT / "src"))
    text = render()
    if args.check:
        current = PAGE.read_text(encoding="utf-8") if PAGE.exists() else ""
        if current != text:
            print(f"{PAGE.relative_to(ROOT)} is out of date. Run: python3 {__file__}")
            return 1
        return 0
    PAGE.write_text(text, encoding="utf-8")
    print(f"Wrote {PAGE.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
