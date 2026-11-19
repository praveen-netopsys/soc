"""Multilingual educational video course endpoints.

Provides single-action generation, streaming, and downloads across 6 languages:
1. English (en)
2. Chinese (Simplified) (zh-CN)
3. Malay (ms)
4. Tamil (ta)
5. Bangla (bn)
6. Telugu (te)
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import zipfile
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from fastapi.responses import FileResponse, Response

from ..ai.multilingual.coordinator import run_multilingual_pipeline
from ..ai.multilingual.models import SUPPORTED_LANGUAGES, MultilingualCourse
from ..db import get_job, update_job_status
from ..rendering.multilingual_engine import synthesize_slide_audio
from ..storage import courses_dir

logger = logging.getLogger("explain_studio.routers.multilingual")

router = APIRouter(prefix="/v1", tags=["multilingual"])


def _run_pipeline_worker(job_id: str) -> None:
    """Synchronous worker wrapper that executes async pipeline in an event loop."""
    try:
        asyncio.run(run_multilingual_pipeline(job_id))
    except Exception as exc:
        logger.exception("Multilingual pipeline failed for job %s: %s", job_id, exc)
        update_job_status(
            job_id,
            status="failed",
            stage="Multilingual video generation failed",
            error=str(exc),
        )


@router.get("/courses/languages")
def get_supported_languages() -> dict[str, object]:
    """Return dictionary of supported languages and metadata."""
    return {
        "count": len(SUPPORTED_LANGUAGES),
        "languages": list(SUPPORTED_LANGUAGES.values()),
    }


@router.post("/jobs/{job_id}/generate_all_languages")
def generate_all_languages_endpoint(
    job_id: str,
    background_tasks: BackgroundTasks,
) -> dict[str, object]:
    """
    Single-action endpoint: triggers complete analysis, module partitioning,
    multilingual explanations, audio synthesis, and video rendering for all 6 languages.
    """
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")

    if job["status"] == "processing" and "Multilingual" in (job.get("stage") or ""):
        return {
            "job_id": job_id,
            "status": "processing",
            "stage": job["stage"],
            "message": "Generation already in progress.",
        }

    update_job_status(
        job_id,
        status="processing",
        stage="Initializing single-action multilingual course generation...",
        error=None,
    )

    background_tasks.add_task(_run_pipeline_worker, job_id)

    return {
        "job_id": job_id,
        "status": "processing",
        "stage": "Initializing single-action multilingual course generation...",
        "message": "Multilingual video generation dispatched for all 6 languages.",
        "languages": list(SUPPORTED_LANGUAGES.keys()),
    }


@router.get("/jobs/{job_id}/course")
def get_job_course(job_id: str) -> dict[str, object]:
    """
    Retrieve generated course metadata including modules, slide allocations,
    timestamps, and video paths.
    """
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")

    c_dir = courses_dir(job_id)
    meta_file = c_dir / "course_metadata.json"

    if meta_file.exists():
        try:
            data = json.loads(meta_file.read_text(encoding="utf-8"))
            return {
                "job_id": job_id,
                "status": job["status"],
                "stage": job["stage"],
                "has_course": True,
                "course": data,
                "error": None,
            }
        except Exception as exc:
            return {
                "job_id": job_id,
                "status": job["status"],
                "stage": job["stage"],
                "has_course": False,
                "course": None,
                "error": f"Failed to parse course metadata: {exc}",
            }

    return {
        "job_id": job_id,
        "status": job["status"],
        "stage": job["stage"],
        "has_course": False,
        "course": None,
        "error": job.get("error"),
    }


@router.get("/jobs/{job_id}/performance")
def get_job_performance(job_id: str) -> dict[str, object]:
    """Retrieve detailed generation-time performance benchmarks and stage metrics."""
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")

    c_dir = courses_dir(job_id)
    metrics_file = c_dir / "performance_metrics.json"

    if metrics_file.exists():
        try:
            data = json.loads(metrics_file.read_text(encoding="utf-8"))
            return {
                "job_id": job_id,
                "status": job["status"],
                "has_metrics": True,
                "metrics": data,
            }
        except Exception as exc:
            return {
                "job_id": job_id,
                "status": job["status"],
                "has_metrics": False,
                "error": f"Failed to parse performance metrics: {exc}",
            }

    return {
        "job_id": job_id,
        "status": job["status"],
        "has_metrics": False,
        "error": "Performance metrics not yet generated for this job.",
    }


@router.get("/courses/{job_id}/slides/{slide_number}")
def get_rendered_slide(job_id: str, slide_number: int) -> FileResponse:
    """Serve the 1080p rendered slide image."""
    c_dir = courses_dir(job_id)
    slide_file = c_dir / "slides" / f"slide_{slide_number}.png"

    if not slide_file.exists():
        raise HTTPException(status_code=404, detail=f"Slide image {slide_number} not found for job '{job_id}'.")

    return FileResponse(
        str(slide_file.resolve()),
        media_type="image/png",
        filename=f"slide_{slide_number}.png",
    )


@router.get("/courses/{job_id}/audio/{lang_code}/{slide_number}")
async def get_slide_audio(
    job_id: str,
    lang_code: str,
    slide_number: int,
    gender: str = Query("female", description="Voice gender: 'female' or 'male'"),
) -> FileResponse:
    """Serve the narration audio MP3 for a specific slide, language, and gender (Female default / Male)."""
    c_dir = courses_dir(job_id)
    is_female = gender.strip().lower() == "female"
    suffix = "_female" if is_female else ""
    norm_lang = "zh-CN" if lang_code.lower() in ("zh", "zh-cn") else lang_code

    # Candidate file names and language folder variants
    lang_dirs = [lang_code]
    if norm_lang not in lang_dirs:
        lang_dirs.append(norm_lang)
    if "zh-CN" not in lang_dirs and lang_code.startswith("zh"):
        lang_dirs.append("zh-CN")

    audio_file = None
    for ld in lang_dirs:
        candidates = [
            c_dir / "audio" / ld / f"slide_{slide_number}{suffix}.mp3",
            c_dir / "audio" / ld / f"slide_{slide_number}_{gender}.mp3",
            c_dir / "audio" / ld / f"Slide{slide_number}{suffix}.mp3",
            c_dir / "audio" / ld / f"Slide{slide_number}_{gender}.mp3",
        ]
        if not is_female:
            candidates.extend([
                c_dir / "audio" / ld / f"slide_{slide_number}.mp3",
                c_dir / "audio" / ld / f"Slide{slide_number}.mp3",
            ])
        for cand in candidates:
            if cand.exists() and cand.stat().st_size > 200:
                audio_file = cand
                break
        if audio_file:
            break

    # Priority 3: On-demand synthesis if requested voice gender does not exist yet
    if not audio_file:
        meta_file = c_dir / "course_metadata.json"
        if meta_file.exists():
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
                explanation_text = ""
                for mod in meta.get("modules", []):
                    for s in mod.get("slides", []):
                        if s.get("slide_number") == slide_number:
                            exps = s.get("explanations", {})
                            explanation_text = exps.get(lang_code, "") or exps.get(norm_lang, "") or exps.get("en", "")
                            break
                    if explanation_text:
                        break

                if explanation_text:
                    dest_lang = norm_lang if (c_dir / "audio" / norm_lang).exists() else lang_code
                    target_file = c_dir / "audio" / dest_lang / f"slide_{slide_number}{suffix}.mp3"
                    await synthesize_slide_audio(
                        text=explanation_text,
                        lang_code=dest_lang,
                        output_path=target_file,
                        gender="female" if is_female else "male",
                    )
                    if target_file.exists() and target_file.stat().st_size > 200:
                        audio_file = target_file
            except Exception as exc:
                logger.warning("On-demand %s audio synthesis failed for slide %d: %s", gender, slide_number, exc)

    # Graceful fallback: If requested gender (e.g. female) could not be synthesized, fallback to default narration
    if audio_file is None or not audio_file.exists():
        if is_female:
            fallback_candidates = [
                c_dir / "audio" / ld / f"slide_{slide_number}.mp3"
                for ld in lang_dirs
            ] + [
                c_dir / "audio" / ld / f"Slide{slide_number}.mp3"
                for ld in lang_dirs
            ]
            for cand in fallback_candidates:
                if cand.exists() and cand.stat().st_size > 200:
                    audio_file = cand
                    break

    if audio_file is None or not audio_file.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Audio for slide {slide_number} in language '{lang_code}' ({gender}) not found for job '{job_id}'.",
        )

    return FileResponse(
        str(audio_file.resolve()),
        media_type="audio/mpeg",
        filename=f"slide_{slide_number}_{lang_code}_{'female' if is_female else 'male'}.mp3",
        headers={"Accept-Ranges": "bytes"},
    )


@router.get("/courses/{job_id}/stream/module/{module_id}/{lang_code}")
def stream_module_video(job_id: str, module_id: str, lang_code: str) -> FileResponse:
    """Stream an individual module video for a specific language."""
    c_dir = courses_dir(job_id)
    clean_mod = module_id.lower().replace("-", "_")
    mod_video = c_dir / "modules" / clean_mod / f"{lang_code}.mp4"

    if not mod_video.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Module video '{clean_mod}' in language '{lang_code}' not found.",
        )

    return FileResponse(
        str(mod_video.resolve()),
        media_type="video/mp4",
        filename=f"{clean_mod}_{lang_code}.mp4",
    )


@router.get("/courses/{job_id}/stream/complete/{lang_code}")
def stream_complete_course(job_id: str, lang_code: str) -> FileResponse:
    """Stream the stitched complete course video for a specific language."""
    c_dir = courses_dir(job_id)
    course_video = c_dir / "complete_courses" / f"{lang_code}_course.mp4"

    if not course_video.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Complete course video in language '{lang_code}' not found.",
        )

    return FileResponse(
        str(course_video.resolve()),
        media_type="video/mp4",
        filename=f"course_{lang_code}.mp4",
    )


@router.get("/courses/{job_id}/download/module/{module_id}/{lang_code}")
def download_module_video(job_id: str, module_id: str, lang_code: str) -> FileResponse:
    """Download an individual module video file."""
    c_dir = courses_dir(job_id)
    clean_mod = module_id.lower().replace("-", "_")
    mod_video = c_dir / "modules" / clean_mod / f"{lang_code}.mp4"

    if not mod_video.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Module video '{clean_mod}' in language '{lang_code}' not found.",
        )

    return FileResponse(
        str(mod_video.resolve()),
        media_type="video/mp4",
        filename=f"ExplainStudio_{clean_mod}_{lang_code}.mp4",
        headers={"Content-Disposition": f'attachment; filename="ExplainStudio_{clean_mod}_{lang_code}.mp4"'},
    )


@router.get("/courses/{job_id}/download/complete/{lang_code}")
def download_complete_course(job_id: str, lang_code: str) -> FileResponse:
    """Download the complete stitched course video file."""
    c_dir = courses_dir(job_id)
    course_video = c_dir / "complete_courses" / f"{lang_code}_course.mp4"

    if not course_video.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Complete course video in language '{lang_code}' not found.",
        )

    return FileResponse(
        str(course_video.resolve()),
        media_type="video/mp4",
        filename=f"ExplainStudio_CompleteCourse_{lang_code}.mp4",
        headers={"Content-Disposition": f'attachment; filename="ExplainStudio_CompleteCourse_{lang_code}.mp4"'},
    )


@router.get("/courses/{job_id}/download/all/{lang_code}")
def download_all_modules_zip(job_id: str, lang_code: str) -> Response:
    """Download all individual module videos for a language in a single ZIP archive."""
    c_dir = courses_dir(job_id)
    modules_dir = c_dir / "modules"

    if not modules_dir.exists():
        raise HTTPException(status_code=404, detail="No generated modules found.")

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for mod_folder in sorted(modules_dir.glob("module_*")):
            video_file = mod_folder / f"{lang_code}.mp4"
            if video_file.exists():
                zip_file.write(video_file, arcname=f"{mod_folder.name}_{lang_code}.mp4")

    zip_bytes = zip_buffer.getvalue()
    if len(zip_bytes) == 0:
        raise HTTPException(
            status_code=404,
            detail=f"No video files found for language '{lang_code}'.",
        )

    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="ExplainStudio_AllModules_{lang_code}.zip"'},
    )
