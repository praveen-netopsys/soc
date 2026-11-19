"""Extraction pipeline orchestrator.

Coordinates extraction across text, markdown, PPTX, and image sources,
assembles the Universal Content Representation, and writes manifest.json.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import db, storage
from .image import extract_from_image_file
from .models import ContentBlock, ExtractionManifest, SourceMetadata
from .pptx import extract_from_pptx_file
from .text import extract_from_text_file, extract_text_blocks
from .pdf import extract_from_pdf_file
from .docx import extract_from_docx_file
from .pdf import extract_from_pdf_file
from .docx import extract_from_docx_file
from .pdf import extract_from_pdf_file
from .docx import extract_from_docx_file


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def process_job_extraction(job_id: str) -> ExtractionManifest:
    """Run extraction for all sources in a job and produce storage/extracted/{job_id}/manifest.json."""
    job = db.get_job(job_id)
    if job is None:
        raise ValueError(f"Job '{job_id}' not found in database.")

    extracted_root = storage.ensure_extracted_dir(job_id)
    assets_dir = extracted_root / "assets"
    raw_files_dir = storage.job_dir(job_id)

    all_blocks: list[ContentBlock] = []
    sources_meta: list[SourceMetadata] = []
    total_assets = 0

    # 1. Process pasted text if present
    pasted_text = job.get("pasted_text")
    if pasted_text and pasted_text.strip():
        text_bytes = pasted_text.encode("utf-8")
        text_sha = hashlib.sha256(text_bytes).hexdigest()
        text_blocks = extract_text_blocks(
            pasted_text,
            source_id="pasted-text",
            original_filename="pasted_text.txt",
            is_markdown=False,
        )
        all_blocks.extend(text_blocks)
        sources_meta.append(
            SourceMetadata(
                source_id="pasted-text",
                original_filename="pasted_text.txt",
                source_type="text",
                file_size_bytes=len(text_bytes),
                sha256=text_sha,
                num_blocks=len(text_blocks),
                extracted_asset_count=0,
                metadata={},
            )
        )

    # 2. Process attached files
    for file_record in job.get("files", []):
        file_id = file_record["id"]
        orig_name = file_record["original_name"]
        stored_name = file_record["stored_name"]
        kind = file_record["kind"]
        file_path = raw_files_dir / stored_name

        if not file_path.exists():
            raise FileNotFoundError(f"Stored file for '{orig_name}' missing from disk at {file_path}")

        raw_bytes = file_path.read_bytes()
        file_sha = hashlib.sha256(raw_bytes).hexdigest()
        file_size = len(raw_bytes)
        ext = file_path.suffix.lstrip(".").lower()

        blocks: list[ContentBlock] = []
        assets_for_source = 0

        if kind == "presentation" or ext == "pptx":
            blocks = extract_from_pptx_file(
                file_path,
                source_id=file_id,
                original_filename=orig_name,
                assets_dir=assets_dir,
            )
            assets_for_source = sum(1 for b in blocks if b.block_type == "image_asset")
            source_type = "presentation"
        elif kind == "image" or ext in {"png", "jpg", "jpeg", "webp"}:
            blocks = extract_from_image_file(
                file_path,
                source_id=file_id,
                original_filename=orig_name,
                assets_dir=assets_dir,
            )
            assets_for_source = len(blocks)
            source_type = "image"
        elif ext == "pdf":
            blocks = extract_from_pdf_file(
                file_path,
                source_id=file_id,
                original_filename=orig_name,
            )
            source_type = "pdf"
        elif ext in {"docx", "doc"}:
            blocks = extract_from_docx_file(
                file_path,
                source_id=file_id,
                original_filename=orig_name,
            )
            source_type = "docx"
        else:
            # Text / Markdown
            blocks = extract_from_text_file(
                file_path,
                source_id=file_id,
                original_filename=orig_name,
            )
            source_type = "markdown" if ext in {"md", "markdown"} else "text"

        all_blocks.extend(blocks)
        total_assets += assets_for_source

        sources_meta.append(
            SourceMetadata(
                source_id=file_id,
                original_filename=orig_name,
                source_type=source_type,
                file_size_bytes=file_size,
                sha256=file_sha,
                num_blocks=len(blocks),
                extracted_asset_count=assets_for_source,
                metadata={},
            )
        )

    # Assign authoritative, clean, sequential canonical block IDs across manifest
    for idx, b in enumerate(all_blocks):
        b.extraction_metadata["original_id"] = b.id
        b.id = f"blk_{idx + 1}"

    manifest = ExtractionManifest(
        job_id=job_id,
        extracted_at=_now(),
        sources=sources_meta,
        blocks=all_blocks,
        total_blocks=len(all_blocks),
        total_assets=total_assets,
    )

    # Save manifest.json
    manifest_path = extracted_root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest.model_dump(), indent=2), encoding="utf-8")

    return manifest
