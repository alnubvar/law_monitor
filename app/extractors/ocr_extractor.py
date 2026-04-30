from __future__ import annotations

from pathlib import Path


def extract_text_with_ocr(file_path: str | Path, engine: str = "stub") -> str:
    path = Path(file_path)
    return (
        f"OCR placeholder: document '{path.name}' requires OCR extraction. "
        f"Configured engine='{engine}'. Real OCR integration will be added later."
    )

