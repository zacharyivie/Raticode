"""Case-only twin files: two paths in one brain that differ only by letter case.

Linux keeps ``a/Note.md`` and ``a/note.md`` apart, but macOS and Windows folders
treat them as one file, and a brain package refuses names like that. Finding them
early names both files instead of failing a backup after every file was read.
"""

from __future__ import annotations

import os
import unicodedata
from collections.abc import Iterable
from pathlib import Path


class CaseTwinError(ValueError):
    """A file would sit next to one whose name differs only by letter case."""


def fold_name(path: str) -> str:
    """The form under which macOS and Windows see two names as one file.

    Letter case is folded, and so is how accents are written: macOS stores "é" as
    one character or as "e" plus an accent and treats both spellings as one name.
    """
    return unicodedata.normalize("NFC", path).casefold()


def case_twins(paths: Iterable[str]) -> list[tuple[str, str]]:
    """Pairs of paths that differ only by letter case, each as (first seen, twin)."""
    first: dict[str, str] = {}
    pairs: list[tuple[str, str]] = []
    for path in paths:
        folded = fold_name(path)
        if folded in first:
            if first[folded] != path:
                pairs.append((first[folded], path))
        else:
            first[folded] = path
    return pairs


def twin_message(first: str, second: str) -> str:
    return (
        f"{first} and {second} differ only by letter case. macOS and Windows treat them "
        "as one file, so a brain package can't hold both. Rename or remove one of them."
    )


def brain_twins(root: Path) -> list[tuple[str, str]]:
    """Case-only twin files in a brain folder, as paths relative to it.

    Walks what a complete backup holds: everything but ``.raticode``, ``.git`` and
    links. Unreadable folders are skipped.
    """
    paths: list[str] = []
    for parent, dirs, names in os.walk(root, followlinks=False):
        here = Path(parent)
        dirs[:] = sorted(
            name
            for name in dirs
            if name not in {".raticode", ".git"} and not (here / name).is_symlink()
        )
        for name in sorted(names):
            path = here / name
            if not path.is_symlink():
                paths.append(path.relative_to(root).as_posix())
    return case_twins(paths)


def existing_twin(root: Path, relative: str) -> tuple[str, str] | None:
    """The existing path that ``relative`` would differ from only by case, if any.

    Each part of ``relative`` is compared with the real names in its folder under
    ``root``. A part that exists with exactly this spelling is fine, even when a twin
    of it already sits beside it; that stays for the user to tidy. Returns (existing,
    new) as paths relative to ``root``, or None.
    """
    folder = root
    parts = Path(relative).parts
    for index, part in enumerate(parts):
        try:
            names = os.listdir(folder)
        except OSError:
            # A folder that doesn't exist yet has nothing to collide with.
            return None
        if part in names:
            folder = folder / part
            continue
        folded = fold_name(part)
        match = next((name for name in sorted(names) if fold_name(name) == folded), None)
        if match is None:
            return None
        prefix = parts[:index]
        return Path(*prefix, match).as_posix(), Path(*prefix, part).as_posix()
    return None


def refuse_twin(root: Path, relative: str) -> None:
    """Raise CaseTwinError naming both paths when ``relative`` would twin one on disk."""
    twin = existing_twin(root, relative)
    if twin:
        existing, new = twin
        raise CaseTwinError(
            f"{existing} already exists, and {new} differs from it only by letter case. "
            "macOS and Windows treat them as one file, and a brain backup can't hold "
            f"both. Use {existing} or choose a different name."
        )
