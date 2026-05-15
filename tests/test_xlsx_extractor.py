from __future__ import annotations

import io
import unittest
import zipfile
import xml.etree.ElementTree as ET

from app.extractors.xlsx_extractor import (
    MAX_XLSX_DOWNLOAD_BYTES,
    MAX_XLSX_UNCOMPRESSED_BYTES,
    extract_from_xlsx_bytes,
)

_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def _make_xlsx(shared_strings: list[str], rows: list[list[tuple[str, str]]]) -> bytes:
    """Build a minimal valid XLSX in memory.

    rows: list of rows; each row is a list of (type, value) cell tuples.
      type "s" = shared-string index (value is the index as string),
      type ""  = inline number/text.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # [Content_Types].xml — minimal stub
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            "</Types>",
        )

        # Shared strings
        if shared_strings:
            ss_root = ET.Element(f"{{{_NS}}}sst", count=str(len(shared_strings)), uniqueCount=str(len(shared_strings)))
            for s in shared_strings:
                si = ET.SubElement(ss_root, f"{{{_NS}}}si")
                t = ET.SubElement(si, f"{{{_NS}}}t")
                t.text = s
            zf.writestr("xl/sharedStrings.xml", ET.tostring(ss_root, encoding="unicode"))

        # Sheet
        ws_root = ET.Element(f"{{{_NS}}}worksheet")
        sd = ET.SubElement(ws_root, f"{{{_NS}}}sheetData")
        for r_idx, row_cells in enumerate(rows, start=1):
            row_el = ET.SubElement(sd, f"{{{_NS}}}row", r=str(r_idx))
            for c_idx, (t, val) in enumerate(row_cells, start=1):
                col_letter = chr(ord("A") + c_idx - 1)
                attrs: dict[str, str] = {"r": f"{col_letter}{r_idx}"}
                if t:
                    attrs["t"] = t
                c_el = ET.SubElement(row_el, f"{{{_NS}}}c", **attrs)
                v_el = ET.SubElement(c_el, f"{{{_NS}}}v")
                v_el.text = val
        zf.writestr("xl/worksheets/sheet1.xml", ET.tostring(ws_root, encoding="unicode"))

    return buf.getvalue()


class XlsxExtractorTest(unittest.TestCase):
    def test_extracts_shared_string_cells(self) -> None:
        shared = ["Субсидия", "Грант АПК", "Возмещение затрат"]
        rows = [[("s", "0"), ("s", "1"), ("s", "2")]]
        data = _make_xlsx(shared, rows)

        result = extract_from_xlsx_bytes(data)

        self.assertEqual(result.document_type, "xlsx")
        self.assertIsNone(result.error)
        self.assertIn("Субсидия", result.raw_text)
        self.assertIn("Грант АПК", result.raw_text)
        self.assertIn("Возмещение затрат", result.raw_text)

    def test_extracts_numeric_cells(self) -> None:
        rows = [[("", "12345"), ("", "67890")]]
        data = _make_xlsx([], rows)

        result = extract_from_xlsx_bytes(data)

        self.assertIn("12345", result.raw_text)
        self.assertIn("67890", result.raw_text)

    def test_multiple_rows_each_on_own_line(self) -> None:
        shared = ["Строка один", "Строка два"]
        rows = [[("s", "0")], [("s", "1")]]
        data = _make_xlsx(shared, rows)

        result = extract_from_xlsx_bytes(data)

        lines = result.raw_text.strip().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn("Строка один", lines[0])
        self.assertIn("Строка два", lines[1])

    def test_empty_sheet_returns_empty_text(self) -> None:
        data = _make_xlsx([], [])

        result = extract_from_xlsx_bytes(data)

        self.assertEqual(result.raw_text, "")
        self.assertIsNone(result.error)
        self.assertEqual(result.document_type, "xlsx")

    def test_rejects_oversized_download(self) -> None:
        oversized = b"x" * (MAX_XLSX_DOWNLOAD_BYTES + 1)

        result = extract_from_xlsx_bytes(oversized)

        self.assertEqual(result.raw_text, "")
        self.assertIsNotNone(result.error)
        self.assertIn("too large", result.error)

    def test_rejects_bad_zip(self) -> None:
        result = extract_from_xlsx_bytes(b"not a zip file at all")

        self.assertEqual(result.raw_text, "")
        self.assertIsNotNone(result.error)
        self.assertIn("bad zip", result.error.lower())

    def test_no_shared_strings_file_is_handled(self) -> None:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("xl/worksheets/sheet1.xml", "<worksheet/>")
        data = buf.getvalue()

        result = extract_from_xlsx_bytes(data)

        self.assertEqual(result.document_type, "xlsx")
        self.assertEqual(result.raw_text, "")

    def test_extracted_text_length_matches(self) -> None:
        shared = ["АПК данные"]
        rows = [[("s", "0")]]
        data = _make_xlsx(shared, rows)

        result = extract_from_xlsx_bytes(data)

        self.assertEqual(result.extracted_text_length, len(result.raw_text.strip()))


if __name__ == "__main__":
    unittest.main()
