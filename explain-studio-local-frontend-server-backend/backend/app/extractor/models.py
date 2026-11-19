"""Universal Content Representation models for extracted source material."""
from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field


class ContentBlock(BaseModel):
    """A granular, structured unit of extracted content with provenance."""
    id: str
    source_id: str
    original_filename: str
    source_type: str  # 'text' | 'markdown' | 'presentation' | 'image'
    slide_number: int | None = None
    block_type: str  # 'heading' | 'paragraph' | 'table' | 'speaker_notes' | 'image_asset' | 'code'
    text: str | None = None
    asset_reference: str | None = None
    extraction_metadata: dict[str, Any] = Field(default_factory=dict)


class SourceMetadata(BaseModel):
    """Metadata and statistics for a specific source input."""
    source_id: str
    original_filename: str
    source_type: str
    file_size_bytes: int
    sha256: str
    num_blocks: int
    extracted_asset_count: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExtractionManifest(BaseModel):
    """Overall manifest saved per job under storage/extracted/{job_id}/manifest.json."""
    job_id: str
    extracted_at: str
    sources: list[SourceMetadata]
    blocks: list[ContentBlock]
    total_blocks: int
    total_assets: int
