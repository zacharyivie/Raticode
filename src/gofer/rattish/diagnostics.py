"""Diagnostics and source positions shared by Rattish compiler stages."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

DIAGNOSTIC_SCHEMA_ID = "urn:raticode:rattish:schema:diagnostic-1.json"


@dataclass(frozen=True, slots=True)
class SourcePosition:
    """One position in source text."""

    offset: int
    line: int
    column: int

    def to_json(self) -> dict[str, int]:
        return {"offset": self.offset, "line": self.line, "column": self.column}


@dataclass(frozen=True, slots=True)
class SourceSpan:
    """A half-open range in source text."""

    start: SourcePosition
    end: SourcePosition

    def to_json(self) -> dict[str, dict[str, int]]:
        return {"start": self.start.to_json(), "end": self.end.to_json()}


@dataclass(frozen=True, slots=True)
class RelatedDiagnostic:
    file: str
    span: SourceSpan
    message: str

    def to_json(self) -> dict[str, Any]:
        return {"file": self.file, "span": self.span.to_json(), "message": self.message}


@dataclass(frozen=True, slots=True)
class RattishDiagnostic:
    """A stable machine-readable Rattish diagnostic."""

    code: str
    severity: Literal["error", "warning"]
    phase: Literal["lexer", "parser", "semantic", "lowering", "preflight", "runtime", "export"]
    message: str
    file: str
    span: SourceSpan
    related: tuple[RelatedDiagnostic, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)
    suggestions: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "$schema": DIAGNOSTIC_SCHEMA_ID,
            "code": self.code,
            "severity": self.severity,
            "phase": self.phase,
            "message": self.message,
            "file": self.file,
            "span": self.span.to_json(),
            "related": [item.to_json() for item in self.related],
            "details": self.details,
            "suggestions": list(self.suggestions),
        }


class RattishError(Exception):
    """Base exception carrying one or more Rattish diagnostics."""

    def __init__(
        self, diagnostics: list[RattishDiagnostic] | tuple[RattishDiagnostic, ...]
    ) -> None:
        self.diagnostics = tuple(diagnostics)
        super().__init__(f"{len(self.diagnostics)} Rattish error(s)")


class RattishParseError(RattishError):
    """Raised when strict parsing cannot produce an AST."""


class RattishCompileError(RattishError):
    """Raised when semantic analysis or lowering rejects source."""


def path_label(path: Path | str) -> str:
    """Return the portable source label used by diagnostics."""
    return str(path).replace("\\", "/")
