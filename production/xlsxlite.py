#!/usr/bin/env python3
"""
Minimal multi-sheet .xlsx writer — stdlib only, no openpyxl.

    from xlsxlite import write_workbook
    write_workbook("out.xlsx", [("2026-09-14", [["a", 1], ["b", 2]]), ...])

Exists because the Google Drive MCP exposes Drive, not Sheets: a file can be
created or replaced wholesale, but a TAB cannot be appended to a live
spreadsheet. Uploading a real xlsx and letting Drive convert it is the only way
to get one spreadsheet with a named page per week — and an xlsx is just a zip of
XML, so it needs no dependency at all, which keeps this in line with the rest of
the codebase (stdlib plus curl).

Values are written as inline strings except for ints/floats, which are written
as numbers so the sheet can sum them. Everything is escaped.
"""
import re, zipfile
from xml.sax.saxutils import escape

INVALID = re.compile(r"[\[\]:*?/\\]")
CT = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
{sheets}
</Types>"""
RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""


def safe_name(n, used):
    """Excel tab rules: <=31 chars, none of []:*?/\\ , and unique."""
    n = INVALID.sub("-", str(n))[:31] or "Sheet"
    base, i = n, 2
    while n in used:
        suffix = f"~{i}"
        n = base[:31 - len(suffix)] + suffix
        i += 1
    used.add(n)
    return n


def _col(i):
    s = ""
    while i >= 0:
        s = chr(65 + i % 26) + s
        i = i // 26 - 1
    return s


def _cell(r, c, v):
    ref = f"{_col(c)}{r}"
    if isinstance(v, bool) or v is None or v == "":
        return f'<c r="{ref}"/>'
    if isinstance(v, (int, float)):
        return f'<c r="{ref}"><v>{v}</v></c>'
    return (f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">'
            f'{escape(str(v))}</t></is></c>')


def _sheet_xml(rows):
    out = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
           '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
           "<sheetData>"]
    for ri, row in enumerate(rows, 1):
        cells = "".join(_cell(ri, ci, v) for ci, v in enumerate(row))
        out.append(f'<row r="{ri}">{cells}</row>')
    out.append("</sheetData></worksheet>")
    return "".join(out)


def write_workbook(path, sheets):
    """sheets: [(name, rows)] where rows is a list of lists."""
    used, named = set(), []
    for name, rows in sheets:
        named.append((safe_name(name, used), rows))

    ov = "\n".join(
        f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
        f'ContentType="application/vnd.openxmlformats-officedocument.'
        f'spreadsheetml.worksheet+xml"/>' for i in range(1, len(named) + 1))
    wb_sheets = "".join(
        f'<sheet name="{escape(n)}" sheetId="{i}" r:id="rId{i}"/>'
        for i, (n, _) in enumerate(named, 1))
    wb = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
          'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
          f'<sheets>{wb_sheets}</sheets></workbook>')
    wb_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
               + "".join(
                   f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/'
                   f'officeDocument/2006/relationships/worksheet" '
                   f'Target="worksheets/sheet{i}.xml"/>'
                   for i in range(1, len(named) + 1))
               + "</Relationships>")

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CT.format(sheets=ov))
        z.writestr("_rels/.rels", RELS)
        z.writestr("xl/workbook.xml", wb)
        z.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        for i, (_, rows) in enumerate(named, 1):
            z.writestr(f"xl/worksheets/sheet{i}.xml", _sheet_xml(rows))
    return path
