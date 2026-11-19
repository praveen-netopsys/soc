"""Explain Studio backend — Milestone 4: Remote AI Integration Foundation.

New in this milestone:
-----------------------
- Ollama API client for remote AI foundation via private SSH tunnel
- GET /v1/ai/health — check Ollama connection and tunnel latency
- GET /v1/ai/models — inspect installed models and verify required models (qwen2.5:14b, qwen2.5vl:7b)
- Strict safety rules: no auto model downloads, no GPU modifications, no unapproved inference

Still deliberately absent:
--------------------------
- Automatic model downloading (ollama pull)
- Automatic generation inference
- Video rendering
"""
from __future__ import annotations

import sys
if sys.platform == "win32":
    try:
        from asyncio.proactor_events import _ProactorBasePipeTransport
        _orig_call_connection_lost = _ProactorBasePipeTransport._call_connection_lost

        def _silenced_call_connection_lost(self, exc):
            try:
                _orig_call_connection_lost(self, exc)
            except (ConnectionResetError, OSError):
                pass

        _ProactorBasePipeTransport._call_connection_lost = _silenced_call_connection_lost
    except Exception:
        pass

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import allowed_origins
from .db import init_db
from .health import check_all_services, check_liveness
from .routers.ai import router as ai_router
from .routers.jobs import router as jobs_router
from .routers.multilingual import router as multilingual_router
from .worker import worker


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialise SQLite database and manage single-worker background processing."""
    init_db()
    worker.start()
    try:
        yield
    finally:
        await worker.stop()


app = FastAPI(
    title="Explain Studio API",
    version="0.6.0",
    description=(
        "Educational video generator backend — "
        "content extraction, AI educational director, narration scriptwriting, and storyboard generation."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Accept", "Content-Type"],
)

app.include_router(jobs_router)
app.include_router(ai_router)
app.include_router(multilingual_router)


# ── Health & Info endpoints ───────────────────────────────────────────────────

@app.get("/health")
def health() -> dict[str, object]:
    return {
        "status": "ok",
        "service": "explain-studio-backend",
        "version": "0.6.0",
        "mode": "multilingual-educational-video-platform",
        "ai_enabled": True,
    }


@app.get("/health/live")
def health_live() -> dict[str, object]:
    """Fast liveness check for Kubernetes/monitors/frontend."""
    return check_liveness()


@app.get("/health/ready")
def health_ready() -> dict[str, object]:
    """Overall readiness check verifying database, storage, and platform services."""
    res = check_all_services()
    return {
        "ready": res["ready"],
        "status": res["status"],
        "ai_server_ready": res["ai_server_ready"],
        "total_latency_ms": res["total_latency_ms"],
    }


@app.get("/health/services")
def health_services() -> dict[str, object]:
    """Detailed multi-service diagnostic report across all infrastructure dependencies."""
    return check_all_services()


@app.get("/v1/info")
def info() -> dict[str, object]:
    return {
        "name": "Explain Studio",
        "api_version": "0.6.0",
        "capabilities": {
            "text_input": True,
            "pptx_input": True,
            "image_input": True,
            "file_upload": True,
            "job_persistence": True,
            "content_extraction": True,
            "universal_manifest": True,
            "remote_ai_discovery": True,
            "ai_inference": True,
            "ai_content_understanding": True,
            "visual_analysis": True,
            "educational_planner": True,
            "narration_script_generator": True,
            "storyboard_generator": True,
            "adaptive_duration_planner": True,
            "programmatic_qa": True,
            "video_generation": False,
            "english_voiceover": False,
        },
        "note": "Sprint 2: Complete AI Educational Director active (planning, script, storyboard, duration, QA).",
    }
