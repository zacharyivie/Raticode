"""Strict Rattish 1 source scanner and parser."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal, Never

from gofer.rattish.diagnostics import (
    RattishDiagnostic,
    RattishParseError,
    SourcePosition,
    SourceSpan,
)
from gofer.rattish.lexer import (
    DuplicateJsonKeyError,
    LineView,
    RattishLexer,
    RattishToken,
    SourceLine,
    strict_json_loads,
)

AST_SCHEMA_ID = "urn:raticode:rattish:schema:ast-1.json"

_IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*\Z")
_INTEGER = re.compile(r"-?(?:0|[1-9][0-9]*)\Z")
_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?\Z")
_DURATION = re.compile(r"([1-9][0-9]*)(ms|s|m|h|d)\Z", re.IGNORECASE)
_REFERENCE_ROOTS = {"input", "node", "loop", "trigger", "secret", "workflow"}


@dataclass(frozen=True, slots=True)
class PredicateToken:
    kind: str
    value: str
    start_index: int
    end_index: int


@dataclass(frozen=True, slots=True)
class InvalidSourceRegion:
    span: SourceSpan
    source: str


@dataclass(frozen=True, slots=True)
class RecoveringParseResult:
    ast: dict[str, Any] | None
    diagnostics: tuple[RattishDiagnostic, ...]
    invalid_regions: tuple[InvalidSourceRegion, ...]
    tokens: tuple[RattishToken, ...]


def _identifier_json(source: str, span: SourceSpan) -> dict[str, Any]:
    return {
        "kind": "identifier",
        "source": source,
        "canonical": source.lower(),
        "span": span.to_json(),
    }


class RattishParser:
    """Parse strict Rattish source into the schema-defined source AST."""

    def __init__(self, source: str, *, source_id: str = "workflow.rattish") -> None:
        self.source = source
        self.source_id = source_id
        self.lexed_source = RattishLexer(source).lex()
        self.lines = self.lexed_source.lines
        self.index = 0

    def parse(self) -> dict[str, Any]:
        self._skip_ignorable()
        version = self._parse_version()
        self._skip_ignorable()
        workflow = self._parse_workflow()
        nodes: list[dict[str, Any]] = []
        while True:
            self._skip_ignorable()
            if self.index >= len(self.lines) or self._at_eof_line():
                break
            nodes.append(self._parse_node())

        start = SourcePosition(0, 1, 1)
        end = self._document_end()
        return {
            "$schema": AST_SCHEMA_ID,
            "schema_version": 1,
            "source_id": self.source_id,
            "span": SourceSpan(start, end).to_json(),
            "version_directive": version,
            "workflow": workflow,
            "nodes": nodes,
        }

    def parse_recovering(self) -> RecoveringParseResult:
        """Parse for the editor, retaining valid nodes and skipped invalid source regions."""
        diagnostics: list[RattishDiagnostic] = []
        invalid_regions: list[InvalidSourceRegion] = []
        try:
            self._skip_ignorable()
            version = self._parse_version()
            self._skip_ignorable()
            workflow = self._parse_workflow()
        except RattishParseError as exc:
            return RecoveringParseResult(
                ast=None,
                diagnostics=exc.diagnostics,
                invalid_regions=(),
                tokens=self.lexed_source.tokens,
            )

        nodes: list[dict[str, Any]] = []
        while True:
            self._skip_ignorable()
            if self.index >= len(self.lines) or self._at_eof_line():
                break
            region_start_index = self.index
            try:
                nodes.append(self._parse_node())
            except RattishParseError as exc:
                diagnostics.extend(exc.diagnostics)
                self.index = self._next_declaration_index(max(self.index, region_start_index + 1))
                region_end_index = self.index
                start = self.lines[region_start_index].position(0)
                if region_end_index < len(self.lines):
                    end = self.lines[region_end_index].position(0)
                else:
                    end = self._document_end()
                source_bytes = self.source.encode("utf-8")
                source = source_bytes[start.offset : end.offset].decode("utf-8")
                invalid_regions.append(InvalidSourceRegion(SourceSpan(start, end), source))

        ast = {
            "$schema": AST_SCHEMA_ID,
            "schema_version": 1,
            "source_id": self.source_id,
            "span": SourceSpan(SourcePosition(0, 1, 1), self._document_end()).to_json(),
            "version_directive": version,
            "workflow": workflow,
            "nodes": nodes,
        }
        return RecoveringParseResult(
            ast=ast,
            diagnostics=tuple(diagnostics),
            invalid_regions=tuple(invalid_regions),
            tokens=self.lexed_source.tokens,
        )

    def _next_declaration_index(self, start: int) -> int:
        index = start
        while index < len(self.lines):
            text = self.lines[index].text
            if text and text[0] not in {" ", "\t"} and re.match(r"(?i)Node[ \t]+", text):
                return index
            index += 1
        return len(self.lines)

    def _document_end(self) -> SourcePosition:
        encoded_length = len(self.source.encode("utf-8"))
        if self.source.endswith(("\n", "\r")):
            line_number = self.source.count("\n") + 1
            if "\n" not in self.source and self.source.endswith("\r"):
                line_number = 2
            return SourcePosition(encoded_length, line_number, 1)
        final_line = self.lines[-1]
        return final_line.position(len(final_line.text))

    def _view(self, index: int | None = None) -> LineView:
        line = self.lines[self.index if index is None else index]
        columns = 0
        character_index = 0
        while character_index < len(line.text) and line.text[character_index] in {" ", "\t"}:
            columns += 2 if line.text[character_index] == "\t" else 1
            character_index += 1
        if columns % 2:
            self._raise(
                "RATTISH_INVALID_INDENTATION",
                "Indentation must use an even number of columns.",
                line,
                0,
                character_index,
                phase="lexer",
            )
        return LineView(line, columns, character_index, line.text[character_index:])

    def _at_eof_line(self) -> bool:
        return self.index == len(self.lines) - 1 and not self.lines[self.index].text

    def _is_ignorable(self, index: int) -> bool:
        view = self._view(index)
        return not view.content or view.content.startswith("#")

    def _skip_ignorable(self) -> None:
        while self.index < len(self.lines) and self._is_ignorable(self.index):
            self.index += 1

    def _parse_version(self) -> dict[str, Any]:
        if self.index >= len(self.lines):
            self._raise_eof("RATTISH_EXPECTED_TOKEN", "Expected 'Rattish: 1'.")
        view = self._view()
        if view.indent_columns != 0:
            self._raise_line_indent(view, 0)
        match = re.fullmatch(r"(?i)(Rattish|Radish)\s*:\s*([0-9]+)\s*(?:#.*)?", view.content)
        if match is None:
            self._raise(
                "RATTISH_EXPECTED_TOKEN",
                "Expected a Rattish version directive.",
                view.line,
                view.content_index,
                len(view.line.text),
            )
        assert match is not None
        version = int(match.group(2))
        keyword_start = view.content_index + match.start(1)
        value_start = view.content_index + match.start(2)
        span = SourceSpan(view.line.position(view.content_index), view.line.line_end())
        result = {
            "kind": "version_directive",
            "keyword_source": match.group(1),
            "value_source": match.group(2),
            "value": version,
            "span": span.to_json(),
        }
        self.index += 1
        _ = keyword_start, value_start
        return result

    def _parse_workflow(self) -> dict[str, Any]:
        header = self._view()
        keyword = self._declaration_header(header, "Workflow", has_name=False)
        start = header.line.position(header.content_index)
        self.index += 1
        entries = self._parse_generic_entries(parent_indent=0)
        if not entries:
            self._raise(
                "RATTISH_EXPECTED_TOKEN",
                "Workflow requires at least one field.",
                header.line,
                header.content_index,
                len(header.line.text),
            )
        end = SourcePosition(**entries[-1]["span"]["end"])
        return {
            "kind": "workflow_declaration",
            "keyword_source": keyword,
            "entries": entries,
            "span": SourceSpan(start, end).to_json(),
        }

    def _parse_node(self) -> dict[str, Any]:
        header = self._view()
        keyword, name, name_span = self._node_header(header)
        start = header.line.position(header.content_index)
        self.index += 1
        entries = self._parse_node_entries(parent_indent=0)
        if not entries:
            self._raise(
                "RATTISH_EXPECTED_TOKEN",
                "Node requires at least one field.",
                header.line,
                header.content_index,
                len(header.line.text),
            )
        end = SourcePosition(**entries[-1]["span"]["end"])
        return {
            "kind": "node_declaration",
            "keyword_source": keyword,
            "name": _identifier_json(name, name_span),
            "entries": entries,
            "span": SourceSpan(start, end).to_json(),
        }

    def _declaration_header(
        self, view: LineView, expected: str, *, has_name: bool
    ) -> str | tuple[str, str, SourceSpan]:
        if view.indent_columns != 0:
            self._raise_line_indent(view, 0)
        if has_name:
            return self._node_header(view)
        match = re.fullmatch(rf"(?i)({expected})\s*:\s*(?:#.*)?", view.content)
        if match is None:
            self._raise(
                "RATTISH_UNEXPECTED_DECLARATION",
                f"Expected {expected} declaration.",
                view.line,
                view.content_index,
                len(view.line.text),
            )
        assert match is not None
        return match.group(1)

    def _node_header(self, view: LineView) -> tuple[str, str, SourceSpan]:
        if view.indent_columns != 0:
            self._raise_line_indent(view, 0)
        match = re.fullmatch(
            r"(?i)(Node)[ \t]+([A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*)\s*:\s*(?:#.*)?",
            view.content,
        )
        if match is None:
            self._raise(
                "RATTISH_UNEXPECTED_DECLARATION",
                "Expected a Node declaration.",
                view.line,
                view.content_index,
                len(view.line.text),
            )
        assert match is not None
        name_start = view.content_index + match.start(2)
        name_end = view.content_index + match.end(2)
        return (
            match.group(1),
            match.group(2),
            SourceSpan(view.line.position(name_start), view.line.position(name_end)),
        )

    def _parse_generic_entries(self, *, parent_indent: int) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        while True:
            self._skip_ignorable()
            if self.index >= len(self.lines) or self._at_eof_line():
                break
            view = self._view()
            expected_indent = parent_indent + 2
            if view.indent_columns <= parent_indent:
                break
            if view.indent_columns != expected_indent:
                self._raise_line_indent(view, expected_indent)
            entries.append(self._parse_generic_field(expected_indent))
        return entries

    def _parse_node_entries(self, *, parent_indent: int) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        expected_indent = parent_indent + 2
        while True:
            self._skip_ignorable()
            if self.index >= len(self.lines) or self._at_eof_line():
                break
            view = self._view()
            if view.indent_columns <= parent_indent:
                break
            if view.indent_columns != expected_indent:
                self._raise_line_indent(view, expected_indent)
            key, rest, key_start, key_end, value_start = self._split_field(view)
            canonical = key.lower()
            if canonical == "needs":
                entries.append(
                    self._parse_needs(view, rest, key_start, key_end, value_start, expected_indent)
                )
            elif canonical == "to":
                entries.append(
                    self._parse_routes(view, rest, key_start, value_start, expected_indent)
                )
            elif canonical == "with":
                entries.append(self._parse_bindings(view, rest, key_start, expected_indent))
            else:
                entries.append(self._parse_generic_field(expected_indent))
        return entries

    def _split_field(self, view: LineView) -> tuple[str, str, int, int, int]:
        colon = self._find_unquoted_colon(view.content)
        if colon < 0:
            self._raise(
                "RATTISH_EXPECTED_TOKEN",
                "Expected ':' after field name.",
                view.line,
                view.content_index,
                len(view.line.text),
            )
        key_text = view.content[:colon].strip()
        if not _IDENTIFIER.fullmatch(key_text):
            self._raise(
                "RATTISH_INVALID_TOKEN",
                "Field name must be a Rattish identifier.",
                view.line,
                view.content_index,
                view.content_index + colon,
                phase="lexer",
            )
        key_start = view.content_index + view.content.index(key_text)
        key_end = key_start + len(key_text)
        raw_rest = view.content[colon + 1 :]
        leading = len(raw_rest) - len(raw_rest.lstrip(" \t"))
        value_start = view.content_index + colon + 1 + leading
        rest = self._strip_comment(raw_rest.lstrip(" \t")).rstrip()
        return key_text, rest, key_start, key_end, value_start

    @staticmethod
    def _find_unquoted_colon(text: str) -> int:
        quoted = False
        escaped = False
        for index, char in enumerate(text):
            if escaped:
                escaped = False
            elif char == "\\" and quoted:
                escaped = True
            elif char == '"':
                quoted = not quoted
            elif char == ":" and not quoted:
                return index
        return -1

    @staticmethod
    def _strip_comment(text: str) -> str:
        quoted = False
        escaped = False
        depth = 0
        for index, char in enumerate(text):
            if escaped:
                escaped = False
                continue
            if char == "\\" and quoted:
                escaped = True
                continue
            if char == '"':
                quoted = not quoted
                continue
            if quoted:
                continue
            if char in "{[":
                depth += 1
            elif char in "}]":
                depth = max(0, depth - 1)
            elif char == "#" and depth == 0:
                return text[:index]
        return text

    def _parse_generic_field(self, indent: int) -> dict[str, Any]:
        view = self._view()
        key, rest, key_start, key_end, value_start = self._split_field(view)
        key_span = SourceSpan(view.line.position(key_start), view.line.position(key_end))
        field_start = view.line.position(key_start)
        self.index += 1

        if rest == "|":
            value = self._parse_block_string(field_indent=indent, marker_line=view.line)
            field_end = SourcePosition(**value["span"]["end"])
        elif rest:
            value = self._parse_inline_value(
                rest,
                view.line,
                value_start,
                identifier_value=key.lower() == "type",
            )
            field_end = self._value_field_end(value, view.line)
        else:
            self._skip_ignorable()
            if self.index >= len(self.lines) or self._view().indent_columns != indent + 2:
                self._raise(
                    "RATTISH_EXPECTED_TOKEN",
                    f"Field {key!r} requires a value or nested block.",
                    view.line,
                    key_start,
                    key_end,
                )
            value = self._parse_block_value(indent + 2)
            field_end = SourcePosition(**value["span"]["end"])

        return {
            "kind": "field",
            "name": _identifier_json(key, key_span),
            "value": value,
            "span": SourceSpan(field_start, field_end).to_json(),
        }

    def _parse_block_string(self, *, field_indent: int, marker_line: SourceLine) -> dict[str, Any]:
        start_index = self.index
        content_indexes: list[int] = []
        while self.index < len(self.lines):
            view = self._view()
            if view.content and view.indent_columns <= field_indent:
                break
            content_indexes.append(self.index)
            self.index += 1
        if not content_indexes:
            value = ""
            end = marker_line.line_end()
        else:
            nonblank = [self._view(index) for index in content_indexes if self._view(index).content]
            trim_columns = min((item.indent_columns for item in nonblank), default=field_indent + 2)
            pieces: list[str] = []
            for index in content_indexes:
                line = self.lines[index]
                if not line.text.strip():
                    pieces.append("")
                    continue
                pieces.append(self._remove_indent_columns(line.text, trim_columns))
            value = "\n".join(pieces).rstrip("\n")
            if pieces and any(piece for piece in pieces):
                value += "\n"
            end = self.lines[content_indexes[-1]].line_end()
        source_start = (
            self.lines[start_index].start_offset if start_index < len(self.lines) else end.offset
        )
        start_line = self.lines[start_index].number if start_index < len(self.lines) else end.line
        return {
            "kind": "string",
            "style": "block",
            "source": value,
            "value": value,
            "span": SourceSpan(SourcePosition(source_start, start_line, 1), end).to_json(),
        }

    @staticmethod
    def _remove_indent_columns(text: str, columns: int) -> str:
        used = 0
        index = 0
        while index < len(text) and used < columns and text[index] in {" ", "\t"}:
            used += 2 if text[index] == "\t" else 1
            index += 1
        return text[index:]

    def _parse_block_value(self, indent: int) -> dict[str, Any]:
        view = self._view()
        if view.indent_columns != indent:
            self._raise_line_indent(view, indent)
        if view.content.startswith("-") and (
            len(view.content) == 1 or view.content[1] in {" ", "\t"}
        ):
            return self._parse_value_list(indent)
        return self._parse_value_map(indent)

    def _parse_value_map(self, indent: int) -> dict[str, Any]:
        entries: list[dict[str, Any]] = []
        start: SourcePosition | None = None
        while self.index < len(self.lines):
            self._skip_ignorable()
            if self.index >= len(self.lines):
                break
            view = self._view()
            if view.indent_columns < indent:
                break
            if view.indent_columns != indent:
                self._raise_line_indent(view, indent)
            key, rest, key_start, key_end, value_start = self._split_map_entry(view)
            key_span = SourceSpan(view.line.position(key_start), view.line.position(key_end))
            key_json = self._map_key_json(key, key_span)
            entry_start = view.line.position(key_start)
            if start is None:
                start = entry_start
            self.index += 1
            if rest:
                value = self._parse_inline_value(rest, view.line, value_start)
                end = self._value_field_end(value, view.line)
            else:
                self._skip_ignorable()
                if self.index >= len(self.lines) or self._view().indent_columns != indent + 2:
                    self._raise(
                        "RATTISH_EXPECTED_TOKEN",
                        "Map entry requires a value or nested block.",
                        view.line,
                        key_start,
                        key_end,
                    )
                value = self._parse_block_value(indent + 2)
                end = SourcePosition(**value["span"]["end"])
            entries.append(
                {
                    "kind": "map_entry",
                    "key": key_json,
                    "value": value,
                    "span": SourceSpan(entry_start, end).to_json(),
                }
            )
            next_index = self._next_meaningful_index(self.index)
            if next_index is None or self._view(next_index).indent_columns != indent:
                break
            self.index = next_index
        assert start is not None and entries
        end = SourcePosition(**entries[-1]["span"]["end"])
        return {"kind": "map", "entries": entries, "span": SourceSpan(start, end).to_json()}

    def _parse_value_list(self, indent: int) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        start: SourcePosition | None = None
        end: SourcePosition | None = None
        while self.index < len(self.lines):
            self._skip_ignorable()
            if self.index >= len(self.lines):
                break
            view = self._view()
            if view.indent_columns != indent or not view.content.startswith("-"):
                break
            dash_index = view.content_index
            if len(view.content) > 1 and view.content[1] not in {" ", "\t"}:
                self._raise(
                    "RATTISH_INVALID_TOKEN",
                    "List marker '-' must be followed by whitespace.",
                    view.line,
                    dash_index,
                    dash_index + 1,
                    phase="lexer",
                )
            if start is None:
                start = view.line.position(dash_index)
            raw = view.content[1:]
            leading = len(raw) - len(raw.lstrip(" \t"))
            rest = self._strip_comment(raw.lstrip(" \t")).rstrip()
            value_start = view.content_index + 1 + leading
            self.index += 1
            if rest:
                value = self._parse_inline_value(rest, view.line, value_start)
                end = self._value_field_end(value, view.line)
            else:
                self._skip_ignorable()
                value = self._parse_value_map(indent + 2)
                end = SourcePosition(**value["span"]["end"])
            items.append(value)
        assert start is not None and end is not None
        return {"kind": "list", "items": items, "span": SourceSpan(start, end).to_json()}

    def _split_map_entry(self, view: LineView) -> tuple[str, str, int, int, int]:
        colon = self._find_unquoted_colon(view.content)
        if colon < 0:
            self._raise(
                "RATTISH_EXPECTED_TOKEN",
                "Expected ':' after map key.",
                view.line,
                view.content_index,
                len(view.line.text),
            )
        raw_key = view.content[:colon].strip()
        if raw_key.startswith('"'):
            try:
                decoded = json.loads(raw_key)
            except json.JSONDecodeError:
                decoded = None
            if not isinstance(decoded, str):
                self._raise(
                    "RATTISH_INVALID_TOKEN",
                    "Quoted map key must use JSON string syntax.",
                    view.line,
                    view.content_index,
                    view.content_index + colon,
                    phase="lexer",
                )
            key = raw_key
        elif _IDENTIFIER.fullmatch(raw_key):
            key = raw_key
        else:
            self._raise(
                "RATTISH_INVALID_TOKEN",
                "Map key must be an identifier or JSON string.",
                view.line,
                view.content_index,
                view.content_index + colon,
                phase="lexer",
            )
        key_start = view.content_index + view.content.index(raw_key)
        key_end = key_start + len(raw_key)
        raw_rest = view.content[colon + 1 :]
        leading = len(raw_rest) - len(raw_rest.lstrip(" \t"))
        value_start = view.content_index + colon + 1 + leading
        rest = self._strip_comment(raw_rest.lstrip(" \t")).rstrip()
        return key, rest, key_start, key_end, value_start

    @staticmethod
    def _map_key_json(key: str, span: SourceSpan) -> dict[str, Any]:
        if key.startswith('"'):
            return {
                "kind": "string",
                "style": "quoted",
                "source": key,
                "value": json.loads(key),
                "span": span.to_json(),
            }
        return _identifier_json(key, span)

    def _parse_needs(
        self,
        view: LineView,
        rest: str,
        key_start: int,
        key_end: int,
        value_start: int,
        indent: int,
    ) -> dict[str, Any]:
        _ = key_end
        start = view.line.position(key_start)
        self.index += 1
        if rest:
            identifier = self._identifier_value(rest, view.line, value_start)
            nodes = [identifier]
            form = "scalar"
            end = view.line.line_end()
        else:
            nodes, end = self._parse_identifier_list(indent + 2)
            form = "list"
        return {
            "kind": "needs",
            "keyword_source": view.line.text[key_start : key_start + len("needs")],
            "form": form,
            "nodes": nodes,
            "span": SourceSpan(start, end).to_json(),
        }

    def _parse_identifier_list(self, indent: int) -> tuple[list[dict[str, Any]], SourcePosition]:
        values: list[dict[str, Any]] = []
        end: SourcePosition | None = None
        while True:
            self._skip_ignorable()
            if self.index >= len(self.lines):
                break
            view = self._view()
            if view.indent_columns != indent or not view.content.startswith("-"):
                break
            rest = self._strip_comment(view.content[1:].lstrip(" \t")).rstrip()
            value_start = view.content_index + view.content.index(rest)
            values.append(self._identifier_value(rest, view.line, value_start))
            end = view.line.line_end()
            self.index += 1
        if not values or end is None:
            self._raise_eof("RATTISH_EXPECTED_TOKEN", "Expected one or more list items.")
        return values, end

    def _identifier_value(
        self, source: str, line: SourceLine, character_index: int
    ) -> dict[str, Any]:
        if not _IDENTIFIER.fullmatch(source):
            self._raise(
                "RATTISH_INVALID_TOKEN",
                "Expected a Rattish identifier.",
                line,
                character_index,
                character_index + len(source),
                phase="lexer",
            )
        span = SourceSpan(
            line.position(character_index), line.position(character_index + len(source))
        )
        return _identifier_json(source, span)

    def _parse_routes(
        self,
        view: LineView,
        rest: str,
        key_start: int,
        value_start: int,
        indent: int,
    ) -> dict[str, Any]:
        start = view.line.position(key_start)
        self.index += 1
        end: SourcePosition
        if rest:
            routes = [self._parse_route(rest, view.line, value_start)]
            form = "scalar"
            end = view.line.line_end()
        else:
            routes = []
            list_end: SourcePosition | None = None
            while True:
                self._skip_ignorable()
                if self.index >= len(self.lines):
                    break
                item = self._view()
                if item.indent_columns != indent + 2 or not item.content.startswith("-"):
                    break
                raw = item.content[1:]
                leading = len(raw) - len(raw.lstrip(" \t"))
                route_text = self._strip_comment(raw.lstrip(" \t")).rstrip()
                route_start = item.content_index + 1 + leading
                routes.append(self._parse_route(route_text, item.line, route_start))
                list_end = item.line.line_end()
                self.index += 1
            if not routes or list_end is None:
                self._raise_eof("RATTISH_EXPECTED_TOKEN", "Expected one or more routes.")
            form = "list"
            end = list_end
        return {
            "kind": "routes",
            "keyword_source": view.line.text[key_start : key_start + 2],
            "form": form,
            "routes": routes,
            "span": SourceSpan(start, end).to_json(),
        }

    def _parse_route(self, text: str, line: SourceLine, start_index: int) -> dict[str, Any]:
        match = re.match(r"([A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*)", text)
        if match is None:
            self._raise(
                "RATTISH_EXPECTED_TOKEN",
                "Route requires a target node ID.",
                line,
                start_index,
                start_index + len(text),
            )
        target = match.group(1)
        target_start = start_index + match.start(1)
        target_span = SourceSpan(
            line.position(target_start), line.position(target_start + len(target))
        )
        remainder = text[match.end() :].strip()
        mode: Literal["unconditional", "when", "otherwise"] = "unconditional"
        predicate: dict[str, Any] | None = None
        if remainder.lower() == "otherwise":
            mode = "otherwise"
        elif remainder.lower().startswith("when "):
            mode = "when"
            predicate_text = remainder[5:].strip()
            predicate_offset = text.index(predicate_text, match.end())
            predicate = PredicateParser(
                predicate_text,
                line=line,
                base_index=start_index + predicate_offset,
                source_id=self.source_id,
            ).parse()
        elif remainder:
            self._raise(
                "RATTISH_EXPECTED_TOKEN",
                "Expected 'when <predicate>' or 'otherwise' after route target.",
                line,
                start_index + match.end(),
                start_index + len(text),
            )
        result: dict[str, Any] = {
            "kind": "route",
            "target": _identifier_json(target, target_span),
            "mode": mode,
            "span": SourceSpan(
                line.position(start_index), line.position(start_index + len(text))
            ).to_json(),
        }
        if predicate is not None:
            result["predicate"] = predicate
        return result

    def _parse_bindings(
        self, view: LineView, rest: str, key_start: int, indent: int
    ) -> dict[str, Any]:
        if rest:
            self._raise(
                "RATTISH_EXPECTED_TOKEN",
                "with requires an indented binding block.",
                view.line,
                key_start,
                len(view.line.text),
            )
        start = view.line.position(key_start)
        self.index += 1
        bindings: list[dict[str, Any]] = []
        while True:
            self._skip_ignorable()
            if self.index >= len(self.lines):
                break
            item = self._view()
            if item.indent_columns != indent + 2:
                break
            name, binding_rest, name_start, name_end, value_start = self._split_field(item)
            name_span = SourceSpan(item.line.position(name_start), item.line.position(name_end))
            binding_start = item.line.position(name_start)
            self.index += 1
            if binding_rest:
                value = self._parse_binding_value(binding_rest, item.line, value_start)
                bindings.append(
                    {
                        "kind": "binding",
                        "form": "compact",
                        "name": _identifier_json(name, name_span),
                        "value": value,
                        "span": SourceSpan(
                            binding_start, self._value_field_end(value, item.line)
                        ).to_json(),
                    }
                )
                continue

            entries: list[dict[str, Any]] = []
            while True:
                self._skip_ignorable()
                if self.index >= len(self.lines):
                    break
                nested = self._view()
                if nested.indent_columns != indent + 4:
                    break
                field, nested_rest, field_start, _, nested_value_start = self._split_field(nested)
                canonical = field.lower()
                if canonical not in {"from", "default"} or not nested_rest:
                    self._raise(
                        "RATTISH_EXPECTED_TOKEN",
                        "Expanded binding accepts from and optional default fields.",
                        nested.line,
                        field_start,
                        len(nested.line.text),
                    )
                value = self._parse_inline_value(nested_rest, nested.line, nested_value_start)
                if canonical == "from" and value["kind"] != "reference":
                    self._raise(
                        "RATTISH_EXPECTED_TOKEN",
                        "Expanded binding from field requires a reference.",
                        nested.line,
                        nested_value_start,
                        len(nested.line.text),
                    )
                entry: dict[str, Any] = {
                    "kind": canonical,
                    "keyword_source": field,
                    "span": SourceSpan(
                        nested.line.position(field_start), nested.line.line_end()
                    ).to_json(),
                }
                entry["reference" if canonical == "from" else "value"] = value
                entries.append(entry)
                self.index += 1
            if not entries:
                self._raise(
                    "RATTISH_EXPECTED_TOKEN",
                    "Expanded binding requires a from field and may include a default field.",
                    item.line,
                    name_start,
                    len(item.line.text),
                )
            end = SourcePosition(**entries[-1]["span"]["end"])
            bindings.append(
                {
                    "kind": "binding",
                    "form": "expanded",
                    "name": _identifier_json(name, name_span),
                    "entries": entries,
                    "span": SourceSpan(binding_start, end).to_json(),
                }
            )
        if not bindings:
            self._raise_eof("RATTISH_EXPECTED_TOKEN", "Expected one or more bindings.")
        end = SourcePosition(**bindings[-1]["span"]["end"])
        return {
            "kind": "bindings",
            "keyword_source": view.line.text[key_start : key_start + 4],
            "bindings": bindings,
            "span": SourceSpan(start, end).to_json(),
        }

    def _parse_binding_value(self, text: str, line: SourceLine, start_index: int) -> dict[str, Any]:
        """Parse a compact binding literal, reference, or Boolean expression."""
        if re.search(
            r"(?:==|!=|<=|>=|<|>|\bcontains\b|\bmatches\b|\band\b|\bor\b|^not\s|^exists\s)",
            text,
            re.IGNORECASE,
        ):
            expression = PredicateParser(
                text,
                line=line,
                base_index=start_index,
                source_id=self.source_id,
                allow_status=False,
            ).parse()
            return {
                "kind": "expression",
                "expression": expression,
                "span": expression["span"],
            }
        return self._parse_inline_value(text, line, start_index)

    def _parse_inline_value(
        self,
        text: str,
        line: SourceLine,
        start_index: int,
        *,
        identifier_value: bool = False,
    ) -> dict[str, Any]:
        span = SourceSpan(line.position(start_index), line.position(start_index + len(text)))
        if identifier_value:
            if not _IDENTIFIER.fullmatch(text):
                self._raise(
                    "RATTISH_INVALID_TOKEN",
                    "Node type must be a Rattish identifier.",
                    line,
                    start_index,
                    start_index + len(text),
                    phase="lexer",
                )
            return {
                "kind": "identifier_value",
                "source": text,
                "canonical": text.lower(),
                "span": span.to_json(),
            }
        if text.startswith('"'):
            try:
                value = json.loads(text)
            except json.JSONDecodeError as exc:
                self._raise(
                    "RATTISH_UNTERMINATED_STRING",
                    exc.msg,
                    line,
                    start_index,
                    start_index + len(text),
                    phase="lexer",
                )
            if not isinstance(value, str):
                self._raise(
                    "RATTISH_INVALID_TOKEN",
                    "Expected a JSON string.",
                    line,
                    start_index,
                    start_index + len(text),
                    phase="lexer",
                )
            return {
                "kind": "string",
                "style": "quoted",
                "source": text,
                "value": value,
                "span": span.to_json(),
            }
        if text.startswith(("{", "[")):
            try:
                json_source, json_span = self._collect_json(text, line, start_index)
                value = strict_json_loads(json_source)
            except (json.JSONDecodeError, DuplicateJsonKeyError) as exc:
                self._raise(
                    "RATTISH_INVALID_JSON",
                    str(exc),
                    line,
                    start_index,
                    start_index + len(text),
                    phase="lexer",
                )
            return {
                "kind": "json",
                "source": json_source,
                "value": value,
                "span": json_span.to_json(),
            }
        lowered = text.lower()
        if lowered in {"true", "false"}:
            return {
                "kind": "boolean",
                "source": text,
                "value": lowered == "true",
                "span": span.to_json(),
            }
        if lowered == "none":
            return {"kind": "none", "source": text, "span": span.to_json()}
        if lowered == "null":
            return {"kind": "null", "source": text, "value": None, "span": span.to_json()}
        duration = _DURATION.fullmatch(text)
        if duration is not None:
            return {
                "kind": "duration",
                "source": text,
                "amount": int(duration.group(1)),
                "unit_source": duration.group(2),
                "canonical_unit": duration.group(2).lower(),
                "span": span.to_json(),
            }
        if _INTEGER.fullmatch(text):
            return {"kind": "integer", "source": text, "value": int(text), "span": span.to_json()}
        if _NUMBER.fullmatch(text) and any(char in text for char in ".eE"):
            return {"kind": "number", "source": text, "value": float(text), "span": span.to_json()}
        reference = self._try_reference(text, line, start_index)
        if reference is not None:
            return reference
        return {
            "kind": "string",
            "style": "bare",
            "source": text,
            "value": text,
            "span": span.to_json(),
        }

    def _collect_json(
        self, initial: str, first_line: SourceLine, start_index: int
    ) -> tuple[str, SourceSpan]:
        pieces: list[str] = []
        depth = 0
        quoted = False
        escaped = False
        current_text = initial
        current_line = first_line
        current_start = start_index
        while True:
            closing_index: int | None = None
            for index, char in enumerate(current_text):
                if escaped:
                    escaped = False
                    continue
                if char == "\\" and quoted:
                    escaped = True
                    continue
                if char == '"':
                    quoted = not quoted
                    continue
                if quoted:
                    continue
                if char in "{[":
                    depth += 1
                elif char in "}]":
                    depth -= 1
                    if depth < 0:
                        self._raise(
                            "RATTISH_INVALID_JSON",
                            "Embedded JSON closes more containers than it opens.",
                            current_line,
                            current_start + index,
                            current_start + index + 1,
                            phase="lexer",
                        )
                    if depth == 0:
                        closing_index = index
                        break
            if closing_index is not None:
                remainder = current_text[closing_index + 1 :]
                if remainder.strip() and not remainder.lstrip().startswith("#"):
                    self._raise(
                        "RATTISH_INVALID_JSON",
                        "Unexpected source after embedded JSON value.",
                        current_line,
                        current_start + closing_index + 1,
                        current_start + len(current_text),
                        phase="lexer",
                    )
                pieces.append(current_text[: closing_index + 1])
                end = current_line.position(current_start + closing_index + 1)
                return "\n".join(pieces), SourceSpan(first_line.position(start_index), end)
            pieces.append(current_text)
            if self.index >= len(self.lines) or self._at_eof_line():
                self._raise(
                    "RATTISH_INVALID_JSON",
                    "Embedded JSON value is not terminated.",
                    first_line,
                    start_index,
                    start_index + len(initial),
                    phase="lexer",
                )
            current_line = self.lines[self.index]
            current_text = current_line.text
            current_start = 0
            self.index += 1

    @staticmethod
    def _value_field_end(value: dict[str, Any], source_line: SourceLine) -> SourcePosition:
        end = SourcePosition(**value["span"]["end"])
        return source_line.line_end() if end.line == source_line.number else end

    def _try_reference(
        self, text: str, line: SourceLine, start_index: int
    ) -> dict[str, Any] | None:
        root_match = re.match(r"[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*", text)
        if root_match is None or root_match.group(0).lower() not in _REFERENCE_ROOTS:
            return None
        if root_match.end() == len(text):
            return None
        cursor = root_match.end()
        selectors: list[dict[str, Any]] = []
        json_mode = False
        while cursor < len(text):
            if text[cursor] == ".":
                segment_start = cursor + 1
                segment_match = re.match(
                    r"[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*|[0-9]+",
                    text[segment_start:],
                )
                if segment_match is None:
                    return None
                source = segment_match.group(0)
                segment_end = segment_start + len(source)
                role = "json_member" if json_mode else "identifier"
                selector = {
                    "kind": "member",
                    "notation": "dot",
                    "source": source,
                    "role": role,
                    "canonical": source.lower() if role == "identifier" else None,
                    "span": SourceSpan(
                        line.position(start_index + segment_start),
                        line.position(start_index + segment_end),
                    ).to_json(),
                }
                selectors.append(selector)
                if root_match.group(0).lower() == "node" and len(selectors) >= 2:
                    if selectors[1]["canonical"] in {"output", "error"}:
                        json_mode = True
                elif root_match.group(0).lower() in {"input", "trigger"} and selectors:
                    json_mode = True
                elif root_match.group(0).lower() == "loop" and selectors:
                    json_mode = selectors[0]["canonical"] == "item"
                cursor = segment_end
                continue
            if text[cursor] == "[":
                close = self._find_bracket_close(text, cursor)
                if close is None:
                    return None
                inner = text[cursor + 1 : close]
                selector_span = SourceSpan(
                    line.position(start_index + cursor), line.position(start_index + close + 1)
                )
                if _INTEGER.fullmatch(inner) and int(inner) >= 0:
                    selectors.append(
                        {
                            "kind": "index",
                            "source": inner,
                            "value": int(inner),
                            "span": selector_span.to_json(),
                        }
                    )
                else:
                    try:
                        member = json.loads(inner)
                    except json.JSONDecodeError:
                        return None
                    if not isinstance(member, str):
                        return None
                    selectors.append(
                        {
                            "kind": "member",
                            "notation": "bracket",
                            "source": member,
                            "role": "json_member",
                            "canonical": None,
                            "span": selector_span.to_json(),
                        }
                    )
                json_mode = True
                cursor = close + 1
                continue
            return None
        root_source = root_match.group(0)
        root_span = SourceSpan(
            line.position(start_index), line.position(start_index + len(root_source))
        )
        return {
            "kind": "reference",
            "root": _identifier_json(root_source, root_span),
            "selectors": selectors,
            "span": SourceSpan(
                line.position(start_index), line.position(start_index + len(text))
            ).to_json(),
        }

    @staticmethod
    def _find_bracket_close(text: str, start: int) -> int | None:
        quoted = False
        escaped = False
        for index in range(start + 1, len(text)):
            char = text[index]
            if escaped:
                escaped = False
            elif char == "\\" and quoted:
                escaped = True
            elif char == '"':
                quoted = not quoted
            elif char == "]" and not quoted:
                return index
        return None

    def _next_meaningful_index(self, start: int) -> int | None:
        index = start
        while index < len(self.lines):
            if not self._is_ignorable(index):
                return index
            index += 1
        return None

    def _raise_line_indent(self, view: LineView, expected: int) -> Never:
        self._raise(
            "RATTISH_INVALID_INDENTATION",
            f"Expected {expected} indentation columns, found {view.indent_columns}.",
            view.line,
            0,
            view.content_index,
            phase="lexer",
        )

    def _raise(
        self,
        code: str,
        message: str,
        line: SourceLine,
        start_index: int,
        end_index: int,
        *,
        phase: Literal["lexer", "parser"] = "parser",
    ) -> Never:
        diagnostic = RattishDiagnostic(
            code=code,
            severity="error",
            phase=phase,
            message=message,
            file=self.source_id,
            span=SourceSpan(line.position(start_index), line.position(end_index)),
        )
        raise RattishParseError([diagnostic])

    def _raise_eof(self, code: str, message: str) -> Never:
        end = self._document_end()
        raise RattishParseError(
            [
                RattishDiagnostic(
                    code=code,
                    severity="error",
                    phase="parser",
                    message=message,
                    file=self.source_id,
                    span=SourceSpan(end, end),
                )
            ]
        )


class PredicateParser:
    """Recursive-descent parser for Rattish route predicates."""

    def __init__(
        self,
        text: str,
        *,
        line: SourceLine,
        base_index: int,
        source_id: str,
        allow_status: bool = True,
    ) -> None:
        self.text = text
        self.line = line
        self.base_index = base_index
        self.source_id = source_id
        self.allow_status = allow_status
        self.tokens = self._tokenize(text)
        self.index = 0

    def parse(self) -> dict[str, Any]:
        result = self._parse_or()
        if self.index != len(self.tokens):
            self._error("Unexpected predicate token.", self.tokens[self.index])
        return result

    def _tokenize(self, text: str) -> list[PredicateToken]:
        tokens: list[PredicateToken] = []
        index = 0
        while index < len(text):
            if text[index].isspace():
                index += 1
                continue
            start = index
            for operator in ("<=", ">=", "==", "!=", "<", ">", "(", ")"):
                if text.startswith(operator, index):
                    tokens.append(PredicateToken("symbol", operator, start, start + len(operator)))
                    index += len(operator)
                    break
            else:
                if text[index] == '"':
                    index += 1
                    escaped = False
                    while index < len(text):
                        char = text[index]
                        index += 1
                        if escaped:
                            escaped = False
                        elif char == "\\":
                            escaped = True
                        elif char == '"':
                            break
                    tokens.append(PredicateToken("quoted", text[start:index], start, index))
                    continue
                while (
                    index < len(text) and not text[index].isspace() and text[index] not in "()<>!="
                ):
                    index += 1
                tokens.append(PredicateToken("word", text[start:index], start, index))
                continue
            continue
        return tokens

    def _parse_or(self) -> dict[str, Any]:
        left = self._parse_and()
        while self._accept_word("or"):
            operator = self.tokens[self.index - 1]
            right = self._parse_and()
            left = self._logical("or", operator, left, right)
        return left

    def _parse_and(self) -> dict[str, Any]:
        left = self._parse_unary()
        while self._accept_word("and"):
            operator = self.tokens[self.index - 1]
            right = self._parse_unary()
            left = self._logical("and", operator, left, right)
        return left

    def _parse_unary(self) -> dict[str, Any]:
        if self._accept_word("not"):
            operator = self.tokens[self.index - 1]
            operand = self._parse_unary()
            return {
                "kind": "not",
                "operator_source": operator.value,
                "operand": operand,
                "span": self._combined_span(operator, operand).to_json(),
            }
        return self._parse_primary()

    def _parse_primary(self) -> dict[str, Any]:
        if self._accept_symbol("("):
            opening = self.tokens[self.index - 1]
            operand = self._parse_or()
            closing = self._expect_symbol(")")
            return {
                "kind": "group",
                "operand": operand,
                "span": self._token_range(opening, closing).to_json(),
            }
        if self.allow_status and (self._accept_word("succeeded") or self._accept_word("failed")):
            token = self.tokens[self.index - 1]
            return {
                "kind": "status",
                "source": token.value,
                "value": token.value.lower(),
                "span": self._token_span(token).to_json(),
            }
        if self._accept_word("exists"):
            keyword = self.tokens[self.index - 1]
            reference_token = self._next()
            reference = self._reference(reference_token)
            return {
                "kind": "exists",
                "keyword_source": keyword.value,
                "reference": reference,
                "span": self._token_to_node_span(keyword, reference).to_json(),
            }

        left_token = self._next()
        left = self._comparable(left_token)
        if self._accept_word("is"):
            operator_start = self.tokens[self.index - 1]
            negated = self._accept_word("not")
            null_token = self._expect_word("null")
            if left.get("kind") != "reference":
                self._error("Null tests require a reference.", left_token)
            return {
                "kind": "null_test",
                "reference": left,
                "operator": "is_not_null" if negated else "is_null",
                "operator_source": self.text[operator_start.start_index : null_token.end_index],
                "span": self._token_to_node_span(left_token, left).to_json()
                | {"end": self._token_span(null_token).to_json()["end"]},
            }
        operator = self._accept_operator()
        if operator is not None:
            right_token = self._next()
            right = self._comparable(right_token)
            return {
                "kind": "comparison",
                "operator": operator.value.lower(),
                "operator_source": operator.value,
                "left": left,
                "right": right,
                "span": SourceSpan(
                    self._token_span(left_token).start,
                    self._token_span(right_token).end,
                ).to_json(),
            }
        if left.get("kind") != "reference":
            self._error("A standalone predicate must be a Boolean reference.", left_token)
        return {
            "kind": "reference_predicate",
            "reference": left,
            "span": left["span"],
        }

    def _logical(
        self,
        operator_name: str,
        operator: PredicateToken,
        left: dict[str, Any],
        right: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "kind": "logical",
            "operator": operator_name,
            "operator_source": operator.value,
            "left": left,
            "right": right,
            "span": {
                "start": left["span"]["start"],
                "end": right["span"]["end"],
            },
        }

    def _comparable(self, token: PredicateToken) -> dict[str, Any]:
        absolute_start = self.base_index + token.start_index
        parser = RattishParser("", source_id=self.source_id)
        reference = parser._try_reference(token.value, self.line, absolute_start)
        if reference is not None:
            return reference
        span = self._token_span(token)
        lowered = token.value.lower()
        if token.kind == "quoted":
            try:
                value = json.loads(token.value)
            except json.JSONDecodeError:
                self._error("Invalid quoted predicate string.", token)
            return {
                "kind": "string",
                "style": "quoted",
                "source": token.value,
                "value": value,
                "span": span.to_json(),
            }
        if lowered in {"true", "false"}:
            return {
                "kind": "boolean",
                "source": token.value,
                "value": lowered == "true",
                "span": span.to_json(),
            }
        if lowered == "null":
            return {"kind": "null", "source": token.value, "value": None, "span": span.to_json()}
        if _INTEGER.fullmatch(token.value):
            return {
                "kind": "integer",
                "source": token.value,
                "value": int(token.value),
                "span": span.to_json(),
            }
        if _NUMBER.fullmatch(token.value):
            return {
                "kind": "number",
                "source": token.value,
                "value": float(token.value),
                "span": span.to_json(),
            }
        self._error("Expected a predicate reference or literal.", token)

    def _reference(self, token: PredicateToken) -> dict[str, Any]:
        parser = RattishParser("", source_id=self.source_id)
        reference = parser._try_reference(
            token.value, self.line, self.base_index + token.start_index
        )
        if reference is None:
            self._error("Expected a reference.", token)
        return reference

    def _accept_operator(self) -> PredicateToken | None:
        if self.index >= len(self.tokens):
            return None
        token = self.tokens[self.index]
        if token.value.lower() in {"==", "!=", "<", "<=", ">", ">=", "contains", "matches"}:
            self.index += 1
            return token
        return None

    def _accept_word(self, word: str) -> bool:
        if self.index < len(self.tokens) and self.tokens[self.index].value.lower() == word:
            self.index += 1
            return True
        return False

    def _accept_symbol(self, symbol: str) -> bool:
        if self.index < len(self.tokens) and self.tokens[self.index].value == symbol:
            self.index += 1
            return True
        return False

    def _expect_word(self, word: str) -> PredicateToken:
        if not self._accept_word(word):
            token = self.tokens[self.index] if self.index < len(self.tokens) else None
            self._error(f"Expected {word!r}.", token)
        return self.tokens[self.index - 1]

    def _expect_symbol(self, symbol: str) -> PredicateToken:
        if not self._accept_symbol(symbol):
            token = self.tokens[self.index] if self.index < len(self.tokens) else None
            self._error(f"Expected {symbol!r}.", token)
        return self.tokens[self.index - 1]

    def _next(self) -> PredicateToken:
        if self.index >= len(self.tokens):
            self._error("Unexpected end of predicate.", None)
        token = self.tokens[self.index]
        self.index += 1
        return token

    def _token_span(self, token: PredicateToken) -> SourceSpan:
        return SourceSpan(
            self.line.position(self.base_index + token.start_index),
            self.line.position(self.base_index + token.end_index),
        )

    def _token_range(self, start: PredicateToken, end: PredicateToken) -> SourceSpan:
        return SourceSpan(self._token_span(start).start, self._token_span(end).end)

    def _combined_span(self, start: PredicateToken, node: dict[str, Any]) -> SourceSpan:
        return SourceSpan(
            self._token_span(start).start,
            SourcePosition(**node["span"]["end"]),
        )

    def _token_to_node_span(self, start: PredicateToken, node: dict[str, Any]) -> SourceSpan:
        return SourceSpan(
            self._token_span(start).start,
            SourcePosition(**node["span"]["end"]),
        )

    def _error(self, message: str, token: PredicateToken | None) -> Never:
        if token is None:
            position = self.line.position(self.base_index + len(self.text))
            span = SourceSpan(position, position)
        else:
            span = self._token_span(token)
        raise RattishParseError(
            [
                RattishDiagnostic(
                    code="RATTISH_EXPECTED_TOKEN",
                    severity="error",
                    phase="parser",
                    message=message,
                    file=self.source_id,
                    span=span,
                )
            ]
        )


def parse_rattish(source: str, *, source_id: str = "workflow.rattish") -> dict[str, Any]:
    """Parse one strict Rattish 1 document."""
    return RattishParser(source, source_id=source_id).parse()


def parse_rattish_recovering(
    source: str, *, source_id: str = "workflow.rattish"
) -> RecoveringParseResult:
    """Return editor-oriented partial syntax state without publishing executable IR."""
    return RattishParser(source, source_id=source_id).parse_recovering()
