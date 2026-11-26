"""File inputs: a workflow input whose schema is ``{"type": "string", "format": "file"}``.

The input's value is a path. The input can also set ``accept``, a list of file name
extensions (``.pdf``) and MIME patterns (``image/*``), and ``max-size``, which defaults
to 20 MB, the same limit as a file attached to Rem's chat.

When a run starts, Raticode checks the file and copies it into the run's folder, beside
the run record, as ``<run_id>.files/<input>/<file name>``. The folders are owner-only
(0700) and the copy is readable only by this user (0600). The input is then bound to the
copy's absolute path, so later changes to the original can't change the run, and the
saved inputs (``run_values``) record that path. The copy's SHA-256 is taken while it is
written, so a cached step (``result_cache``) can name the file by its contents instead of
by this run's path without reading it again. Resume, Run again and Run from here give
the new run its own copy of the earlier run's copy, a hard link where the file system
allows one, so deleting either run never takes the other's file. ``delete_run_files``
removes the folder with the run, and ``remove_orphaned_files`` sweeps folders whose run
record is gone.

A paired phone uploads its file into ``<data>/workflow-uploads/<id>/``; a run that copies
an upload removes it, and uploads nobody ran are swept after an hour.

A trigger has nobody to choose a file, so it can't fill a file input. Preflight for a
triggered run says so when the input is required, and a trigger's own inputs refuse one.
"""

from __future__ import annotations

import fnmatch
import hashlib
import mimetypes
import os
import re
import shutil
import stat
import time
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

FILE_FORMAT = "file"
# The same limit as one file attached to Rem's chat (CHAT_ATTACHMENT_MAX_FILE_BYTES).
DEFAULT_MAX_SIZE = 20 * 1024 * 1024
# The largest max-size an input can declare.
MAX_MAX_SIZE = 1024 * 1024 * 1024
MAX_ACCEPT_PATTERNS = 32
FILES_SUFFIX = ".files"
UPLOADS_FOLDER = "workflow-uploads"
UPLOAD_MAX_AGE_SECONDS = 3600
# A run copies its files just before it writes its first record, so a files folder
# younger than this may belong to a run that is still starting.
ORPHAN_GRACE_SECONDS = 600
_COPY_CHUNK = 1024 * 1024
_NAME_LIMIT = 160

_EXTENSION = re.compile(r"\.[a-z0-9][a-z0-9._+-]{0,31}")
_MIME_PART = r"[a-z0-9][a-z0-9!#$&^_.+-]{0,63}"
_MIME = re.compile(rf"{_MIME_PART}/(?:\*|{_MIME_PART})")
_SIZE = re.compile(r"\s*(\d+(?:\.\d+)?)\s*(b|bytes?|kb|kib|mb|mib|gb|gib)?\s*", re.IGNORECASE)
_UNITS = {
    None: 1,
    "b": 1,
    "byte": 1,
    "bytes": 1,
    # Sizes follow Raticode's other limits: 1 MB is 1,048,576 bytes.
    "kb": 1024,
    "kib": 1024,
    "mb": 1024**2,
    "mib": 1024**2,
    "gb": 1024**3,
    "gib": 1024**3,
}


class FileInputError(ValueError):
    """A file input's value can't be used, with a sentence for the user.

    A run that raises it ends as ``invalid_inputs``, like any other invalid input.
    """


@dataclass(frozen=True, slots=True)
class FileInputSpec:
    """One file input: the extensions and MIME patterns it takes and its size limit."""

    name: str
    accept: tuple[str, ...] = ()
    max_size: int = DEFAULT_MAX_SIZE

    def document(self) -> dict[str, Any]:
        return {"accept": list(self.accept), "max_size": self.max_size}


def is_file_schema(schema: Any) -> bool:
    """Whether an input schema declares a file: a string with ``format: file``."""
    return (
        isinstance(schema, Mapping)
        and schema.get("type") == "string"
        and schema.get("format") == FILE_FORMAT
    )


