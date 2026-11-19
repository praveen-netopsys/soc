"""GPU Safety and Concurrency Manager for Explain Studio.

Enforces strict GPU discipline on the company's shared server:
- Exactly ONE AI inference task at a time within Explain Studio.
- No concurrent requests to the two models (sequential execution).
- Queues requests safely when resources are currently in use.
- Tracks active inference jobs, models, and tasks.
- Never falls back to CPU inference.
"""
from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from typing import Any, Generator

logger = logging.getLogger("explain_studio.ai.gpu")


class GPUSafetyManager:
    """Thread-safe singleton managing serialized GPU access."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._queue_lock = threading.Lock()
        self._queue_count = 0
        self._is_busy = False
        self._current_job_id: str | None = None
        self._current_model: str | None = None
        self._current_task: str | None = None
        self._started_at: float | None = None

    @property
    def is_busy(self) -> bool:
        return self._is_busy

    @property
    def queue_count(self) -> int:
        with self._queue_lock:
            return self._queue_count

    def get_status(self) -> dict[str, Any]:
        """Return current status of GPU utilization within Explain Studio."""
        elapsed = round(time.perf_counter() - self._started_at, 2) if self._started_at else None
        return {
            "is_busy": self._is_busy,
            "current_job_id": self._current_job_id,
            "current_model": self._current_model,
            "current_task": self._current_task,
            "queue_count": self._queue_count,
            "elapsed_seconds": elapsed,
        }

    @contextmanager
    def guard(
        self,
        job_id: str,
        model: str,
        task: str,
        timeout: float | None = None,
    ) -> Generator[None, None, None]:
        """
        Context manager that acquires the single GPU execution lock.
        If another inference task is active, queues until the lock becomes available.
        Always releases the lock and cleans up state in finally block.
        """
        with self._queue_lock:
            self._queue_count += 1

        logger.info(
            "GPU task requested: job=%s, model=%s, task=%s (queued tasks: %d)",
            job_id,
            model,
            task,
            self._queue_count,
        )

        acquired = self._lock.acquire(timeout=timeout if timeout is not None else -1)
        if not acquired:
            with self._queue_lock:
                self._queue_count = max(0, self._queue_count - 1)
            raise TimeoutError(
                f"Timed out waiting for GPU availability for job {job_id} ({task})"
            )

        with self._queue_lock:
            self._queue_count = max(0, self._queue_count - 1)

        self._is_busy = True
        self._current_job_id = job_id
        self._current_model = model
        self._current_task = task
        self._started_at = time.perf_counter()

        logger.info("GPU lock acquired: job=%s, model=%s, task=%s", job_id, model, task)
        try:
            yield
        finally:
            elapsed = round(time.perf_counter() - (self._started_at or time.perf_counter()), 2)
            logger.info(
                "GPU lock released: job=%s, model=%s, task=%s (duration: %ss)",
                job_id,
                model,
                task,
                elapsed,
            )
            self._is_busy = False
            self._current_job_id = None
            self._current_model = None
            self._current_task = None
            self._started_at = None
            self._lock.release()


# Global GPU safety singleton
gpu_manager = GPUSafetyManager()
