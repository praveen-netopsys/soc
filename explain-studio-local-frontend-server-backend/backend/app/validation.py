"""Upload validation — file types, sizes, deep format inspection, and streaming."""
from __future__ import annotations

import re
import shutil
import uuid
import zipfile
from pathlib import Path

from fastapi import HTTPException, UploadFile
from PIL import Image

from . import config, storage


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ext(filename: str) -> str:
    """Return the lowercase extension without the leading dot."""
    return Path(filename).suffix.lstrip(".").lower()


def sanitize_filename(name: str) -> str:
    """Strip directory components and characters unsafe for display or storage."""
    name = Path(name).name
    name = re.sub(r"[\x00-\x1f\x7f]", "", name)
    name = name.lstrip(". ")
    name = re.sub(r"[^\w\-. ]", "_", name)
    return name[:200] or "file"


def file_kind(filename: str) -> str:
    """Return one of 'presentation', 'image', or 'text'."""
    ext = _ext(filename)
    if ext == "pptx":
        return "presentation"
    if ext in {"png", "jpg", "jpeg", "webp"}:
        return "image"
    return "text"


# ── Basic Validators ──────────────────────────────────────────────────────────

def validate_file_count(uploads: list[UploadFile]) -> None:
    if len(uploads) > config.MAX_FILES:
        raise HTTPException(
            status_code=422,
            detail=f"Too many files. Maximum is {config.MAX_FILES} per request.",
        )


def validate_extension(upload: UploadFile) -> None:
    name = upload.filename or "file"
    ext = _ext(name)
    if ext not in config.ALLOWED_EXTENSIONS:
        safe = sanitize_filename(name)
        allowed = ", ".join(f".{e}" for e in sorted(config.ALLOWED_EXTENSIONS))
        raise HTTPException(
            status_code=422,
            detail=(
                f'File "{safe}" has unsupported extension ".{ext}". '
                f"Allowed types: {allowed}."
            ),
        )


def validate_at_least_one_input(text: str | None, uploads: list[UploadFile]) -> None:
    has_text = bool(text and text.strip())
    if not has_text and not uploads:
        raise HTTPException(
            status_code=422,
            detail="Provide at least one input: paste text or upload a supported file.",
        )


# ── Deep Content Validation ───────────────────────────────────────────────────

def validate_file_content(temp_path: Path, filename: str) -> None:
    """Perform format-specific content checks (ZIP structure for PPTX, Pillow verify for images)."""
    ext = _ext(filename)
    safe = sanitize_filename(filename)

    # 1. PPTX structure check
    if ext == "pptx":
        if not zipfile.is_zipfile(temp_path):
            raise HTTPException(
                status_code=422,
                detail=f'File "{safe}" is not a valid PowerPoint (.pptx) file (missing ZIP container).',
            )
        try:
            with zipfile.ZipFile(temp_path, "r") as zf:
                if zf.testzip() is not None:
                    raise HTTPException(
                        status_code=422,
                        detail=f'File "{safe}" is corrupted (corrupt ZIP structure).',
                    )
                # Verify standard Office OpenXML content types declaration
                namelist = zf.namelist()
                if "[Content_Types].xml" not in namelist:
                    raise HTTPException(
                        status_code=422,
                        detail=f'File "{safe}" is not a valid PowerPoint presentation (missing [Content_Types].xml).',
                    )
        except zipfile.BadZipFile:
            raise HTTPException(
                status_code=422,
                detail=f'File "{safe}" is not a valid PowerPoint presentation (bad ZIP header).',
            )

    # 2. Image integrity check
    elif ext in {"png", "jpg", "jpeg", "webp"}:
        try:
            with Image.open(temp_path) as img:
                img.verify()
        except Exception as exc:
            raise HTTPException(
                status_code=422,
                detail=f'File "{safe}" is corrupted or not a valid {ext.upper()} image: {exc}',
            )


# ── Chunked Streaming & Atomic Move ───────────────────────────────────────────

def stream_and_validate_file(
    upload: UploadFile,
    *,
    job_id: str,
    file_id: str,
    current_total_bytes: int,
) -> tuple[str, str, str, int]:
    """
    Stream an UploadFile in 64KB chunks to a temporary file.
    Enforces per-file and cumulative upload limits in real-time.
    Runs deep format validation on the completed temp file.
    Atomically moves the temp file to storage/files/{job_id}/{file_id}.{ext}.
    Returns (safe_original_name, stored_name, kind, size_bytes).
    """
    raw_name = upload.filename or "file"
    safe_name = sanitize_filename(raw_name)
    ext = _ext(safe_name)
    stored_name = f"{file_id}.{ext}" if ext else file_id

    tmp_dir = storage.ensure_tmp_dir()
    temp_path = tmp_dir / f"stream_{uuid.uuid4().hex}.tmp"

    bytes_written = 0
    chunk_size = 64 * 1024  # 64 KB

    try:
        with open(temp_path, "wb") as out_f:
            while True:
                chunk = upload.file.read(chunk_size)
                if not chunk:
                    break
                bytes_written += len(chunk)

                # Real-time per-file limit enforcement
                if bytes_written > config.MAX_FILE_BYTES:
                    limit_mb = config.MAX_FILE_BYTES // (1024 * 1024)
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            f'File "{safe_name}" exceeds the {limit_mb} MB per-file limit.'
                        ),
                    )

                # Real-time total upload limit enforcement
                if current_total_bytes + bytes_written > config.MAX_TOTAL_BYTES:
                    limit_mb = config.MAX_TOTAL_BYTES // (1024 * 1024)
                    raise HTTPException(
                        status_code=422,
                        detail=f"Combined upload size exceeds the {limit_mb} MB limit per request.",
                    )

                out_f.write(chunk)

        # Deep format validation
        validate_file_content(temp_path, safe_name)

        # Atomically move to final job directory
        dest_dir = storage.ensure_job_dir(job_id)
        final_path = dest_dir / stored_name
        shutil.move(temp_path, final_path)

        kind = file_kind(safe_name)
        return safe_name, stored_name, kind, bytes_written

    except Exception:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        raise
