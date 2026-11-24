"""The .xlsx reader: openpyxl-compatible values and safety on untrusted workbooks."""

from __future__ import annotations

import struct
import sys
import zipfile
import zlib
from datetime import datetime
from pathlib import Path

import pytest

from gofer.core import health
from gofer.core.executor import _load_tabular
from gofer.utils import xlsx
from gofer.utils.xlsx import XlsxReadError, open_xlsx_rows
from tests.helpers.xlsx_files import write_xlsx

pytest.importorskip("python_calamine")

_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def _date(serial: float, style: int) -> tuple[str, str]:
    return ("xml", f'<c r="{{ref}}" s="{style}"><v>{serial}</v></c>')


def _rows(path: Path, **kwargs: int) -> list[tuple[object, ...]]:
    with open_xlsx_rows(path, **kwargs) as rows:
        return list(rows)


def test_values_match_openpyxl_read_only_data_only(tmp_path: Path) -> None:
    path = write_xlsx(
        tmp_path / "rows.xlsx",
        [
            [
                ["name", "count", "ratio", "day", "stamp", "ok", "error", "formula"],
                [
                    "alice",
                    3,
                    1.5,
                    _date(46197, 1),
                    _date(46197.5, 2),
                    True,
                    ("xml", '<c r="{ref}" t="e"><v>#DIV/0!</v></c>'),
                    ("xml", '<c r="{ref}" t="str"><f>A1</f><v>calc</v></c>'),
                ],
                ["bob", -2.0, None, None, None, False, None, None],
            ]
        ],
    )

    assert _rows(path) == [
        ("name", "count", "ratio", "day", "stamp", "ok", "error", "formula"),
        (
            "alice",
            3,
            1.5,
            datetime(2026, 6, 24),
            datetime(2026, 6, 24, 12),
            True,
            None,
            "calc",
        ),
        ("bob", -2, None, None, None, False, None, None),
    ]
    count = _rows(path)[1][1]
    assert type(count) is int


def test_rows_start_at_column_a_and_row_1_like_openpyxl(tmp_path: Path) -> None:
    sheet = (
        f'<worksheet xmlns="{_NS}"><sheetData>'
        '<row r="3"><c r="C3" t="inlineStr"><is><t>h</t></is></c></row>'
        '<row r="4"><c r="C4"><v>7</v></c><c r="D4"><v>0.25</v></c></row>'
        "</sheetData></worksheet>"
    )
    path = write_xlsx(tmp_path / "offset.xlsx", [sheet])

    assert _rows(path) == [
        (None, None, None, None),
        (None, None, None, None),
        (None, None, "h", None),
        (None, None, 7, 0.25),
    ]


def test_numbers_in_exponent_form_stay_floats(tmp_path: Path) -> None:
    path = write_xlsx(tmp_path / "types.xlsx", [[["big"], [1.152921504606847e18]]])
    (big,) = _rows(path)[1]
    assert isinstance(big, float)


def test_active_sheet_is_read(tmp_path: Path) -> None:
    sheets = [[["first"], [1]], [["second"], [2]]]
    assert _rows(write_xlsx(tmp_path / "a.xlsx", sheets, active=1)) == [("second",), (2,)]
    assert _rows(write_xlsx(tmp_path / "b.xlsx", sheets)) == [("first",), (1,)]
    assert _rows(write_xlsx(tmp_path / "c.xlsx", sheets, active=9)) == [("first",), (1,)]


def test_empty_sheet_has_no_rows_and_tabular_load_returns_nothing(tmp_path: Path) -> None:
    path = write_xlsx(tmp_path / "empty.xlsx", [[]])
    assert _rows(path) == []
    assert _load_tabular(path) == []


def test_workbook_is_closed_after_use(tmp_path: Path) -> None:
    path = write_xlsx(tmp_path / "rows.xlsx", [[["name"], ["a"]]])
    rows = open_xlsx_rows(path)
    with rows:
        assert next(rows) == ("name",)
    assert rows.closed
    rows.close()


