"""Canonical source formatting for strict Rattish 1 documents."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gofer.rattish.migration import migrate_source
from gofer.rattish.parser import parse_rattish


class RattishFormatError(RuntimeError):
    """Raised when a Rattish source file cannot be read or replaced."""


@dataclass(frozen=True, slots=True)
class FormatResult:
    source: str
    changed: bool


@dataclass(frozen=True, slots=True)
class _Line:
    text: str
    source_start: int
    source_end: int


@dataclass(frozen=True, slots=True)
class _Comment:
    line: int
    indent: int
    text: str
    inline: bool


def format_rattish(source: str, *, source_id: str = "workflow.rattish") -> str:
    """Return the canonical spelling and layout of one strict Rattish document."""
    ast = parse_rattish(source, source_id=source_id)
    rendered = _Renderer().document(ast)
    rendered = _attach_comments(rendered, _comments(source, ast))
    return "\n".join(line.text.rstrip() for line in rendered).rstrip() + "\n"


def format_rattish_file(source_path: Path, *, write: bool = True) -> FormatResult:
    """Format one Rattish file and optionally replace it atomically."""
    path = source_path.expanduser().resolve()
    try:
        path = migrate_source(path)
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise RattishFormatError(f"Could not read Rattish source {path}: {exc}") from exc
    formatted = format_rattish(source, source_id=path.name)
    changed = formatted != source
    if changed and write:
        _replace_text(path, formatted)
    return FormatResult(formatted, changed)


def _replace_text(path: Path, source: str) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(source, encoding="utf-8", newline="\n")
        os.replace(temporary, path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise RattishFormatError(f"Could not replace Rattish source {path}: {exc}") from exc


class _Renderer:
    def document(self, ast: dict[str, Any]) -> list[_Line]:
        version = ast["version_directive"]
        lines = [self._line(f"rattish: {version['value']}", version)]
        lines.append(_Line("", version["span"]["end"]["line"], version["span"]["end"]["line"]))
        lines.extend(self._declaration(ast["workflow"], workflow=True))
        for node in ast["nodes"]:
            lines.append(_Line("", node["span"]["start"]["line"], node["span"]["start"]["line"]))
            lines.extend(self._declaration(node, workflow=False))
        return lines

    def _declaration(self, declaration: dict[str, Any], *, workflow: bool) -> list[_Line]:
        if workflow:
            header = "workflow:"
        else:
            header = f"node {declaration['name']['canonical']}:"
        lines = [self._line(header, declaration, exact=True)]
        for entry in declaration["entries"]:
            lines.extend(self._node_entry(entry, 1))
        return lines

    def _node_entry(self, entry: dict[str, Any], depth: int) -> list[_Line]:
        kind = entry["kind"]
        if kind == "field":
            return self._field(entry, depth)
        if kind == "needs":
            return self._identifiers("needs", entry["nodes"], entry, depth, entry["form"])
        if kind == "routes":
            return self._routes(entry, depth)
        if kind == "bindings":
            return self._bindings(entry, depth)
        raise ValueError(f"Unsupported Rattish AST entry kind {kind!r}.")

    def _field(self, field: dict[str, Any], depth: int) -> list[_Line]:
        name = field["name"]["canonical"]
        value = field["value"]
        prefix = "  " * depth
        if value["kind"] == "string" and value["style"] == "block":
            lines = [self._line(f"{prefix}{name}: |", field, exact=True)]
            content = value["value"].removesuffix("\n").split("\n") if value["value"] else []
            start_line = value["span"]["start"]["line"]
            lines.extend(
                _Line(f"{prefix}  {part}" if part else "", start_line + index, start_line + index)
                for index, part in enumerate(content)
            )
            return lines
        if value["kind"] in {"list", "map"}:
            lines = [self._line(f"{prefix}{name}:", field, exact=True)]
            lines.extend(self._block_value(value, depth + 1))
            return lines
        rendered_value = self._inline_value(value)
        if name == "finish" and value["kind"] == "string":
            lowered = rendered_value.lower()
            if lowered in {"pass", "fail"}:
                rendered_value = lowered
        return [
            self._line(
                f"{prefix}{name}: {rendered_value}",
                field,
                exact=value["kind"] != "json",
            )
        ]

    def _block_value(self, value: dict[str, Any], depth: int) -> list[_Line]:
        if value["kind"] == "list":
            lines: list[_Line] = []
            for item in value["items"]:
                prefix = "  " * depth
                if item["kind"] == "map":
                    lines.append(self._line(f"{prefix}-", item, exact=True))
                    lines.extend(self._map_entries(item, depth + 1))
                else:
                    lines.append(
                        self._line(
                            f"{prefix}- {self._inline_value(item)}",
                            item,
                            exact=item["kind"] != "json",
                        )
                    )
            return lines
        if value["kind"] == "map":
            return self._map_entries(value, depth)
        raise ValueError(f"Unsupported block value kind {value['kind']!r}.")

    def _map_entries(self, value: dict[str, Any], depth: int) -> list[_Line]:
        lines: list[_Line] = []
        prefix = "  " * depth
        for entry in value["entries"]:
            key = entry["key"]
            key_text = (
                key["canonical"]
                if key["kind"] == "identifier"
                else json.dumps(key["value"], ensure_ascii=False)
            )
            nested = entry["value"]
            if nested["kind"] in {"list", "map"}:
                lines.append(self._line(f"{prefix}{key_text}:", entry, exact=True))
                lines.extend(self._block_value(nested, depth + 1))
            else:
                lines.append(
                    self._line(
                        f"{prefix}{key_text}: {self._inline_value(nested)}",
                        entry,
                        exact=nested["kind"] != "json",
                    )
                )
        return lines

    def _identifiers(
        self,
        keyword: str,
        identifiers: list[dict[str, Any]],
        entry: dict[str, Any],
        depth: int,
        form: str,
    ) -> list[_Line]:
        prefix = "  " * depth
        if form == "scalar":
            return [self._line(f"{prefix}{keyword}: {identifiers[0]['canonical']}", entry)]
        lines = [self._line(f"{prefix}{keyword}:", entry, exact=True)]
        lines.extend(
            self._line(f"{prefix}  - {identifier['canonical']}", identifier, exact=True)
            for identifier in identifiers
        )
        return lines

    def _routes(self, entry: dict[str, Any], depth: int) -> list[_Line]:
        prefix = "  " * depth
        routes = entry["routes"]
        if entry["form"] == "scalar":
            return [self._line(f"{prefix}to: {self._route(routes[0])}", entry)]
        lines = [self._line(f"{prefix}to:", entry, exact=True)]
        lines.extend(
            self._line(f"{prefix}  - {self._route(route)}", route, exact=True) for route in routes
        )
        return lines

    def _route(self, route: dict[str, Any]) -> str:
        text = str(route["target"]["canonical"])
        if route["mode"] == "otherwise":
            return f"{text} otherwise"
        if route["mode"] == "when":
            return f"{text} when {self._predicate(route['predicate'])}"
        return text

    def _bindings(self, entry: dict[str, Any], depth: int) -> list[_Line]:
        prefix = "  " * depth
        lines = [self._line(f"{prefix}with:", entry, exact=True)]
        for binding in entry["bindings"]:
            name = binding["name"]["canonical"]
            if binding["form"] == "compact":
                lines.append(
                    self._line(
                        f"{prefix}  {name}: {self._inline_value(binding['value'])}",
                        binding,
                        exact=binding["value"]["kind"] != "json",
                    )
                )
                continue
            lines.append(self._line(f"{prefix}  {name}:", binding, exact=True))
            for nested in binding["entries"]:
                value = nested["reference"] if nested["kind"] == "from" else nested["value"]
                lines.append(
                    self._line(
                        f"{prefix}    {nested['kind']}: {self._inline_value(value)}",
                        nested,
                        exact=value["kind"] != "json",
                    )
                )
        return lines

    def _inline_value(self, value: dict[str, Any]) -> str:
        kind = value["kind"]
        if kind == "identifier_value":
            return str(value["canonical"])
        if kind == "string":
            if value["style"] == "quoted":
                return json.dumps(value["value"], ensure_ascii=False)
            return str(value["value"])
        if kind == "integer":
            return str(value["value"])
        if kind == "number":
            return json.dumps(value["value"], allow_nan=False)
        if kind == "boolean":
            return "true" if value["value"] else "false"
        if kind == "none":
            return "none"
        if kind == "null":
            return "null"
        if kind == "duration":
            return f"{value['amount']}{value['canonical_unit']}"
        if kind == "json":
            return json.dumps(
                value["value"], ensure_ascii=False, sort_keys=True, separators=(", ", ": ")
            )
        if kind == "reference":
            return self._reference(value)
        if kind == "expression":
            return self._predicate(value["expression"])
        raise ValueError(f"Rattish value kind {kind!r} cannot be rendered inline.")

    def _reference(self, reference: dict[str, Any]) -> str:
        text = str(reference["root"]["canonical"])
        for selector in reference["selectors"]:
            if selector["kind"] == "index":
                text += f"[{selector['value']}]"
            elif selector["notation"] == "bracket":
                text += f"[{json.dumps(selector['source'], ensure_ascii=False)}]"
            elif selector["role"] == "identifier":
                text += f".{selector['canonical']}"
            else:
                text += f".{selector['source']}"
        return text

    def _predicate(self, predicate: dict[str, Any]) -> str:
        kind = predicate["kind"]
        if kind == "logical":
            return (
                f"{self._predicate(predicate['left'])} {predicate['operator']} "
                f"{self._predicate(predicate['right'])}"
            )
        if kind == "not":
            return f"not {self._predicate(predicate['operand'])}"
        if kind == "group":
            return f"({self._predicate(predicate['operand'])})"
        if kind == "exists":
            return f"exists {self._reference(predicate['reference'])}"
        if kind == "null_test":
            operator = "is null" if predicate["operator"] == "is_null" else "is not null"
            return f"{self._reference(predicate['reference'])} {operator}"
        if kind == "comparison":
            return (
                f"{self._comparable(predicate['left'])} {predicate['operator']} "
                f"{self._comparable(predicate['right'])}"
            )
        if kind == "status":
            return str(predicate["value"])
        if kind == "reference_predicate":
            return self._reference(predicate["reference"])
        raise ValueError(f"Unsupported predicate kind {kind!r}.")

    def _comparable(self, value: dict[str, Any]) -> str:
        return self._reference(value) if value["kind"] == "reference" else self._inline_value(value)

    @staticmethod
    def _line(text: str, node: dict[str, Any], *, exact: bool = False) -> _Line:
        span = node["span"]
        start = span["start"]["line"]
        end = start if exact else max(start, span["end"]["line"])
        return _Line(text, start, end)


def _attach_comments(lines: list[_Line], comments: list[_Comment]) -> list[_Line]:
    result = list(lines)
    for comment in sorted((item for item in comments if item.inline), key=lambda item: item.line):
        index = next(
            (
                position
                for position, line in enumerate(result)
                if line.source_start <= comment.line <= line.source_end and line.text
            ),
            None,
        )
        if index is not None:
            line = result[index]
            result[index] = _Line(
                f"{line.text.rstrip()}  {comment.text}", line.source_start, line.source_end
            )
    standalone = sorted((item for item in comments if not item.inline), key=lambda item: item.line)
    for comment in standalone:
        index = next(
            (
                position
                for position, line in enumerate(result)
                if line.source_start > comment.line and line.text
            ),
            len(result),
        )
        rendered = _Line(" " * comment.indent + comment.text, comment.line, comment.line)
        result.insert(index, rendered)
    return result


def _comments(source: str, ast: dict[str, Any]) -> list[_Comment]:
    protected = _protected_spans(ast)
    comments: list[_Comment] = []
    byte_offset = 0
    for line_number, raw in enumerate(source.splitlines(keepends=True), start=1):
        text = raw.removesuffix("\n").removesuffix("\r")
        for index, character in enumerate(text):
            if character != "#":
                continue
            offset = byte_offset + len(text[:index].encode("utf-8"))
            if any(start <= offset < end for start, end in protected):
                continue
            prefix = text[:index]
            indent = _indent_columns(prefix) if not prefix.strip() else 0
            comments.append(
                _Comment(line_number, indent, text[index:].rstrip(), inline=bool(prefix.strip()))
            )
            break
        byte_offset += len(raw.encode("utf-8"))
    return comments


def _protected_spans(node: Any) -> list[tuple[int, int]]:
    protected: list[tuple[int, int]] = []
    if isinstance(node, dict):
        if node.get("kind") in {"json", "string"} and "span" in node:
            span = node["span"]
            protected.append((span["start"]["offset"], span["end"]["offset"]))
        for value in node.values():
            protected.extend(_protected_spans(value))
    elif isinstance(node, list):
        for value in node:
            protected.extend(_protected_spans(value))
    return protected


def _indent_columns(source: str) -> int:
    columns = 0
    for character in source:
        if character == " ":
            columns += 1
        elif character == "\t":
            columns += 2
        else:
            break
    return columns
