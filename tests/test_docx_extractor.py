from __future__ import annotations

import unittest
from unittest.mock import patch

from app.extractors import docx_extractor


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


class DocxExtractorSizeCapTest(unittest.TestCase):
    def test_oversized_download_raises_value_error_before_writing_file(self) -> None:
        oversize_chunk = b"x" * (docx_extractor.MAX_DOCX_DOWNLOAD_BYTES + 1)
        response = _FakeResponse([oversize_chunk])

        with patch("app.extractors.docx_extractor.requests.get", return_value=response):
            with patch("app.extractors.docx_extractor.Path.write_bytes") as write_bytes:
                with self.assertRaises(ValueError) as ctx:
                    docx_extractor.extract_text_from_docx(
                        "https://example.test/big.docx",
                    )

        self.assertIn("DOCX download exceeded safety limit", str(ctx.exception))
        write_bytes.assert_not_called()
        self.assertTrue(response.closed)


if __name__ == "__main__":
    unittest.main()
