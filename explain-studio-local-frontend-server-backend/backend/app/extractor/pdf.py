"""PDF extraction engine using pypdf.

Extracts text blocks per page, preserving page numbers and reading order.
"""
from __future__ import annotations

import logging
from pathlib import Path
from pypdf import PdfReader

from .models import ContentBlock

logger = logging.getLogger("explain_studio.extractor.pdf")


def extract_from_pdf_file(
    file_path: Path,
    *,
    source_id: str,
    original_filename: str,
) -> list[ContentBlock]:
    """Parse a PDF document into ordered ContentBlocks by page and paragraph."""
    if not file_path.exists() or file_path.stat().st_size == 0:
        return []

    try:
        reader = PdfReader(str(file_path))
    except Exception as exc:
        raise ValueError(f"Failed to parse PDF document '{original_filename}': {exc}") from exc

    blocks: list[ContentBlock] = []

    for page_idx, page in enumerate(reader.pages, start=1):
        try:
            page_text = page.extract_text() or ""
        except Exception as exc:
            logger.warning("Error extracting text from page %d of %s: %s", page_idx, original_filename, exc)
            continue

        paragraphs = [p.strip() for p in page_text.split("\n\n") if p.strip()]
        if not paragraphs and page_text.strip():
            paragraphs = [page_text.strip()]

        for p_idx, para in enumerate(paragraphs, start=1):
            is_heading = len(para) < 80 and not para.endswith((".", ":", ";"))
            blocks.append(
                ContentBlock(
                    id=f"{source_id}_p{page_idx}_b{p_idx}",
                    source_id=source_id,
                    original_filename=original_filename,
                    source_type="pdf",
                    slide_number=page_idx,
                    block_type="heading" if is_heading else "paragraph",
                    text=para,
                    extraction_metadata={"page_number": page_idx, "is_heading": is_heading},
                )
            )

    return blocks
