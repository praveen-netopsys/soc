"""PowerPoint (.pptx) extraction engine.

Extracts slides in order, preserving titles, shape paragraphs, tables,
speaker notes, and embedded image assets associated with specific slides.
"""
from __future__ import annotations

import hashlib
import io
import uuid
from pathlib import Path
from typing import Any

from PIL import Image
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

from .models import ContentBlock


def extract_from_pptx_file(
    file_path: Path,
    *,
    source_id: str,
    original_filename: str,
    assets_dir: Path,
) -> list[ContentBlock]:
    """Parse a PowerPoint presentation into ordered ContentBlocks."""
    if not file_path.exists() or file_path.stat().st_size == 0:
        return []

    try:
        prs = Presentation(file_path)
    except Exception as exc:
        raise ValueError(f"Failed to parse PowerPoint presentation '{original_filename}': {exc}") from exc

    blocks: list[ContentBlock] = []
    image_counter = 0

    for slide_idx, slide in enumerate(prs.slides, start=1):
        slide_title: str | None = None

        # 1. Slide title
        if slide.shapes.title and slide.shapes.title.text.strip():
            slide_title = slide.shapes.title.text.strip()
            blocks.append(
                ContentBlock(
                    id=str(uuid.uuid4()),
                    source_id=source_id,
                    original_filename=original_filename,
                    source_type="presentation",
                    slide_number=slide_idx,
                    block_type="heading",
                    text=slide_title,
                    extraction_metadata={"heading_level": 1, "is_slide_title": True},
                )
            )

        # 2. Iterate slide shapes
        for shape in slide.shapes:
            # Skip title shape since it's already captured
            if shape == slide.shapes.title:
                continue

            # Tables
            if shape.has_table:
                table_matrix: list[list[str]] = []
                for row in shape.table.rows:
                    row_data = [cell.text.strip() for cell in row.cells]
                    table_matrix.append(row_data)

                # Format readable table text
                table_lines = []
                for row in table_matrix:
                    table_lines.append(" | ".join(row))
                table_text = "\n".join(table_lines)

                blocks.append(
                    ContentBlock(
                        id=str(uuid.uuid4()),
                        source_id=source_id,
                        original_filename=original_filename,
                        source_type="presentation",
                        slide_number=slide_idx,
                        block_type="table",
                        text=table_text,
                        extraction_metadata={
                            "num_rows": len(table_matrix),
                            "num_cols": len(table_matrix[0]) if table_matrix else 0,
                            "raw_table": table_matrix,
                            "slide_title": slide_title,
                        },
                    )
                )
                continue

            # Text frames (paragraphs)
            if shape.has_text_frame:
                for paragraph in shape.text_frame.paragraphs:
                    p_text = paragraph.text.strip()
                    if p_text:
                        blocks.append(
                            ContentBlock(
                                id=str(uuid.uuid4()),
                                source_id=source_id,
                                original_filename=original_filename,
                                source_type="presentation",
                                slide_number=slide_idx,
                                block_type="paragraph",
                                text=p_text,
                                extraction_metadata={"slide_title": slide_title},
                            )
                        )

            # Embedded pictures / images
            if shape.shape_type == MSO_SHAPE_TYPE.PICTURE or hasattr(shape, "image"):
                try:
                    img = shape.image
                    blob = img.blob
                    ext = (img.ext or "png").lower()
                    image_counter += 1
                    asset_filename = f"slide_{slide_idx}_img_{image_counter}.{ext}"
                    asset_dest = assets_dir / asset_filename
                    asset_dest.write_bytes(blob)

                    # Inspect with Pillow
                    width, height, img_fmt = None, None, ext.upper()
                    try:
                        with Image.open(io.BytesIO(blob)) as pil_img:
                            width, height = pil_img.size
                            img_fmt = pil_img.format or img_fmt
                    except Exception:
                        pass

                    sha256 = hashlib.sha256(blob).hexdigest()

                    blocks.append(
                        ContentBlock(
                            id=str(uuid.uuid4()),
                            source_id=source_id,
                            original_filename=original_filename,
                            source_type="presentation",
                            slide_number=slide_idx,
                            block_type="image_asset",
                            text=None,
                            asset_reference=f"assets/{asset_filename}",
                            extraction_metadata={
                                "dimensions": [width, height] if width and height else None,
                                "format": img_fmt,
                                "file_size": len(blob),
                                "sha256": sha256,
                                "slide_title": slide_title,
                            },
                        )
                    )
                except Exception:
                    # Ignore non-fatal image extraction errors within a slide
                    pass

        # 3. Speaker notes
        if slide.has_notes_slide and slide.notes_slide:
            try:
                notes_frame = slide.notes_slide.notes_text_frame
                if notes_frame and notes_frame.text.strip():
                    blocks.append(
                        ContentBlock(
                            id=str(uuid.uuid4()),
                            source_id=source_id,
                            original_filename=original_filename,
                            source_type="presentation",
                            slide_number=slide_idx,
                            block_type="speaker_notes",
                            text=notes_frame.text.strip(),
                            extraction_metadata={"slide_title": slide_title},
                        )
                    )
            except Exception:
                pass

    return blocks
