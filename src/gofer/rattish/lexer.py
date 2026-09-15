"""Source scanning primitives for the Rattish lexer and parser."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from gofer.rattish.diagnostics import SourcePosition


class DuplicateJsonKeyError(ValueError):
    pass


def strict_json_loads(source: str) -> Any:
    """Decode strict JSON while rejecting duplicate object member names."""

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise DuplicateJsonKeyError(f"Duplicate JSON object key {key!r}.")
            result[key] = value
        return result

    return json.loads(source, object_pairs_hook=reject_duplicates)


@dataclass(frozen=True, slots=True)
class SourceLine:
    number: int
    text: str
    start_offset: int
    end_offset: int
    has_newline: bool

    def position(self, character_index: int) -> SourcePosition:
        prefix = self.text[:character_index]
        return SourcePosition(
            offset=self.start_offset + len(prefix.encode("utf-8")),
            line=self.number,
            column=character_index + 1,
        )

    def line_end(self) -> SourcePosition:
        if self.has_newline:
            return SourcePosition(offset=self.end_offset, line=self.number + 1, column=1)
        return self.position(len(self.text))


@dataclass(frozen=True, slots=True)
class LineView:
    line: SourceLine
    indent_columns: int
    content_index: int
    content: str


@dataclass(frozen=True, slots=True)
class RattishToken:
    """One layout or source-line token, including comment trivia."""

    kind: Literal["indent", "dedent", "line", "comment", "newline", "eof"]
    source: str
    start: SourcePosition
    end: SourcePosition


@dataclass(frozen=True, slots=True)
class LexedSource:
    source: str
    lines: tuple[SourceLine, ...]
    tokens: tuple[RattishToken, ...]


class RattishLexer:
    """Scan UTF-8 Rattish text into physical lines, layout tokens, and trivia."""

    def __init__(self, source: str) -> None:
        self.source = source

    def lex(self) -> LexedSource:
        lines = tuple(self._physical_lines(self.source))
        tokens: list[RattishToken] = []
        indents = [0]
        for line in lines:
            content_index, columns = self._leading_indent(line.text)
            content = line.text[content_index:]
            if content and not content.startswith("#"):
                while columns < indents[-1]:
                    indents.pop()
                    position = line.position(content_index)
                    tokens.append(RattishToken("dedent", "", position, position))
                if columns > indents[-1]:
                    indents.append(columns)
                    position = line.position(content_index)
                    tokens.append(
                        RattishToken("indent", line.text[:content_index], position, position)
                    )
                start = line.position(content_index)
                tokens.append(RattishToken("line", content, start, line.position(len(line.text))))
            elif content.startswith("#"):
                start = line.position(content_index)
                tokens.append(
                    RattishToken("comment", content, start, line.position(len(line.text)))
                )
            if line.has_newline:
                end = line.line_end()
                tokens.append(RattishToken("newline", "\n", end, end))
        eof = self._document_end(lines)
        while len(indents) > 1:
            indents.pop()
            tokens.append(RattishToken("dedent", "", eof, eof))
        tokens.append(RattishToken("eof", "", eof, eof))
        return LexedSource(self.source, lines, tuple(tokens))

    @staticmethod
    def _leading_indent(text: str) -> tuple[int, int]:
        index = 0
        columns = 0
        while index < len(text) and text[index] in {" ", "\t"}:
            columns += 2 if text[index] == "\t" else 1
            index += 1
        return index, columns

    @staticmethod
    def _physical_lines(source: str) -> list[SourceLine]:
        lines: list[SourceLine] = []
        offset = 0
        for number, raw in enumerate(source.splitlines(keepends=True), start=1):
            if raw.endswith("\r\n"):
                text = raw[:-2]
                has_newline = True
            elif raw.endswith(("\n", "\r")):
                text = raw[:-1]
                has_newline = True
            else:
                text = raw
                has_newline = False
            end_offset = offset + len(raw.encode("utf-8"))
            lines.append(SourceLine(number, text, offset, end_offset, has_newline))
            offset = end_offset
        if not lines:
            lines.append(SourceLine(1, "", 0, 0, False))
        elif source.endswith(("\n", "\r")):
            lines.append(SourceLine(len(lines) + 1, "", offset, offset, False))
        return lines

    def _document_end(self, lines: tuple[SourceLine, ...]) -> SourcePosition:
        encoded_length = len(self.source.encode("utf-8"))
        if self.source.endswith(("\n", "\r")):
            return SourcePosition(encoded_length, lines[-1].number, 1)
        final_line = lines[-1]
        return final_line.position(len(final_line.text))
