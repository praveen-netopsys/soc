"""SQLite persistence for jobs and uploaded files.

All functions import `config` as a module (not individual names) so that
pytest monkeypatching of `app.config.DB_PATH` and `app.config.FILES_DIR`
takes effect at call time, not import time.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from . import config


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    """Create tables if they do not exist, and migrate columns if needed. Safe to call multiple times."""
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id              TEXT PRIMARY KEY,
                status          TEXT NOT NULL DEFAULT 'pending',
                stage           TEXT NOT NULL DEFAULT 'Queued - awaiting processing',
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL,
                audience        TEXT NOT NULL DEFAULT 'beginner',
                animation_style TEXT NOT NULL DEFAULT 'auto',
                subtitles       INTEGER NOT NULL DEFAULT 0,
                has_text        INTEGER NOT NULL DEFAULT 0,
                pasted_text     TEXT,
                error           TEXT,
                output_path     TEXT,
                target_duration_seconds REAL DEFAULT 120.0,
                aspect_ratio    TEXT NOT NULL DEFAULT '9:16',
                progress_pct    REAL DEFAULT 0.0,
                eta_seconds     REAL,
                progress_detail TEXT
            );

            CREATE TABLE IF NOT EXISTS job_files (
                id            TEXT PRIMARY KEY,
                job_id        TEXT NOT NULL,
                original_name TEXT NOT NULL,
                stored_name   TEXT NOT NULL,
                kind          TEXT NOT NULL,
                size_bytes    INTEGER NOT NULL,
                created_at    TEXT NOT NULL,
                FOREIGN KEY (job_id) REFERENCES jobs(id)
            );
            """
        )

        # Migration: ensure optional columns exist for existing DBs
        columns = [row["name"] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()]
        if "pasted_text" not in columns:
            conn.execute("ALTER TABLE jobs ADD COLUMN pasted_text TEXT")
        if "target_duration_seconds" not in columns:
            conn.execute("ALTER TABLE jobs ADD COLUMN target_duration_seconds REAL DEFAULT 120.0")
        if "aspect_ratio" not in columns:
            conn.execute("ALTER TABLE jobs ADD COLUMN aspect_ratio TEXT DEFAULT '9:16'")
        if "progress_pct" not in columns:
            conn.execute("ALTER TABLE jobs ADD COLUMN progress_pct REAL DEFAULT 0.0")
        if "eta_seconds" not in columns:
            conn.execute("ALTER TABLE jobs ADD COLUMN eta_seconds REAL")
        if "progress_detail" not in columns:
            conn.execute("ALTER TABLE jobs ADD COLUMN progress_detail TEXT")


def create_job(
    *,
    job_id: str,
    has_text: bool,
    pasted_text: str | None = None,
    audience: str,
    animation_style: str,
    subtitles: bool = False,
    target_duration_seconds: float = 120.0,
    aspect_ratio: str = "9:16",
    status: str = "pending",
    stage: str = "Queued - awaiting processing",
) -> dict[str, Any]:
    now = _now()
    with _connect() as conn:
        # Self-healing migration for isolated test databases
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
        if not columns:
            init_db()
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
        if "aspect_ratio" not in columns:
            conn.execute("ALTER TABLE jobs ADD COLUMN aspect_ratio TEXT DEFAULT '9:16'")
        if "target_duration_seconds" not in columns:
            conn.execute("ALTER TABLE jobs ADD COLUMN target_duration_seconds REAL DEFAULT 120.0")
        if "pasted_text" not in columns:
            conn.execute("ALTER TABLE jobs ADD COLUMN pasted_text TEXT")
        if "progress_pct" not in columns:
            conn.execute("ALTER TABLE jobs ADD COLUMN progress_pct REAL DEFAULT 0.0")
        if "eta_seconds" not in columns:
            conn.execute("ALTER TABLE jobs ADD COLUMN eta_seconds REAL")
        if "progress_detail" not in columns:
            conn.execute("ALTER TABLE jobs ADD COLUMN progress_detail TEXT")

        conn.execute(
            """
            INSERT INTO jobs
                (id, status, stage, created_at, updated_at,
                 audience, animation_style, subtitles, has_text, pasted_text, target_duration_seconds, aspect_ratio)
            VALUES
                (?, ?, ?, ?, ?,
                 ?, ?, ?, ?, ?, ?, ?)
            """,
            (job_id, status, stage, now, now, audience, animation_style, int(subtitles), int(has_text), pasted_text, float(target_duration_seconds), aspect_ratio),
        )
    result = get_job(job_id)
    assert result is not None
    return result


