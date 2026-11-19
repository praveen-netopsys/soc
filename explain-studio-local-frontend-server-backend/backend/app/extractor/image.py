"""Image extraction and metadata inspection engine.

Uses Pillow for dimension and format validation, computes SHA-256 checksums,
and copies original assets to the job's extracted asset directory without fake AI descriptions.
"""
from __future__ import annotations

import hashlib
import shutil
import uuid
from pathlib import Path

from PIL import Image

from .models import ContentBlock


def extract_from_image_file(
    file_path: Path,
    *,
    source_id: str,
    original_filename: str,
    assets_dir: Path,
) -> list[ContentBlock]:
    """Inspect and register an uploaded image file as a ContentBlock."""
    if not file_path.exists():
        return []

    data = file_path.read_bytes()
    sha256 = hashlib.sha256(data).hexdigest()
    file_size = len(data)

    try:
        with Image.open(file_path) as img:
            width, height = img.size
            fmt = img.format or file_path.suffix.lstrip(".").upper()
    except Exception as exc:
        raise ValueError(f"Corrupted or invalid image file '{original_filename}': {exc}") from exc

    # Preserve asset in job extraction directory
    ext = file_path.suffix.lstrip(".") or "img"
    asset_filename = f"{source_id}_{Path(original_filename).stem}.{ext}"
    asset_dest = assets_dir / asset_filename
    shutil.copy2(file_path, asset_dest)

    block = ContentBlock(
        id=str(uuid.uuid4()),
        source_id=source_id,
        original_filename=original_filename,
        source_type="image",
        slide_number=None,
        block_type="image_asset",
        text=None,
        asset_reference=f"assets/{asset_filename}",
        extraction_metadata={
            "dimensions": [width, height],
            "format": fmt,
            "file_size": file_size,
            "sha256": sha256,
        },
    )

    return [block]
