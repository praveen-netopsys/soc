"""File storage utilities — all paths are UUID-based to prevent traversal."""
from __future__ import annotations

import shutil
from pathlib import Path

from . import config


def job_dir(job_id: str) -> Path:
    """Return the storage directory for raw uploaded files for a job."""
    return config.FILES_DIR / job_id


def ensure_job_dir(job_id: str) -> Path:
    d = job_dir(job_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def extracted_dir(job_id: str) -> Path:
    """Return the directory where extracted content and manifest for a job live."""
    return config.EXTRACTED_DIR / job_id


def ensure_extracted_dir(job_id: str) -> Path:
    d = extracted_dir(job_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "assets").mkdir(parents=True, exist_ok=True)
    return d


def ensure_tmp_dir() -> Path:
    config.TMP_DIR.mkdir(parents=True, exist_ok=True)
    return config.TMP_DIR


def analysis_dir(job_id: str) -> Path:
    """Return the directory where persistent AI analysis results for a job live."""
    return config.ANALYSIS_DIR / job_id


def ensure_analysis_dir(job_id: str) -> Path:
    d = analysis_dir(job_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def renders_dir(job_id: str) -> Path:
    """Return the working directory where scene clips, audio, and subtitle files live."""
    return config.RENDERS_DIR / job_id


def ensure_renders_dir(job_id: str) -> Path:
    d = renders_dir(job_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def final_video_path(job_id: str) -> Path:
    """Return the path to the final rendered MP4 video file for a job."""
    config.VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
    return config.VIDEOS_DIR / f"{job_id}.mp4"


def final_subtitles_path(job_id: str, fmt: str = "srt") -> Path:
    """Return the path to the final subtitle file for a job."""
    config.VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
    return config.VIDEOS_DIR / f"{job_id}.{fmt}"


def courses_dir(job_id: str) -> Path:
    """Return the root directory for structured multilingual course assets."""
    d = config.COURSES_DIR / job_id
    d.mkdir(parents=True, exist_ok=True)
    return d



def save_file(job_id: str, file_id: str, ext: str, data: bytes) -> Path:
    """Write file bytes; returns the stored path."""
    d = ensure_job_dir(job_id)
    stored_name = f"{file_id}.{ext}" if ext else file_id
    path = d / stored_name
    path.write_bytes(data)
    return path


def delete_job_files(job_id: str) -> None:
    """Remove all stored files, extracted assets, and analysis results for a job."""
    d = job_dir(job_id)
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
    ed = extracted_dir(job_id)
    if ed.exists():
        shutil.rmtree(ed, ignore_errors=True)
    ad = analysis_dir(job_id)
    if ad.exists():
        shutil.rmtree(ad, ignore_errors=True)
    rd = renders_dir(job_id)
    if rd.exists():
        shutil.rmtree(rd, ignore_errors=True)
    cd = courses_dir(job_id)
    if cd.exists():
        shutil.rmtree(cd, ignore_errors=True)
    vf = final_video_path(job_id)
    if vf.exists():
        vf.unlink(missing_ok=True)
    sf = final_subtitles_path(job_id, "srt")
    if sf.exists():
        sf.unlink(missing_ok=True)
    vtt = final_subtitles_path(job_id, "vtt")
    if vtt.exists():
        vtt.unlink(missing_ok=True)