def add_job_file(
    *,
    file_id: str,
    job_id: str,
    original_name: str,
    stored_name: str,
    kind: str,
    size_bytes: int,
) -> None:
    now = _now()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO job_files
                (id, job_id, original_name, stored_name, kind, size_bytes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (file_id, job_id, original_name, stored_name, kind, size_bytes, now),
        )


def get_job(job_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if row is None:
            return None
        job = dict(row)
        files = conn.execute(
            "SELECT * FROM job_files WHERE job_id = ? ORDER BY created_at",
            (job_id,),
        ).fetchall()
        job["files"] = [dict(f) for f in files]
    return job


def update_job_status(
    job_id: str,
    *,
    status: str,
    stage: str | None = None,
    error: str | None = None,
    output_path: str | None = None,
    progress_pct: float | None = None,
    eta_seconds: float | None = None,
    progress_detail: str | None = None,
) -> None:
    now = _now()
    with _connect() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
        if "progress_pct" not in columns:
            conn.execute("ALTER TABLE jobs ADD COLUMN progress_pct REAL DEFAULT 0.0")
        if "eta_seconds" not in columns:
            conn.execute("ALTER TABLE jobs ADD COLUMN eta_seconds REAL")
        if "progress_detail" not in columns:
            conn.execute("ALTER TABLE jobs ADD COLUMN progress_detail TEXT")

        conn.execute(
            """
            UPDATE jobs
            SET status          = ?,
                stage           = COALESCE(?, stage),
                error           = COALESCE(?, error),
                output_path     = COALESCE(?, output_path),
                progress_pct    = COALESCE(?, progress_pct),
                eta_seconds     = COALESCE(?, eta_seconds),
                progress_detail = COALESCE(?, progress_detail),
                updated_at      = ?
            WHERE id = ?
            """,
            (status, stage, error, output_path, progress_pct, eta_seconds, progress_detail, now, job_id),
        )


def recover_interrupted_jobs() -> int:
    """
    Recover any jobs left in incomplete states upon server/worker startup.
    - Jobs left in 'extracting' are reset back to 'pending'.
    - Jobs left in 'analyzing' are reset back to 'ready_for_ai'.
    - Jobs left in 'processing' (video rendering) are marked failed — they cannot be safely resumed.
    """
    now = _now()
    with _connect() as conn:
        c1 = conn.execute(
            """
            UPDATE jobs
            SET status = 'pending',
                stage = 'Processing was interrupted; requeued for extraction',
                updated_at = ?
            WHERE status = 'extracting'
            """,
            (now,),
        )
        c2 = conn.execute(
            """
            UPDATE jobs
            SET status = 'ready_for_ai',
                stage = 'AI analysis was interrupted by backend restart; ready to re-analyze',
                updated_at = ?
            WHERE status = 'analyzing'
            """,
            (now,),
        )
        # DEF-08: Orphaned processing jobs — video rendering cannot be safely resumed mid-encode
        c3 = conn.execute(
            """
            UPDATE jobs
            SET status = 'failed',
                stage = 'Video rendering was interrupted by backend restart. Re-trigger rendering to restart.',
                error = 'Pipeline was interrupted during video rendering. Re-submit for rendering.',
                updated_at = ?
            WHERE status = 'processing'
            """,
            (now,),
        )
        return c1.rowcount + c2.rowcount + c3.rowcount


def claim_next_pending_job() -> dict[str, Any] | None:
    """Safely and atomically claim the oldest pending job for extraction."""
    now = _now()
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT id FROM jobs
            WHERE status = 'pending'
            ORDER BY created_at ASC
            LIMIT 1
            """
        ).fetchone()
        if not row:
            return None
        job_id = row["id"]
        cursor = conn.execute(
            """
            UPDATE jobs
            SET status = 'extracting',
                stage = 'Extracting content from sources',
                updated_at = ?
            WHERE id = ? AND status = 'pending'
            """,
            (now, job_id),
        )
        if cursor.rowcount == 0:
            return None
    return get_job(job_id)
