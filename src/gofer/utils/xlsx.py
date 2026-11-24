"""Read the rows of an .xlsx workbook's active sheet with python-calamine.

Workbooks often come from outside, so the archive is checked before calamine
parses it: calamine loads a whole sheet into memory, and a small file can
inflate to gigabytes. The check reads only the zip directory. calamine itself
refuses entries that inflate past their declared size and XML that declares
entities, so the declared sizes bound the work.

Values match what openpyxl's ``load_workbook(read_only=True, data_only=True)``
returned before Raticode switched readers: empty cells are ``None``, whole
numbers are ``int``, dates are ``datetime``, and every row starts at column A.
Error cells such as ``#DIV/0!`` read as ``None``; calamine does not expose them.
"""

from __future__ import annotations

import importlib.util
import re
import zipfile
from collections.abc import Iterator
from datetime import date, datetime, time
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from python_calamine import CalamineWorkbook

XLSX_INSTALL_HINT = "python-calamine is required for .xlsx support: pip install 'raticode[xlsx]'"
# Inflated size allowed for the whole archive: this floor, or this many times the
# per-file read limit when that is larger. Ordinary workbooks inflate 5 to 15 times.
XLSX_MIN_UNCOMPRESSED_BYTES = 64 * 1024 * 1024
XLSX_EXPANSION_FACTOR = 32
XLSX_MAX_ENTRIES = 10_000
# Excel writes up to 15 significant digits before switching to exponent form.
_WHOLE_NUMBER_LIMIT = 1e15
_WORKBOOK_XML = "xl/workbook.xml"
_WORKBOOK_XML_LIMIT = 4 * 1024 * 1024
_ACTIVE_TAB = re.compile(
    rb"<(?:[\w.-]+:)?workbookView\b[^>]*?\sactiveTab\s*=\s*[\"'](\d{1,6})[\"']"
)


class XlsxReadError(ValueError):
    """The file is not a workbook Raticode will read."""


def xlsx_reader_available() -> bool:
    return importlib.util.find_spec("python_calamine") is not None


def xlsx_uncompressed_limit(max_file_bytes: int | None) -> int:
    if max_file_bytes is None:
        return XLSX_MIN_UNCOMPRESSED_BYTES
    return max(XLSX_MIN_UNCOMPRESSED_BYTES, max_file_bytes * XLSX_EXPANSION_FACTOR)


def _check_archive(path: Path, limit: int) -> int:
    """Return the active sheet index after checking the archive's declared sizes."""
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > XLSX_MAX_ENTRIES:
                raise XlsxReadError(
                    f"{path.name} has {len(entries)} archive entries; the limit is "
                    f"{XLSX_MAX_ENTRIES}."
                )
            inflated = sum(entry.file_size for entry in entries)
            if inflated > limit:
                raise XlsxReadError(
                    f"{path.name} expands to {inflated} bytes; the limit is {limit} bytes."
                )
            try:
                workbook = archive.getinfo(_WORKBOOK_XML)
            except KeyError:
                return 0
            if workbook.file_size > _WORKBOOK_XML_LIMIT:
                return 0
            match = _ACTIVE_TAB.search(archive.read(workbook))
    except zipfile.BadZipFile as exc:
        raise XlsxReadError(f"{path.name} is not a readable .xlsx workbook.") from exc
    return int(match.group(1)) if match else 0


def _cell(value: Any) -> Any:
    if value == "":
        return None
    if isinstance(value, float) and value.is_integer() and abs(value) < _WHOLE_NUMBER_LIMIT:
        return int(value)
    if isinstance(value, date) and not isinstance(value, datetime):
        return datetime.combine(value, time())
    return value


class XlsxRows:
    """Rows of the active sheet as tuples; close it, or use it in a ``with`` block."""

    def __init__(self, workbook: CalamineWorkbook, rows: Iterator[tuple[Any, ...]]) -> None:
        self._workbook: CalamineWorkbook | None = workbook
        self._rows = rows

    def __iter__(self) -> Iterator[tuple[Any, ...]]:
        return self._rows

    def __next__(self) -> tuple[Any, ...]:
        return next(self._rows)

    @property
    def closed(self) -> bool:
        return self._workbook is None

    def close(self) -> None:
        workbook, self._workbook = self._workbook, None
        if workbook is not None:
            workbook.close()

    def __enter__(self) -> XlsxRows:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def open_xlsx_rows(path: Path, *, max_file_bytes: int | None = None) -> XlsxRows:
    """Open the active sheet's rows, padded from column A like openpyxl's read-only rows.

    Raises ImportError without the xlsx extra and XlsxReadError for a file that
    is not a workbook, inflates past the limit, or that calamine cannot parse.
    """
    try:
        import python_calamine
    except ImportError as exc:
        raise ImportError(XLSX_INSTALL_HINT) from exc
    active = _check_archive(path, xlsx_uncompressed_limit(max_file_bytes))
    try:
        workbook = python_calamine.CalamineWorkbook.from_path(path)
    except python_calamine.CalamineError as exc:
        raise XlsxReadError(f"{path.name} is not a readable .xlsx workbook: {exc}") from exc
    try:
        if not 0 <= active < len(workbook.sheet_names):
            active = 0
        sheet = workbook.get_sheet_by_index(active)
    except python_calamine.CalamineError as exc:
        workbook.close()
        raise XlsxReadError(f"{path.name} has no readable sheet: {exc}") from exc
    except BaseException:
        workbook.close()
        raise
    start = sheet.start
    # iter_rows already starts at row 1 but skips empty leading columns.
    padding: tuple[None, ...] = (None,) * start[1] if start is not None else ()
    raw_rows = sheet.iter_rows() if start is not None else iter(())
    rows = (padding + tuple(_cell(value) for value in row) for row in raw_rows)
    return XlsxRows(workbook, rows)
