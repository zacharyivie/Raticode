"""Diagnostics and source positions shared by Rattish compiler stages."""

from __future__ import annotations

import difflib
from collections.abc import Iterable
from dataclasses import dataclass, field
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
    severity: Literal["error", "warning", "info"]
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


def close_names(name: str, candidates: Iterable[str], *, limit: int = 3) -> list[str]:
    """Names in ``candidates`` that look like a typo of ``name``, closest first.

    Underscores and hyphens are treated alike, so ``write_file`` finds ``write-file``.
    """
    by_key: dict[str, str] = {}
    for candidate in candidates:
        by_key.setdefault(candidate.lower().replace("_", "-"), candidate)
    key = name.lower().replace("_", "-")
    if key in by_key:
        return [by_key[key]]
    matches = difflib.get_close_matches(key, list(by_key), n=limit, cutoff=0.6)
    if matches:
        # Keep only names about as close as the best one, so a clear winner stands alone.
        scores = {match: difflib.SequenceMatcher(None, key, match).ratio() for match in matches}
        best = max(scores.values())
        matches = sorted(
            (match for match in matches if scores[match] >= best - 0.1),
            key=lambda match: (-scores[match], match),
        )
    else:
        # A short name inside a longer one, such as 'claude' in 'claude-code'.
        matches = [
            candidate
            for candidate in sorted(by_key)
            if len(key) >= 4 and candidate.split("-")[0] == key
        ][:limit]
    return [by_key[match] for match in matches]


def did_you_mean(name: str, candidates: Iterable[str]) -> tuple[str, ...]:
    """A one-item suggestion tuple naming the closest candidates, or nothing."""
    matches = close_names(name, candidates)
    if not matches:
        return ()
    if len(matches) == 1:
        return (f"Did you mean {matches[0]!r}?",)
    listed = ", ".join(repr(match) for match in matches[:-1])
    return (f"Did you mean {listed} or {matches[-1]!r}?",)
