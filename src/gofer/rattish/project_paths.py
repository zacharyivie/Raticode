"""Project-root path policy shared by Rattish preflight and runtime handlers."""

from __future__ import annotations

from pathlib import Path


def normalize_project_path(authored: str) -> str:
    """Preserve authored filesystem access, including paths outside the project."""
    if not authored or "\x00" in authored:
        raise ValueError("Rattish paths must be nonempty and contain no NUL bytes.")
    return Path(authored).as_posix()


def project_path(project_root: Path, authored: str) -> Path:
    """Resolve relative paths from the project without restricting filesystem access."""
    candidate = Path(normalize_project_path(authored)).expanduser()
    # Preserve the final component so operations on symlinks retain their semantics.
    path = candidate if candidate.is_absolute() else project_root.resolve() / candidate
    return path.parent.resolve() / path.name


def path_kind(path: Path) -> str:
    if path.is_symlink():
        return "symlink"
    if path.is_dir():
        return "directory"
    if path.is_file():
        return "file"
    return "missing"
