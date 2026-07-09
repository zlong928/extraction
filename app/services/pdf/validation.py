from __future__ import annotations

from pathlib import Path
from typing import Any


class PdfValidationError(ValueError):
    pass


def validate_pdf_file(path: str | Path) -> dict[str, Any]:
    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise PdfValidationError(f"PDF file not found: {path}")

    try:
        import fitz
    except ImportError:
        return {"page_count": None, "warnings": []}

    try:
        doc = fitz.open(str(resolved))
    except Exception as exc:
        raise PdfValidationError(f"Cannot open PDF: {exc}") from exc

    page_count = doc.page_count
    if page_count == 0:
        doc.close()
        raise PdfValidationError(f"PDF has 0 pages: {resolved.name}")

    total_text = 0
    total_images = 0
    for page_num in range(page_count):
        page = doc[page_num]
        total_text += len(page.get_text().strip())
        total_images += len(page.get_images())

    doc.close()

    summary = f"pg={page_count} chars={total_text} images={total_images}"
    if total_text == 0 and total_images == 0:
        raise PdfValidationError(
            f"PDF '{resolved.name}' appears blank: {summary}"
        )

    return {
        "page_count": page_count,
        "text_length": total_text,
        "image_count": total_images,
    }
