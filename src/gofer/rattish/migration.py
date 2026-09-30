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
        try:
            same_file = not target.is_symlink() and os.path.samefile(path, target)
        except FileNotFoundError:
            # A concurrent migration can unlink the source during samefile's stat.
            # Only accept completion if an ordinary destination still exists.
            if not path.exists() and not target.is_symlink() and target.is_file():
                return target
            raise
        if not same_file:
            raise FileExistsError(
                f"Cannot migrate {path}: {target} already exists. Keep or rename one of the files."
            ) from None
    path.unlink(missing_ok=True)
    return target
