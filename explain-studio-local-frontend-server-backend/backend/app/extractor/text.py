"""Text and Markdown extraction engine.

Preserves paragraph and heading order, handles UTF-8 / UTF-8 BOM,
and degrades gracefully on malformed or empty text files.
"""
from __future__ import annotations

import re
import uuid
from pathlib import Path

from .models import ContentBlock


def decode_bytes(data: bytes) -> str:
    """Robust decoder handling UTF-8 with BOM, standard UTF-8, and fallback encodings."""
    if not data:
        return ""
    # Try utf-8-sig first to strip BOM if present
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    # Fallback to cp1252 / latin-1 with replacement
    try:
        return data.decode("cp1252", errors="replace")
    except Exception:
        return data.decode("latin-1", errors="replace")


def extract_text_blocks(
    content: str,
    *,
    source_id: str,
    original_filename: str,
    is_markdown: bool = False,
) -> list[ContentBlock]:
    """Parse text/markdown into structured ContentBlocks preserving reading order."""
    if not content or not content.strip():
        return []

    lines = content.splitlines()
    blocks: list[ContentBlock] = []

    current_para: list[str] = []

    def flush_para():
        if current_para:
            text = "\n".join(current_para).strip()
            if text:
                blocks.append(
                    ContentBlock(
                        id=str(uuid.uuid4()),
                        source_id=source_id,
                        original_filename=original_filename,
                        source_type="markdown" if is_markdown else "text",
                        slide_number=None,
                        block_type="paragraph",
                        text=text,
                    )
                )
            current_para.clear()

    idx = 0
    in_code_block = False
    code_block_lines: list[str] = []

    while idx < len(lines):
        line = lines[idx]
        stripped = line.strip()

        # Handle Markdown code fences
        if is_markdown and stripped.startswith("```"):
            if not in_code_block:
                flush_para()
                in_code_block = True
                code_block_lines = []
            else:
                in_code_block = False
                blocks.append(
                    ContentBlock(
                        id=str(uuid.uuid4()),
                        source_id=source_id,
                        original_filename=original_filename,
                        source_type="markdown",
                        slide_number=None,
                        block_type="code",
                        text="\n".join(code_block_lines),
                    )
                )
                code_block_lines = []
            idx += 1
            continue

        if in_code_block:
            code_block_lines.append(line)
            idx += 1
            continue

        # Markdown headings (# Heading, ## Heading)
        if is_markdown:
            heading_match = re.match(r"^(#{1,6})\s+(.+)$", stripped)
            if heading_match:
                flush_para()
                level = len(heading_match.group(1))
                heading_text = heading_match.group(2).strip()
                blocks.append(
                    ContentBlock(
                        id=str(uuid.uuid4()),
                        source_id=source_id,
                        original_filename=original_filename,
                        source_type="markdown",
                        slide_number=None,
                        block_type="heading",
                        text=heading_text,
                        extraction_metadata={"heading_level": level},
                    )
                )
                idx += 1
                continue

            # Markdown setext headings (Heading\n=== or Heading\n---)
            if idx + 1 < len(lines) and stripped and not current_para:
                next_stripped = lines[idx + 1].strip()
                if re.match(r"^={3,}$", next_stripped):
                    blocks.append(
                        ContentBlock(
                            id=str(uuid.uuid4()),
                            source_id=source_id,
                            original_filename=original_filename,
                            source_type="markdown",
                            slide_number=None,
                            block_type="heading",
                            text=stripped,
                            extraction_metadata={"heading_level": 1},
                        )
                    )
                    idx += 2
                    continue
                elif re.match(r"^-{3,}$", next_stripped):
                    blocks.append(
                        ContentBlock(
                            id=str(uuid.uuid4()),
                            source_id=source_id,
                            original_filename=original_filename,
                            source_type="markdown",
                            slide_number=None,
                            block_type="heading",
                            text=stripped,
                            extraction_metadata={"heading_level": 2},
                        )
                    )
                    idx += 2
                    continue

        # Blank line delimits paragraphs
        if not stripped:
            flush_para()
        else:
            current_para.append(line)

        idx += 1

    flush_para()

    # Flush any unclosed code block
    if code_block_lines:
        blocks.append(
            ContentBlock(
                id=str(uuid.uuid4()),
                source_id=source_id,
                original_filename=original_filename,
                source_type="markdown" if is_markdown else "text",
                slide_number=None,
                block_type="code",
                text="\n".join(code_block_lines),
            )
        )

    return blocks


def extract_from_text_file(
    file_path: Path,
    *,
    source_id: str,
    original_filename: str,
) -> list[ContentBlock]:
    """Read a text or markdown file and return its extracted ContentBlocks."""
    if not file_path.exists():
        return []
    raw = file_path.read_bytes()
    text = decode_bytes(raw)
    ext = file_path.suffix.lstrip(".").lower()
    is_md = ext in ("md", "markdown")
    return extract_text_blocks(
        text,
        source_id=source_id,
        original_filename=original_filename,
        is_markdown=is_md,
    )