def contains_file_format(schema: Any, depth: int = 0) -> bool:
    """Whether ``format: file`` appears anywhere in a schema."""
    if depth > 32:
        return False
    if isinstance(schema, Mapping):
        if schema.get("format") == FILE_FORMAT:
            return True
        return any(contains_file_format(value, depth + 1) for value in schema.values())
    if isinstance(schema, list):
        return any(contains_file_format(value, depth + 1) for value in schema)
    return False


def parse_accept(value: Any) -> tuple[list[str], str | None]:
    """Canonical accept patterns, or an error sentence."""
    items = [value] if isinstance(value, str) else value
    if not isinstance(items, list) or not items:
        return [], "accept must be a file extension, a MIME pattern, or a list of them."
    if len(items) > MAX_ACCEPT_PATTERNS:
        return [], f"accept takes at most {MAX_ACCEPT_PATTERNS} patterns."
    patterns: list[str] = []
    for item in items:
        if not isinstance(item, str):
            return [], 'accept must list strings, such as ".pdf" or "image/*".'
        text = item.strip().lower()
        if not (_EXTENSION.fullmatch(text) or _MIME.fullmatch(text)):
            return [], (
                f"{item!r} isn't a file extension or a MIME pattern. "
                'Use one such as ".pdf", "image/png" or "image/*".'
            )
        if text not in patterns:
            patterns.append(text)
    return patterns, None


def parse_size(value: Any) -> tuple[int | None, str | None]:
    """A size in bytes from an integer or text such as ``"5 MB"``, or an error sentence."""
    if isinstance(value, bool):
        size = None
    elif isinstance(value, int):
        size = value
    elif isinstance(value, str) and (match := _SIZE.fullmatch(value)):
        unit = match.group(2).lower() if match.group(2) else None
        size = int(float(match.group(1)) * _UNITS[unit])
    else:
        size = None
    if size is None:
        return None, 'max-size must be a number of bytes or a size such as "5 MB".'
    if size <= 0:
        return None, "max-size must be more than 0 bytes."
    if size > MAX_MAX_SIZE:
        return None, "max-size can be at most 1 GB."
    return size, None


def format_size(size: int) -> str:
    """A size as people read it: ``20 MB``, ``1.5 KB``, ``312 bytes``."""
    for unit, scale in (("GB", 1024**3), ("MB", 1024**2), ("KB", 1024)):
        if size >= scale:
            text = f"{size / scale:.1f}".removesuffix(".0")
            return f"{text} {unit}"
    return f"{size} byte" + ("" if size == 1 else "s")


def file_input_specs(ir: Mapping[str, Any]) -> dict[str, FileInputSpec]:
    """The workflow's file inputs by name."""
    specs: dict[str, FileInputSpec] = {}
    for item in ir["workflow"].get("inputs") or []:
        if not is_file_schema(item.get("schema")):
            continue
        raw = item.get("file")
        options: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
        listed = options.get("accept")
        accept: list[Any] = listed if isinstance(listed, list) else []
        size = options.get("max_size")
        specs[str(item["name"])] = FileInputSpec(
            str(item["name"]),
            tuple(str(pattern) for pattern in accept),
            size if isinstance(size, int) and not isinstance(size, bool) else DEFAULT_MAX_SIZE,
        )
    return specs


def accepts(spec: FileInputSpec, name: str, mime: str | None = None) -> bool:
    """Whether a file with this name (and MIME type, when known) fits ``accept``."""
    if not spec.accept:
        return True
    lowered = name.lower()
    types = {
        value.lower()
        for value in (mime, mimetypes.guess_type(lowered, strict=False)[0])
        if isinstance(value, str) and value
    }
    for pattern in spec.accept:
        if pattern.startswith("."):
            if lowered.endswith(pattern):
                return True
        elif any(fnmatch.fnmatchcase(kind, pattern) for kind in types):
            return True
    return False


def accept_text(spec: FileInputSpec) -> str:
    return ", ".join(spec.accept)


