"""Compatibility for workflow sources authored before the Rattish rename."""

import os
from pathlib import Path


def migrate_source(path: Path) -> Path:
    """Rename a legacy source without changing its bytes or overwriting another file.

    Old references continue resolving after migration. Symlinks are left alone.
    Linking first provides exclusive destination creation even across processes.
    """
    if path.suffix.lower() != ".rad":
        return path
    target = path.with_suffix(".rattish")
    if path.is_symlink():
        return path
    if not path.exists():
        return target if target.is_file() else path
    try:
        os.link(path, target)
    except FileNotFoundError:
        if not path.exists() and target.is_file():
            return target
        raise
    except FileExistsError:
        if not path.exists():
            return target
        if target.is_symlink() or not os.path.samefile(path, target):
            raise FileExistsError(
                f"Cannot migrate {path}: {target} already exists. Keep or rename one of the files."
            ) from None
    path.unlink(missing_ok=True)
    return target
