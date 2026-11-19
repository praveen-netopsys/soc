"""Word (.docx) extraction engine using python-docx.

Extracts headings, paragraphs, and tables in reading order.
"""
from __future__ import annotations

import logging
from pathlib import Path
from docx import Document

from .models import ContentBlock

logger = logging.getLogger("explain_studio.extractor.docx")


def extract_from_docx_file(
    file_path: Path,
    *,
    source_id: str,
    original_filename: str,
) -> list[ContentBlock]:
    """Parse a DOCX document into ordered ContentBlocks."""
    if not file_path.exists() or file_path.stat().st_size == 0:
        return []

    try:
        doc = Document(str(file_path))
    except Exception as exc:
        raise ValueError(f"Failed to parse Word document '{original_filename}': {exc}") from exc

    blocks: list[ContentBlock] = []

    block_counter = 1
    for p in doc.paragraphs:
        text = p.text.strip()
        if not text:
            continue

        style_name = p.style.name.lower() if p.style and p.style.name else ""
        is_heading = "heading" in style_name or "title" in style_name

        blocks.append(
            ContentBlock(
                id=f"{source_id}_b{block_counter}",
                source_id=source_id,
                original_filename=original_filename,
                source_type="docx",
                slide_number=None,
                block_type="heading" if is_heading else "paragraph",
                text=text,
                extraction_metadata={"style": style_name, "is_heading": is_heading},
            )
        )
        block_counter += 1

    # Extract tables
    for tbl_idx, table in enumerate(doc.tables, start=1):
        rows_text = []
        for row in table.rows:
            cell_texts = [c.text.strip() for c in row.cells]
            rows_text.append(" | ".join(cell_texts))
        if rows_text:
            table_summary = "\n".join(rows_text)
            blocks.append(
                ContentBlock(
                    id=f"{source_id}_tbl_{tbl_idx}",
                    source_id=source_id,
                    original_filename=original_filename,
                    source_type="docx",
                    slide_number=None,
                    block_type="table",
                    text=table_summary,
                    extraction_metadata={"table_index": tbl_idx, "row_count": len(table.rows)},
                )
            )

    return blocks