def describe(spec: FileInputSpec) -> str:
    """What the input takes, for help text: ``.pdf, image/* up to 20 MB``."""
    limit = f"up to {format_size(spec.max_size)}"
    return f"{accept_text(spec)} {limit}" if spec.accept else f"Any file {limit}"


def _resolve(value: Any, name: str, base: Path | None) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise FileInputError(f"File input {name!r} needs the path of a file.")
    if "\0" in value:
        raise FileInputError(f"File input {name!r} isn't a usable path.")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = (base or Path.cwd()) / path
    return path


def check_file(
    spec: FileInputSpec, value: Any, *, base: Path | None = None, mime: str | None = None
) -> Path:
    """The absolute path of an existing file that fits the input, or ``FileInputError``.

    A relative path is taken from ``base``, or the current folder without one.
    """
    path = _resolve(value, spec.name, base)
    try:
        info = path.stat()
    except FileNotFoundError:
        raise FileInputError(f"File input {spec.name!r}: {path} doesn't exist.") from None
    except OSError as exc:
        raise FileInputError(
            f"File input {spec.name!r}: {path} can't be read ({exc.strerror or exc})."
        ) from None
    if not stat.S_ISREG(info.st_mode):
        raise FileInputError(f"File input {spec.name!r}: {path} is not a file.")
    _check_size(spec, path.name, info.st_size)
    _check_kind(spec, path.name, mime)
    return path.resolve()


def _check_size(spec: FileInputSpec, name: str, size: int) -> None:
    if size > spec.max_size:
        raise FileInputError(
            f"File input {spec.name!r}: {name} is {format_size(size)}, more than the "
            f"{format_size(spec.max_size)} this input takes."
        )


def _check_kind(spec: FileInputSpec, name: str, mime: str | None) -> None:
    if not accepts(spec, name, mime):
        raise FileInputError(
            f"File input {spec.name!r}: {name} isn't a kind of file this input takes "
            f"({accept_text(spec)})."
        )


def check_file_inputs(
    ir: Mapping[str, Any], inputs: Mapping[str, Any], *, base: Path | None = None
) -> dict[str, Any]:
    """``inputs`` with each file input's path checked and made absolute.

    The CLI calls this before a run starts so a missing or unsuitable file is
    refused without a run record; the run checks it again when it copies the file.
    """
    result = dict(inputs)
    for name, spec in file_input_specs(ir).items():
        if name in inputs and inputs[name] is not None:
            result[name] = str(check_file(spec, inputs[name], base=base))
    return result


def safe_file_name(name: str, fallback: str = "file") -> str:
    """A file name safe to create: no folders, control characters or leading dots."""
    text = unicodedata.normalize("NFC", str(name or ""))
    text = text.replace("\\", "/").rsplit("/", 1)[-1]
    text = "".join(
        character
        for character in text
        if unicodedata.category(character)[0] != "C" and character not in '<>:"|?*'
    )
    text = text.strip().lstrip(".").strip()
    if not text:
        return fallback
    if len(text.encode("utf-8")) > _NAME_LIMIT:
        stem, dot, suffix = text.rpartition(".")
        suffix = f".{suffix}" if dot and 0 < len(suffix) <= 16 else ""
        stem = stem if suffix else text
        while len((stem + suffix).encode("utf-8")) > _NAME_LIMIT:
            stem = stem[:-1]
        text = (stem + suffix) or fallback
    return text


def run_files_dir(record: Path) -> Path:
    """The folder holding the file input copies of the run whose record is ``record``."""
    # Writes and removals refuse linked parents, so name the folder by its real path.
    return record.parent.resolve() / f"{record.stem}{FILES_SUFFIX}"


def uploads_dir(data_dir: Path) -> Path:
    return data_dir.resolve() / UPLOADS_FOLDER


