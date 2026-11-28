"""Segment-by-segment glob matching shared by bundle ignore files and file triggers."""

from __future__ import annotations

import fnmatch


def match_path_parts(path: tuple[str, ...], pattern: tuple[str, ...]) -> bool:
    """Match path segments against pattern segments the way ``Path.glob`` reads them.

    ``*`` and the other wildcards stay within one segment, and a ``**`` segment matches
    zero or more folders, so ``**/*.md`` matches both ``a.md`` and ``x/y/a.md``.

    One pass per pattern segment over the positions the path can have reached, so a
    deeply nested folder (a worker can make one thousands of levels deep) can't run
    out of recursion, and many ``**`` segments can't take exponential time.
    """
    reached = {0}
    for segment in pattern:
        if not reached:
            return False
        if segment == "**":
            reached = set(range(min(reached), len(path) + 1))
        else:
            reached = {
                index + 1
                for index in reached
                if index < len(path) and fnmatch.fnmatchcase(path[index], segment)
            }
    return len(path) in reached
