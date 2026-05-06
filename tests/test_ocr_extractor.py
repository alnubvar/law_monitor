from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from app.extractors.ocr_extractor import extract_text_with_ocr


class OcrExtractorTest(unittest.TestCase):
    def test_extract_text_with_ocr_passes_tessdata_path_to_pymupdf(self) -> None:
        temp_file = Path("data/test_artifacts/ocr_extractor_input.pdf")
        temp_file.parent.mkdir(parents=True, exist_ok=True)
        temp_file.write_bytes(b"%PDF-1.4 test")
        captured_kwargs: dict[str, object] = {}

        class FakePage:
            def get_textpage_ocr(self, **kwargs: object) -> object:
                captured_kwargs.update(kwargs)
                return object()

            def get_text(self, *_args: object, **_kwargs: object) -> str:
                return "OCR text"

        class FakePdf:
            def __init__(self) -> None:
                self._page = FakePage()

            def __enter__(self) -> "FakePdf":
                return self

            def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> bool:
                return False

            def __len__(self) -> int:
                return 1

            def __getitem__(self, _index: int) -> FakePage:
                return self._page

        tessdata_path = r"C:\Program Files\Tesseract-OCR\tessdata"
        try:
            with patch("app.extractors.ocr_extractor.OCR_ENABLED", True):
                with patch(
                    "app.extractors.ocr_extractor._probe_ocr_availability",
                    return_value=(True, None),
                ):
                    with patch("app.extractors.ocr_extractor.fitz.open", return_value=FakePdf()):
                        result = extract_text_with_ocr(
                            temp_file,
                            language="rus+eng",
                            max_pages=1,
                            tessdata_path=tessdata_path,
                        )
        finally:
            if temp_file.exists():
                temp_file.unlink()

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["text_length"], len("OCR text"))
        self.assertEqual(captured_kwargs.get("language"), "rus+eng")
        self.assertEqual(captured_kwargs.get("tessdata"), tessdata_path)


if __name__ == "__main__":
    unittest.main()