def _private_dir(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise FileInputError("Raticode couldn't make a private folder for this run's files.")
    if stat.S_IMODE(info.st_mode) != 0o700:
        os.chmod(path, 0o700)


def _copy_into(source: Path, destination: Path, spec: FileInputSpec) -> str:
    """Copy at most ``spec.max_size`` bytes of a regular file into a new 0600 file.

    Returns the SHA-256 of the bytes copied, as hex.
    """
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        reader = os.open(source, flags)
    except OSError as exc:
        raise FileInputError(
            f"File input {spec.name!r}: {source} can't be read ({exc.strerror or exc})."
        ) from None
    try:
        info = os.fstat(reader)
        # Checked again on the open file, in case the path changed since it was checked.
        if not stat.S_ISREG(info.st_mode):
            raise FileInputError(f"File input {spec.name!r}: {source} is not a file.")
        _check_size(spec, source.name, info.st_size)
        writer = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        copied = 0
        digest = hashlib.sha256()
        try:
            while chunk := os.read(reader, _COPY_CHUNK):
                copied += len(chunk)
                if copied > spec.max_size:
                    raise FileInputError(
                        f"File input {spec.name!r}: {source.name} grew past "
                        f"{format_size(spec.max_size)} while it was copied."
                    )
                digest.update(chunk)
                view = memoryview(chunk)
                while view:
                    view = view[os.write(writer, view) :]
            os.fsync(writer)
        except BaseException:
            os.close(writer)
            writer = -1
            destination.unlink(missing_ok=True)
            raise
        finally:
            if writer >= 0:
                os.close(writer)
    finally:
        os.close(reader)
    return digest.hexdigest()


def _file_digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _link_or_copy(source: Path, destination: Path, spec: FileInputSpec) -> str:
    """Link or copy an earlier run's copy; returns the new copy's SHA-256 as hex."""
    try:
        info = source.lstat()
    except OSError:
        info = None
    if info is not None and stat.S_ISREG(info.st_mode):
        try:
            os.link(source, destination, follow_symlinks=False)
            return _file_digest(destination)
        except OSError:
            pass
    return _copy_into(source, destination, spec)


def _inside(path: Path, folder: Path) -> bool:
    try:
        return path.is_relative_to(folder)
    except ValueError:
        return False


def missing_copies(
    ir: Mapping[str, Any] | None, inputs: Mapping[str, Any], earlier_record: Path
) -> list[str]:
    """File inputs whose copy in the earlier run's folder is gone.

    Without ``ir``, every saved value that points into that folder counts.
    """
    folder = run_files_dir(earlier_record)
    missing = []
    for name in file_input_specs(ir) if ir is not None else sorted(inputs):
        value = inputs.get(name)
        if not isinstance(value, str):
            continue
        path = Path(value)
        if _inside(path, folder) and not path.is_file():
            missing.append(name)
    return missing


def missing_copies_message(names: list[str]) -> str:
    listed = ", ".join(repr(name) for name in names)
    one = len(names) == 1
    return (
        f"This run's copy of the file for {'input' if one else 'inputs'} {listed} "
        f"{'is' if one else 'are'} gone, so it can't be used again. "
        "Start a new run and choose the file again."
    )


@dataclass(frozen=True, slots=True)
class StagedInputs:
    """A run's inputs bound to its file copies, and what each copy holds.

    ``contents`` maps each copy's path to ``file:<input>/<name>#sha256:<digest>``, which
    the result cache uses in place of the path, so a new copy of the same bytes keeps
    its saved answers.
    """

    inputs: dict[str, Any]
    contents: dict[str, str]


def content_token(input_name: str, file_name: str, digest: str) -> str:
    return f"file:{input_name}/{file_name}#sha256:{digest}"


def stage_file_inputs(
    ir: Mapping[str, Any],
    inputs: Mapping[str, Any],
    record: Path,
    *,
    data_dir: Path,
    reuse_from: Path | None = None,
    base: Path | None = None,
) -> StagedInputs:
    """Copy each file input into the run folder; ``inputs`` bound to the copies.

    ``reuse_from`` is the record of the run whose saved inputs these are (Resume, Run
    again and Run from here): a value inside that run's files folder is that run's copy,
    and the new run gets its own link or copy of it. Any other value is a path to check
    and copy, relative to ``base`` (the project folder) when it isn't absolute. Raises
    ``FileInputError``, which ends the run as ``invalid_inputs``.
    """
    specs = file_input_specs(ir)
    supplied = [name for name in specs if isinstance(inputs.get(name), str)]
    if not supplied:
        return StagedInputs(dict(inputs), {})
    folder = run_files_dir(record)
    earlier = run_files_dir(reuse_from) if reuse_from is not None else None
    uploads = uploads_dir(data_dir)
    result = dict(inputs)
    contents: dict[str, str] = {}
    consumed: list[Path] = []
    try:
        _private_dir(folder)
        for name in supplied:
            spec = specs[name]
            value = Path(str(inputs[name]))
            target_dir = folder / safe_file_name(name, "input")
            _private_dir(target_dir)
            if earlier is not None and _inside(value, earlier):
                if not value.is_file():
                    raise FileInputError(missing_copies_message([name]))
                destination = target_dir / safe_file_name(value.name)
                digest = _link_or_copy(value, destination, spec)
            else:
                source = check_file(spec, str(value), base=base)
                destination = target_dir / safe_file_name(source.name)
                digest = _copy_into(source, destination, spec)
                if _inside(source, uploads):
                    consumed.append(source)
            result[name] = str(destination)
            contents[str(destination)] = content_token(name, destination.name, digest)
    except FileInputError:
        remove_tree(folder)
        raise
    except OSError as exc:
        remove_tree(folder)
        raise FileInputError(
            f"Raticode couldn't copy this run's files: {exc.strerror or exc}."
        ) from None
    for upload in consumed:
        remove_upload(upload, data_dir)
    return StagedInputs(result, contents)


def remove_tree(folder: Path) -> None:
    """Delete a files folder. A symlink in its place is removed, never followed."""
    try:
        info = folder.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISDIR(info.st_mode):
        folder.unlink(missing_ok=True)
        return
    # rmtree removes symlinks inside the folder without following them.
    shutil.rmtree(folder, ignore_errors=True)


def remove_upload(path: Path, data_dir: Path) -> None:
    """Delete a phone upload's folder once a run has its copy."""
    root = uploads_dir(data_dir)
    parent = path.parent
    if parent.parent == root and _inside(parent, root):
        remove_tree(parent)


def new_upload_path(data_dir: Path, token: str, name: str) -> Path:
    """A fresh owner-only folder for one phone upload, and the file path inside it."""
    root = uploads_dir(data_dir)
    _private_dir(root)
    folder = root / token
    folder.mkdir(mode=0o700)
    return folder / safe_file_name(name)


def sweep_uploads(data_dir: Path, *, now: float | None = None) -> None:
    """Delete phone uploads no run used within an hour."""
    root = uploads_dir(data_dir)
    cutoff = (time.time() if now is None else now) - UPLOAD_MAX_AGE_SECONDS
    try:
        entries = list(root.iterdir())
    except OSError:
        return
    for entry in entries:
        try:
            if entry.lstat().st_mtime > cutoff:
                continue
        except OSError:
            continue
        remove_tree(entry)


def remove_orphaned_files(folder: Path, *, now: float | None = None) -> list[str]:
    """Delete files folders whose run record is gone; return the run IDs removed."""
    removed: list[str] = []
    try:
        candidates = list(folder.glob(f"*{FILES_SUFFIX}"))
    except OSError:
        return removed
    cutoff = (time.time() if now is None else now) - ORPHAN_GRACE_SECONDS
    for path in candidates:
        run_id = path.name.removesuffix(FILES_SUFFIX)
        if (folder / f"{run_id}.json").exists():
            continue
        try:
            if path.lstat().st_mtime > cutoff:
                continue
        except OSError:
            continue
        remove_tree(folder.resolve() / path.name)
        removed.append(run_id)
    return removed
