"""Job creation, status, and source extraction endpoints.

POST /v1/jobs           — chunked multipart upload, persists job record
GET  /v1/jobs/{job_id}  — returns job status and uploaded file metadata
GET  /v1/jobs/{job_id}/sources — returns extracted universal content blocks & metadata
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .. import config
from ..ai.analyzer import process_job_analysis
from ..ai.director.pipeline import run_complete_director_pipeline
from ..ai.models import (
    AIAnalysisResult,
    CompleteDirectorResult,
    DurationEstimate,
    EducationalPlan,
    NarrationScript,
    QualityReport,
    Storyboard,
)
from ..db import _connect, add_job_file, create_job, get_job, update_job_status
from ..extractor.models import ExtractionManifest
from ..models import (
    FileInfo,
    JobAnalysisResponse,
    JobAnalysisTriggerResponse,
    JobCreatedResponse,
    JobRenderResponse,
    JobScriptResponse,
    JobSourcesResponse,
    JobStatusResponse,
    JobStoryboardResponse,
)
from ..pipeline.orchestrator import run_video_generation_pipeline
from ..storage import (
    analysis_dir,
    delete_job_files,
    extracted_dir,
    final_subtitles_path,
    final_video_path,
)

logger = logging.getLogger("explain_studio.jobs")
from ..validation import (
    stream_and_validate_file,
    validate_at_least_one_input,
    validate_extension,
    validate_file_count,
)

router = APIRouter(prefix="/v1", tags=["jobs"])


@router.post("/jobs", response_model=JobCreatedResponse, status_code=201)
def create_job_endpoint(
    text: Annotated[str | None, Form()] = None,
    audience: Annotated[str, Form()] = "high_school",
    animation_style: Annotated[str, Form()] = "auto",
    subtitles: Annotated[str, Form()] = "false",
    target_duration_seconds: Annotated[float, Form()] = 120.0,
    aspect_ratio: Annotated[str, Form()] = "9:16",
    files: Annotated[list[UploadFile], File()] = [],  # noqa: B006
) -> JobCreatedResponse:
    """
    Create a generation job from pasted text and/or uploaded files.

    - Streams uploads in 64KB chunks to temporary storage, enforcing size limits.
    - Deeply validates file content (PPTX ZIP container check, Pillow image integrity).
    - Atomically moves files into job storage and registers SQLite job record.
    - Cleans up temporary and stored files on any failure.
    """
    subtitles_bool = subtitles.lower() in {"true", "1", "yes"}
    actual_files = [f for f in files if f.filename]
    clean_text = text.strip() if text and text.strip() else None

    # Initial checks
    validate_at_least_one_input(clean_text, actual_files)
    validate_file_count(actual_files)
    for upload in actual_files:
        validate_extension(upload)

    job_id = str(uuid.uuid4())
    initial_status = "uploading" if actual_files else "pending"
    initial_stage = "Uploading files..." if actual_files else "Queued - awaiting processing"

    # Create job in database
    create_job(
        job_id=job_id,
        has_text=bool(clean_text),
        pasted_text=clean_text,
        audience=audience,
        animation_style=animation_style,
        subtitles=subtitles_bool,
        target_duration_seconds=target_duration_seconds,
        aspect_ratio=aspect_ratio,
        status=initial_status,
        stage=initial_stage,
    )

    # Stream files to disk with chunked reading and validation
    total_bytes = 0
    saved_records: list[tuple[str, str, str, str, int]] = []

    try:
        for upload in actual_files:
            file_id = str(uuid.uuid4())
            safe_name, stored_name, kind, size_bytes = stream_and_validate_file(
                upload,
                job_id=job_id,
                file_id=file_id,
                current_total_bytes=total_bytes,
            )
            total_bytes += size_bytes
            saved_records.append((file_id, safe_name, stored_name, kind, size_bytes))

        # Register uploaded files in SQLite
        for file_id, safe_name, stored_name, kind, size_bytes in saved_records:
            add_job_file(
                file_id=file_id,
                job_id=job_id,
                original_name=safe_name,
                stored_name=stored_name,
                kind=kind,
                size_bytes=size_bytes,
            )

        if actual_files:
            update_job_status(job_id, status="pending", stage="Queued - awaiting processing")

    except HTTPException:
        delete_job_files(job_id)
        with _connect() as conn:
            conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        raise
    except Exception as exc:
        delete_job_files(job_id)
        with _connect() as conn:
            conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        raise HTTPException(status_code=500, detail="Failed to stream and store files.") from exc

    job = get_job(job_id)
    assert job is not None
    return JobCreatedResponse(
        job_id=job_id,
        status=job["status"],
        created_at=job["created_at"],
        file_count=len(saved_records),
        has_text=bool(clean_text),
        target_duration_seconds=float(job.get("target_duration_seconds") or target_duration_seconds),
        aspect_ratio=str(job.get("aspect_ratio") or aspect_ratio),
    )


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
def get_job_status(job_id: str) -> JobStatusResponse:
    """Return the current status of a job, including stage and uploaded file metadata."""
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")

    return JobStatusResponse(
        job_id=job["id"],
        status=job["status"],
        stage=job["stage"],
        created_at=job["created_at"],
        updated_at=job["updated_at"],
        has_text=bool(job["has_text"]),
        files=[
            FileInfo(
                file_id=f["id"],
                original_name=f["original_name"],
                kind=f["kind"],
                size_bytes=f["size_bytes"],
            )
            for f in job["files"]
        ],
        error=job["error"],
        output_path=job["output_path"],
        target_duration_seconds=float(job.get("target_duration_seconds") or 120.0),
        aspect_ratio=str(job.get("aspect_ratio") or "9:16"),
        progress_pct=float(job.get("progress_pct") or 0.0),
        eta_seconds=float(job["eta_seconds"]) if job.get("eta_seconds") is not None else None,
        progress_detail=job.get("progress_detail"),
    )


@router.get("/jobs/{job_id}/sources", response_model=JobSourcesResponse)
def get_job_sources(job_id: str) -> JobSourcesResponse:
    """
    Return extraction metadata and universal content blocks for a job.
    Does not expose raw server filesystem paths.
    """
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")

    manifest_file = extracted_dir(job_id) / "manifest.json"

    if manifest_file.exists():
        try:
            data = json.loads(manifest_file.read_text(encoding="utf-8"))
            manifest = ExtractionManifest.model_validate(data)
            return JobSourcesResponse(
                job_id=job["id"],
                status=job["status"],
                extracted=True,
                stage=job["stage"],
                extracted_at=manifest.extracted_at,
                sources=manifest.sources,
                blocks=manifest.blocks,
                total_blocks=manifest.total_blocks,
                total_assets=manifest.total_assets,
            )
        except Exception as exc:
            return JobSourcesResponse(
                job_id=job["id"],
                status=job["status"],
                extracted=False,
                stage=job["stage"],
                error=f"Failed to parse extraction manifest: {exc}",
            )

    # Manifest not created yet
    return JobSourcesResponse(
        job_id=job["id"],
        status=job["status"],
        extracted=False,
        stage=job["stage"],
        error=job.get("error"),
    )


# ── AI Content Analysis Endpoints ─────────────────────────────────────────────

def _run_analysis_worker(job_id: str) -> None:
    """Synchronous worker function to execute complete AI Educational Director pipeline in thread pool."""
    try:
        run_complete_director_pipeline(job_id)
        logger.info("Complete AI Educational Director finished successfully for job %s", job_id)
    except Exception as exc:
        logger.exception("AI Educational Director failed for job %s: %s", job_id, exc)
        update_job_status(
            job_id,
            status="ready_for_ai",  # Reset to ready_for_ai so user can retry
            stage="AI content analysis failed",
            error=str(exc),
        )


@router.post("/jobs/{job_id}/analyze", response_model=JobAnalysisTriggerResponse)
def trigger_job_analysis(job_id: str, background_tasks: BackgroundTasks) -> JobAnalysisTriggerResponse:
    """
    Explicitly trigger the AI Educational Director suite for an extracted job.
    - Validates that content extraction has succeeded.
    - Sequentially executes Content Intelligence, Educational Planning, Script Generation,
      Storyboard Generation, Adaptive Duration, and Quality Assurance under single GPU safety lock.
    """
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")

    current_status = job["status"]

    if current_status in {"pending", "extracting"}:
        raise HTTPException(
            status_code=400,
            detail=f"Job '{job_id}' is currently extracting content ({current_status}). Please wait for extraction to finish.",
        )

    if current_status == "analyzing":
        return JobAnalysisTriggerResponse(
            job_id=job_id,
            status="analyzing",
            stage=job["stage"],
        )

    # Transition status to analyzing
    update_job_status(
        job_id,
        status="analyzing",
        stage="Queued for AI Educational Director",
        error=None,
    )

    # Launch in background thread
    background_tasks.add_task(_run_analysis_worker, job_id)

    return JobAnalysisTriggerResponse(
        job_id=job_id,
        status="analyzing",
        stage="Queued for AI Educational Director",
    )


@router.get("/jobs/{job_id}/analysis", response_model=JobAnalysisResponse)
def get_job_analysis(job_id: str) -> JobAnalysisResponse:
    """
    Retrieve structured AI content analysis and educational plan for a job.
    """
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")

    out_dir = analysis_dir(job_id)
    analysis_file = out_dir / "analysis.json"
    plan_file = out_dir / "plan.json"
    duration_file = out_dir / "duration.json"
    quality_file = out_dir / "quality.json"

    if analysis_file.exists():
        try:
            data = json.loads(analysis_file.read_text(encoding="utf-8"))
            result = AIAnalysisResult.model_validate(data)

            plan = None
            if plan_file.exists():
                plan = EducationalPlan.model_validate(json.loads(plan_file.read_text(encoding="utf-8")))

            duration = None
            if duration_file.exists():
                duration = DurationEstimate.model_validate(json.loads(duration_file.read_text(encoding="utf-8")))

            quality = None
            if quality_file.exists():
                quality = QualityReport.model_validate(json.loads(quality_file.read_text(encoding="utf-8")))

            return JobAnalysisResponse(
                job_id=job["id"],
                status=job["status"],
                stage=job["stage"],
                analyzed=True,
                analysis=result,
                plan=plan,
                duration=duration,
                quality=quality,
                error=None,
            )
        except Exception as exc:
            return JobAnalysisResponse(
                job_id=job["id"],
                status=job["status"],
                stage=job["stage"],
                analyzed=False,
                analysis=None,
                error=f"Failed to load analysis JSON: {exc}",
            )

    return JobAnalysisResponse(
        job_id=job["id"],
        status=job["status"],
        stage=job["stage"],
        analyzed=False,
        analysis=None,
        error=job.get("error"),
    )


@router.get("/jobs/{job_id}/script", response_model=JobScriptResponse)
def get_job_script(job_id: str) -> JobScriptResponse:
    """
    Retrieve generated conversational teaching script and scene segments.
    """
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")

    script_file = analysis_dir(job_id) / "script.json"

    if script_file.exists():
        try:
            data = json.loads(script_file.read_text(encoding="utf-8"))
            script = NarrationScript.model_validate(data)
            return JobScriptResponse(
                job_id=job["id"],
                status=job["status"],
                stage=job["stage"],
                has_script=True,
                script=script,
                error=None,
            )
        except Exception as exc:
            return JobScriptResponse(
                job_id=job["id"],
                status=job["status"],
                stage=job["stage"],
                has_script=False,
                script=None,
                error=f"Failed to load script JSON: {exc}",
            )

    return JobScriptResponse(
        job_id=job["id"],
        status=job["status"],
        stage=job["stage"],
        has_script=False,
        script=None,
        error=job.get("error"),
    )


@router.get("/jobs/{job_id}/storyboard", response_model=JobStoryboardResponse)
def get_job_storyboard(job_id: str) -> JobStoryboardResponse:
    """
    Retrieve executable storyboard specification, animation instructions, and duration.
    """
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")

    out_dir = analysis_dir(job_id)
    storyboard_file = out_dir / "storyboard.json"
    duration_file = out_dir / "duration.json"
    quality_file = out_dir / "quality.json"

    if storyboard_file.exists():
        try:
            sb_data = json.loads(storyboard_file.read_text(encoding="utf-8"))
            storyboard = Storyboard.model_validate(sb_data)

            duration = None
            if duration_file.exists():
                duration = DurationEstimate.model_validate(json.loads(duration_file.read_text(encoding="utf-8")))

            quality = None
            if quality_file.exists():
                quality = QualityReport.model_validate(json.loads(quality_file.read_text(encoding="utf-8")))

            return JobStoryboardResponse(
                job_id=job["id"],
                status=job["status"],
                stage=job["stage"],
                has_storyboard=True,
                storyboard=storyboard,
                duration=duration,
                quality=quality,
                error=None,
            )
        except Exception as exc:
            return JobStoryboardResponse(
                job_id=job["id"],
                status=job["status"],
                stage=job["stage"],
                has_storyboard=False,
                storyboard=None,
                error=f"Failed to load storyboard JSON: {exc}",
            )

    return JobStoryboardResponse(
        job_id=job["id"],
        status=job["status"],
        stage=job["stage"],
        has_storyboard=False,
        storyboard=None,
        error=job.get("error"),
    )


@router.post("/jobs/{job_id}/render", response_model=JobRenderResponse)
def trigger_render_job(job_id: str, background_tasks: BackgroundTasks) -> JobRenderResponse:
    """
    Guarded progression to video rendering.
    Verifies that the job has completed AI director analysis and that mandatory QA checks passed.
    Blocks rendering with HTTP 422 and actionable reasons if QA validation failed.
    Dispatches the end-to-end video production pipeline in the background.
    """
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")

    out_dir = analysis_dir(job_id)
    quality_file = out_dir / "quality.json"

    if not quality_file.exists():
        raise HTTPException(
            status_code=400,
            detail=f"Job '{job_id}' has not completed educational director analysis yet. Run analysis first.",
        )

    try:
        quality_data = json.loads(quality_file.read_text(encoding="utf-8"))
        quality = QualityReport.model_validate(quality_data)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to read quality assurance report: {exc}",
        )

    if not quality.passed:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Cannot proceed to video rendering: mandatory QA validation failed.",
                "actionable_reasons": quality.actionable_reasons or quality.warnings or ["Quality threshold not satisfied."],
                "score": quality.overall_score,
                "invalid_source_references": quality.invalid_source_references,
                "mismatched_source_references": quality.mismatched_source_references,
            },
        )

    # Update job state and schedule background production pipeline
    update_job_status(job_id, status="processing", stage="Queued for video rendering...")
    background_tasks.add_task(run_video_generation_pipeline, job_id)

    return JobRenderResponse(
        job_id=job["id"],
        status="processing",
        rendering_authorized=True,
        overall_score=quality.overall_score,
        message="Quality assurance passed. Video rendering pipeline dispatched.",
    )


@router.get("/output/{job_id}")
def get_output_video(job_id: str) -> FileResponse:
    """
    Stream or download the completed educational video MP4.
    """
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")

    video_file = final_video_path(job_id)
    if not video_file.exists() or video_file.stat().st_size == 0:
        raise HTTPException(
            status_code=404,
            detail=f"Video for job '{job_id}' has not been rendered yet. Status: {job['status']}.",
        )

    return FileResponse(
        str(video_file.resolve()),
        media_type="video/mp4",
        filename=f"explain-studio-{job_id[:8]}.mp4",
    )


@router.get("/output/{job_id}/lessons/{lesson_id}")
def get_output_lesson_video(job_id: str, lesson_id: str) -> FileResponse:
    """
    Stream or download an individual lesson training video MP4.
    """
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")

    from ..storage import renders_dir
    r_dir = renders_dir(job_id)

    # Search for lesson video
    candidates = [
        r_dir / f"lesson_{lesson_id}.mp4",
        r_dir / f"lesson_mod_{lesson_id}.mp4",
    ]
    matched = None
    for c in candidates:
        if c.exists() and c.stat().st_size > 0:
            matched = c
            break

    if not matched:
        # Glob match
        for f in r_dir.glob(f"*{lesson_id}*.mp4"):
            if "raw_" not in f.name and "scene_" not in f.name and f.stat().st_size > 0:
                matched = f
                break

    if not matched or not matched.exists() or matched.stat().st_size == 0:
        raise HTTPException(
            status_code=404,
            detail=f"Lesson video '{lesson_id}' for job '{job_id}' not found.",
        )

    return FileResponse(
        str(matched.resolve()),
        media_type="video/mp4",
        filename=f"lesson-{lesson_id}-{job_id[:8]}.mp4",
    )


@router.get("/output/{job_id}/subtitles")
def get_output_subtitles(job_id: str, fmt: str = "vtt") -> FileResponse:
    """
    Download synchronized WebVTT or SRT subtitles for an educational video.
    """
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")

    clean_fmt = "vtt" if fmt.lower() == "vtt" else "srt"
    sub_file = final_subtitles_path(job_id, fmt=clean_fmt)
    if not sub_file.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Subtitles ({clean_fmt}) for job '{job_id}' not found.",
        )

    media_type = "text/vtt" if clean_fmt == "vtt" else "application/x-subrip"
    return FileResponse(
        str(sub_file.resolve()),
        media_type=media_type,
        filename=f"explain-studio-{job_id[:8]}.{clean_fmt}",
    )


