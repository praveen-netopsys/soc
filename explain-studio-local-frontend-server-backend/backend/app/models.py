"""Pydantic response models for the Explain Studio API."""
from __future__ import annotations

from typing import Any
from pydantic import BaseModel

from .ai.models import (
    AIAnalysisResult,
    CompleteDirectorResult,
    DurationEstimate,
    EducationalPlan,
    NarrationScript,
    QualityReport,
    Storyboard,
)
from .extractor.models import ContentBlock, SourceMetadata


class FileInfo(BaseModel):
    file_id: str
    original_name: str
    kind: str        # 'text' | 'presentation' | 'image'
    size_bytes: int


class JobCreatedResponse(BaseModel):
    job_id: str
    status: str
    created_at: str
    file_count: int
    has_text: bool
    target_duration_seconds: float = 120.0
    aspect_ratio: str = "9:16"


class JobStatusResponse(BaseModel):
    job_id: str
    status: str      # 'pending' | 'extracting' | 'ready_for_ai' | 'processing' | 'completed' | 'failed'
    stage: str
    created_at: str
    updated_at: str
    has_text: bool
    files: list[FileInfo]
    error: str | None
    output_path: str | None
    target_duration_seconds: float = 120.0
    aspect_ratio: str = "9:16"
    progress_pct: float = 0.0
    eta_seconds: float | None = None
    progress_detail: str | None = None


class JobSourcesResponse(BaseModel):
    job_id: str
    status: str
    extracted: bool
    stage: str | None = None
    extracted_at: str | None = None
    sources: list[SourceMetadata] = []
    blocks: list[ContentBlock] = []
    total_blocks: int = 0
    total_assets: int = 0
    error: str | None = None


class JobAnalysisTriggerResponse(BaseModel):
    job_id: str
    status: str
    stage: str


class JobAnalysisResponse(BaseModel):
    job_id: str
    status: str
    stage: str | None = None
    analyzed: bool
    analysis: AIAnalysisResult | None = None
    plan: EducationalPlan | None = None
    duration: DurationEstimate | None = None
    quality: QualityReport | None = None
    error: str | None = None


class JobScriptResponse(BaseModel):
    job_id: str
    status: str
    stage: str | None = None
    has_script: bool
    script: NarrationScript | None = None
    error: str | None = None


class JobStoryboardResponse(BaseModel):
    job_id: str
    status: str
    stage: str | None = None
    has_storyboard: bool
    storyboard: Storyboard | None = None
    duration: DurationEstimate | None = None
    quality: QualityReport | None = None
    error: str | None = None


class JobRenderResponse(BaseModel):
    job_id: str
    status: str
    rendering_authorized: bool
    overall_score: float
    message: str

