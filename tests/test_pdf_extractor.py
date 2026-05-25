from __future__ import annotations

import unittest
from unittest.mock import patch

from app.extractors import pdf_extractor


class _FakeResponse:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks
        self.closed = False

    def raise_for_status(self) -> None:
        return None

    def iter_content(self, chunk_size: int = 65536):
        del chunk_size
        for chunk in self._chunks:
            yield chunk

    def close(self) -> None:
        self.closed = True


class PdfExtractorSizeCapTest(unittest.TestCase):
    def test_empty_pdf_response_returns_warning_result_without_opening_file(self) -> None:
        response = _FakeResponse([])

        with patch("app.extractors.pdf_extractor.requests.get", return_value=response):
            with patch("app.extractors.pdf_extractor.Path.write_bytes") as write_bytes:
                with patch("app.extractors.pdf_extractor.fitz.open") as fitz_open:
                    result = pdf_extractor.extract_text_from_pdf(
                        "https://example.test/empty.pdf",
                    )

        self.assertEqual(result.raw_text, "")
        self.assertEqual(result.document_type, "pdf")
        self.assertEqual(result.error, pdf_extractor.PDF_EMPTY_RESPONSE_ERROR)
        self.assertEqual(result.extracted_text_length, 0)
        write_bytes.assert_not_called()
        fitz_open.assert_not_called()
        self.assertTrue(response.closed)

    def test_non_pdf_response_returns_warning_result_without_opening_file(self) -> None:
        response = _FakeResponse([b"<html>blocked</html>"])

        with patch("app.extractors.pdf_extractor.requests.get", return_value=response):
            with patch("app.extractors.pdf_extractor.Path.write_bytes") as write_bytes:
                with patch("app.extractors.pdf_extractor.fitz.open") as fitz_open:
                    result = pdf_extractor.extract_text_from_pdf(
                        "https://example.test/not-a-pdf.pdf",
                    )

        self.assertEqual(result.raw_text, "")
        self.assertEqual(result.document_type, "pdf")
        self.assertEqual(result.error, pdf_extractor.PDF_INVALID_HEADER_ERROR)
        write_bytes.assert_not_called()
        fitz_open.assert_not_called()
        self.assertTrue(response.closed)

    def test_oversized_download_raises_value_error_before_writing_file(self) -> None:
        # One chunk just past the cap is enough to trip the guard.
        oversize_chunk = b"x" * (pdf_extractor.MAX_PDF_DOWNLOAD_BYTES + 1)
        response = _FakeResponse([oversize_chunk])

        with patch("app.extractors.pdf_extractor.requests.get", return_value=response):
            with patch("app.extractors.pdf_extractor.Path.write_bytes") as write_bytes:
                with self.assertRaises(ValueError) as ctx:
                    pdf_extractor.extract_text_from_pdf(
                        "https://example.test/big.pdf",
                    )

        self.assertIn("PDF download exceeded safety limit", str(ctx.exception))
        write_bytes.assert_not_called()
        self.assertTrue(response.closed)

    def test_streaming_aborts_at_cap_even_across_many_chunks(self) -> None:
        # Many small chunks that together exceed the cap by a single byte.
        chunk = b"y" * (1024 * 1024)
        chunks = [chunk] * (pdf_extractor.MAX_PDF_DOWNLOAD_BYTES // len(chunk))
        chunks.append(b"z")  # one extra byte past the cap
        response = _FakeResponse(chunks)

        with patch("app.extractors.pdf_extractor.requests.get", return_value=response):
            with patch("app.extractors.pdf_extractor.Path.write_bytes"):
                with self.assertRaises(ValueError):
                    pdf_extractor.extract_text_from_pdf(
                        "https://example.test/almost.pdf",
                    )

        self.assertTrue(response.closed)


if __name__ == "__main__":
    unittest.main()