def test_inflated_size_over_the_limit_is_refused_before_parsing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = "<row>" + '<c t="inlineStr"><is><t>xxxxxxxxxx</t></is></c>' * 50 + "</row>"
    sheet = f'<worksheet xmlns="{_NS}"><sheetData>{row * 2000}</sheetData></worksheet>'
    path = write_xlsx(tmp_path / "bomb.xlsx", [sheet])
    assert path.stat().st_size < 64 * 1024
    monkeypatch.setattr(xlsx, "XLSX_MIN_UNCOMPRESSED_BYTES", 1024 * 1024)
    # The message comes from the zip-directory check, which runs before calamine parses.
    with pytest.raises(XlsxReadError, match="expands to"):
        open_xlsx_rows(path)
    with pytest.raises(XlsxReadError, match="expands to"):
        _load_tabular(path)


def test_limit_scales_with_the_per_file_read_limit() -> None:
    assert xlsx.xlsx_uncompressed_limit(None) == 64 * 1024 * 1024
    assert xlsx.xlsx_uncompressed_limit(1024) == 64 * 1024 * 1024
    assert xlsx.xlsx_uncompressed_limit(10 * 1024 * 1024) == 320 * 1024 * 1024


def test_entry_that_inflates_past_its_declared_size_is_refused(tmp_path: Path) -> None:
    row = "<row>" + '<c t="inlineStr"><is><t>xxxxxxxxxx</t></is></c>' * 50 + "</row>"
    sheet = f'<worksheet xmlns="{_NS}"><sheetData>{row * 2000}</sheetData></worksheet>'
    path = write_xlsx(tmp_path / "lie.xlsx", [sheet])
    real = sheet.encode()
    claimed = 5000
    data = bytearray(path.read_bytes())
    with zipfile.ZipFile(path) as archive:
        actual = archive.getinfo("xl/worksheets/sheet1.xml").file_size
    patched = 0
    # Local file header and central directory: CRC and uncompressed size fields.
    for signature, crc_at, size_at in ((b"PK\x03\x04", 14, 22), (b"PK\x01\x02", 16, 24)):
        index = data.find(signature)
        while index >= 0:
            if struct.unpack_from("<I", data, index + size_at)[0] == actual:
                struct.pack_into("<I", data, index + crc_at, zlib.crc32(real[:claimed]))
                struct.pack_into("<I", data, index + size_at, claimed)
                patched += 1
            index = data.find(signature, index + 4)
    assert patched == 2
    path.write_bytes(bytes(data))

    with pytest.raises(XlsxReadError):
        _rows(path)


def test_too_many_archive_entries_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_xlsx(tmp_path / "many.xlsx", [[["a"]]])
    monkeypatch.setattr(xlsx, "XLSX_MAX_ENTRIES", 3)
    with pytest.raises(XlsxReadError, match="archive entries"):
        _rows(path)


@pytest.mark.parametrize(
    "doctype",
    [
        '<!DOCTYPE w [<!ENTITY e SYSTEM "file:///etc/hostname">]>',
        '<!DOCTYPE w [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">'
        '<!ENTITY e "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">]>',
    ],
    ids=["external-entity", "entity-expansion"],
)
def test_xml_entities_are_refused(tmp_path: Path, doctype: str) -> None:
    sheet = (
        f'<?xml version="1.0"?>{doctype}<worksheet xmlns="{_NS}"><sheetData>'
        '<row r="1"><c r="A1" t="inlineStr"><is><t>&e;</t></is></c></row>'
        "</sheetData></worksheet>"
    )
    path = write_xlsx(tmp_path / "entities.xlsx", [sheet])
    with pytest.raises(XlsxReadError):
        _rows(path)


def test_files_that_are_not_workbooks_are_refused(tmp_path: Path) -> None:
    text = tmp_path / "text.xlsx"
    text.write_text("name\nalice\n")
    with pytest.raises(XlsxReadError, match="not a readable .xlsx"):
        _rows(text)
    archive = tmp_path / "plain.xlsx"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("hello.txt", "hi")
    with pytest.raises(XlsxReadError):
        _rows(archive)


def test_missing_reader_names_the_extra(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "python_calamine", None)
    with pytest.raises(ImportError, match="raticode\\[xlsx\\]"):
        open_xlsx_rows(tmp_path / "rows.xlsx")


def test_health_reports_the_xlsx_reader(monkeypatch: pytest.MonkeyPatch) -> None:
    present = health._xlsx_diagnostic()
    assert (present.id, present.severity) == ("optional.python_calamine", "ok")
    monkeypatch.setattr(health, "xlsx_reader_available", lambda: False)
    missing = health._xlsx_diagnostic()
    assert (missing.id, missing.severity) == ("optional.python_calamine", "warning")
    assert "raticode[xlsx]" in missing.message
