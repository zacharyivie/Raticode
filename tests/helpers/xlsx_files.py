"""Write small .xlsx files for tests without a spreadsheet library."""

# ruff: noqa: E501

from __future__ import annotations

import zipfile
from collections.abc import Sequence
from pathlib import Path
from xml.sax.saxutils import escape

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
{sheets}
</Types>"""
_ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""
# Style 1 is a date (numFmt 14), style 2 a date and time (numFmt 22).
_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="1"><font/></fonts><fills count="1"><fill><patternFill patternType="none"/></fill></fills><borders count="1"><border/></borders>
<cellStyleXfs count="1"><xf/></cellStyleXfs>
<cellXfs count="3"><xf numFmtId="0"/><xf numFmtId="14" applyNumberFormat="1"/><xf numFmtId="22" applyNumberFormat="1"/></cellXfs>
</styleSheet>"""

DATE_STYLE = 1
DATETIME_STYLE = 2


def _column(index: int) -> str:
    name = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        name = chr(65 + rest) + name
    return name


def cell_xml(ref: str, value: object) -> str:
    """Return one <c> element: str, bool, int, float, None, or a raw ("xml", text) pair."""
    if value is None:
        return ""
    if isinstance(value, tuple) and value[0] == "xml":
        return str(value[1]).replace("{ref}", ref)
    if isinstance(value, bool):
        return f'<c r="{ref}" t="b"><v>{int(value)}</v></c>'
    if isinstance(value, int | float):
        return f'<c r="{ref}"><v>{value}</v></c>'
    return (
        f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{escape(str(value))}</t></is></c>'
    )


def sheet_xml(rows: Sequence[Sequence[object]], *, first_row: int = 1) -> str:
    body = []
    for row_index, row in enumerate(rows, start=first_row):
        cells = "".join(
            cell_xml(f"{_column(col)}{row_index}", value) for col, value in enumerate(row)
        )
        body.append(f'<row r="{row_index}">{cells}</row>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{''.join(body)}</sheetData></worksheet>"
    )


def write_xlsx(
    path: Path,
    sheets: Sequence[Sequence[Sequence[object]] | str],
    *,
    active: int | None = None,
    names: Sequence[str] | None = None,
    compression: int = zipfile.ZIP_DEFLATED,
) -> Path:
    """Write a workbook; each sheet is rows of values or a complete worksheet XML string."""
    names = list(names or [f"Sheet{index + 1}" for index in range(len(sheets))])
    view = "" if active is None else f'<bookViews><workbookView activeTab="{active}"/></bookViews>'
    sheet_entries = "".join(
        f'<sheet name="{escape(name)}" sheetId="{index + 1}" r:id="rId{index + 1}"/>'
        for index, name in enumerate(names)
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f"{view}<sheets>{sheet_entries}</sheets></workbook>"
    )
    rels = "".join(
        f'<Relationship Id="rId{index + 1}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        f'Target="worksheets/sheet{index + 1}.xml"/>'
        for index in range(len(sheets))
    )
    rels += (
        f'<Relationship Id="rId{len(sheets) + 1}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/>'
    )
    overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{index + 1}.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for index in range(len(sheets))
    )
    with zipfile.ZipFile(path, "w", compression) as archive:
        archive.writestr("[Content_Types].xml", _CONTENT_TYPES.format(sheets=overrides))
        archive.writestr("_rels/.rels", _ROOT_RELS)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f"{rels}</Relationships>",
        )
        archive.writestr("xl/styles.xml", _STYLES)
        for index, sheet in enumerate(sheets):
            xml = sheet if isinstance(sheet, str) else sheet_xml(sheet)
            archive.writestr(f"xl/worksheets/sheet{index + 1}.xml", xml)
    return path
