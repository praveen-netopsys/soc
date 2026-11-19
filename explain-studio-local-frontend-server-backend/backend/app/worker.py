"""Background worker for sequential content extraction.

Runs as a lightweight single worker task within the FastAPI lifespan.
Guarantees safe atomic job claiming via SQLite and recovers interrupted jobs on startup.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from . import db, storage
from .extractor.pipeline import process_job_extraction

logger = logging.getLogger("explain_studio.worker")


def _assess_extraction_result(job_id: str, manifest) -> tuple[str, str]:
    """
    DEF-01: Assess whether extracted content is usable.
    Returns (status, stage_message).
    
    Rules:
    - 0 text blocks + 0 visual assets → failed (no usable content)
    - 0 text blocks + ≥1 visual assets → ready_for_ai (image-only: vision model will analyze)
    - ≥1 text blocks → ready_for_ai (text or mixed content)
    """
    total_text_blocks = sum(
        1 for b in manifest.blocks if b.block_type != "image_asset"
    )
    total_visual = manifest.total_assets

    if total_text_blocks == 0 and total_visual == 0:
        return (
            "failed",
            "No usable content found in uploaded sources. "
            "Please upload a document with text, slides, or images.",
        )

    if total_text_blocks == 0 and total_visual > 0:
        return (
            "ready_for_ai",
            f"Visual content ready for AI — {total_visual} image(s) extracted, "
            f"0 text blocks. Vision model will analyze the visual material.",
        )

    # Has text blocks (may also have visuals)
    asset_part = f", {total_visual} visual asset(s)" if total_visual > 0 else ""
    return (
        "ready_for_ai",
        f"Extraction complete — {total_text_blocks} text block(s){asset_part}. Ready for AI analysis.",
    )


def process_one_job_sync() -> bool:
    """
    Attempt to claim and process exactly one pending job synchronously.
    Returns True if a job was claimed and processed, False if queue was empty.
    Useful for unit/integration tests and manual processing triggers.
    """
    job = db.claim_next_pending_job()
    if not job:
        return False

    job_id = job["id"]
    logger.info("Claimed job %s for extraction", job_id)

    try:
        manifest = process_job_extraction(job_id)

        # DEF-01: assess content usability before marking ready
        status, stage = _assess_extraction_result(job_id, manifest)
        db.update_job_status(job_id, status=status, stage=stage)

        if status == "ready_for_ai":
            logger.info(
                "Successfully extracted content for job %s (%d blocks, %d assets); marked ready_for_ai",
                job_id,
                manifest.total_blocks,
                manifest.total_assets,
            )
        else:
            logger.warning(
                "Extraction for job %s produced no usable content (%d blocks, %d assets); marked failed",
                job_id,
                manifest.total_blocks,
                manifest.total_assets,
            )

    except Exception as exc:
        logger.exception("Extraction failed for job %s: %s", job_id, exc)
        db.update_job_status(
            job_id,
            status="failed",
            stage="Content extraction failed",
            error=str(exc),
        )

    return True


class ExtractionWorker:
    """Async worker loop managing the background extraction lifecycle."""

    def __init__(self, poll_interval_seconds: float = 1.0) -> None:
        self.poll_interval = poll_interval_seconds
        self._running = False
        self._task: asyncio.Task[None] | None = None

    async def _loop(self) -> None:
        while self._running:
            try:
                # Run the blocking extraction work in a separate thread to keep event loop free
                processed = await asyncio.to_thread(process_one_job_sync)
                if not processed:
                    await asyncio.sleep(self.poll_interval)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Error in extraction worker loop: %s", exc)
                await asyncio.sleep(self.poll_interval)

    def start(self) -> None:
        """Startup hook: recover any previously interrupted jobs, then start worker loop."""
        recovered = db.recover_interrupted_jobs()
        if recovered:
            logger.info("Recovered %d interrupted job(s) from previous backend run", recovered)

        self._running = True
        self._task = asyncio.create_task(self._loop(), name="extraction-worker")
        logger.info("Extraction worker started")

    async def stop(self) -> None:
        """Shutdown hook: signal worker to exit and await clean termination."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Extraction worker stopped")


# Global worker instance
worker = ExtractionWorker()
