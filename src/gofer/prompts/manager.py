from __future__ import annotations

import re
from pathlib import Path


class PromptManager:
    def __init__(self, search_dirs: list[Path] | None = None) -> None:
        self._search_dirs: list[Path] = search_dirs or []

    def load(self, path: Path, context: dict[str, object]) -> str:
        resolved = self._resolve_path(path)
        text = resolved.read_text(encoding="utf-8")
        return self._interpolate(text, context)

    def _resolve_path(self, path: Path) -> Path:
        if path.is_absolute():
            return path
        for d in self._search_dirs:
            candidate = d / path
            if candidate.exists():
                return candidate
        return path  # let caller get a FileNotFoundError if missing

    @staticmethod
    def _interpolate(text: str, context: dict[str, object]) -> str:
        def replacer(match: re.Match[str]) -> str:
            key = match.group(1).strip()
            parts = key.split(".")
            value: object = context
            for part in parts:
                if not isinstance(value, dict):
                    return match.group(0)
                value = value.get(part, match.group(0))
            return str(value)

        return re.sub(r"\{\{([^}]+)\}\}", replacer, text)
