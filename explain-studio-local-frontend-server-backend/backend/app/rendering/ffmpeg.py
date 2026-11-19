"""FFmpeg and FFprobe wrapper for video composition, normalization, and validation.

Uses the verified bundled static FFmpeg binary from imageio-ffmpeg on Windows.
Ensures uniform 1080p (1920x1080) @ 30fps H.264/AAC video encoding.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import imageio_ffmpeg

logger = logging.getLogger("explain_studio.rendering.ffmpeg")

_CACHED_FFMPEG_EXE: str | None = None


def get_ffmpeg_exe() -> str:
    """Return the absolute path to a working ffmpeg binary and ensure it is on PATH."""
    global _CACHED_FFMPEG_EXE
    if _CACHED_FFMPEG_EXE and os.path.exists(_CACHED_FFMPEG_EXE):
        return _CACHED_FFMPEG_EXE

    # Check imageio_ffmpeg bundled binary
    raw_exe = imageio_ffmpeg.get_ffmpeg_exe()
    bin_dir = os.path.dirname(raw_exe)

    # Ensure a canonical 'ffmpeg.exe' exists in the directory for external tools (Manim, pydub)
    target_exe = os.path.join(bin_dir, "ffmpeg.exe")
    if not os.path.exists(target_exe):
        try:
            shutil.copyfile(raw_exe, target_exe)
        except Exception:
            target_exe = raw_exe

    # Inject into current process PATH
    if bin_dir not in os.environ.get("PATH", ""):
        os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")

    _CACHED_FFMPEG_EXE = target_exe
    return target_exe


def run_ffmpeg(args: list[str], timeout_seconds: float = 300.0) -> subprocess.CompletedProcess[str]:
    """Execute ffmpeg with arguments, capturing output."""
    ffmpeg_exe = get_ffmpeg_exe()
    cmd = [ffmpeg_exe, "-y", "-hide_banner", "-loglevel", "warning"] + args
    logger.debug("Executing ffmpeg: %s", " ".join(cmd))

    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_seconds,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"FFmpeg command timed out after {timeout_seconds}s: {' '.join(cmd)}") from exc
    except Exception as exc:
        raise RuntimeError(f"Failed to execute FFmpeg: {exc}") from exc

    if proc.returncode != 0:
        err_msg = proc.stderr.strip() or proc.stdout.strip()
        logger.error("FFmpeg failed with code %d: %s", proc.returncode, err_msg)
        raise RuntimeError(f"FFmpeg error (code {proc.returncode}): {err_msg}")

    return proc


def probe_video(video_path: Path) -> dict[str, Any]:
    """
    Inspect a video file using ffmpeg -i to extract duration, dimensions, and streams.
    Works reliably without requiring a separate ffprobe binary.
    """
    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    ffmpeg_exe = get_ffmpeg_exe()
    cmd = [ffmpeg_exe, "-hide_banner", "-i", str(video_path.resolve())]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace")
    output = proc.stderr + "\n" + proc.stdout

    # Extract Duration: 00:00:10.50
    dur_match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", output)
    duration = 0.0
    if dur_match:
        h, m, s = dur_match.groups()
        duration = int(h) * 3600 + int(m) * 60 + float(s)

    # Extract resolution: e.g. 1920x1080
    res_match = re.search(r",\s*(\d{3,4})x(\d{3,4})", output)
    width = int(res_match.group(1)) if res_match else 1920
    height = int(res_match.group(2)) if res_match else 1080

    # Extract fps: e.g. 30 fps or 29.97 fps
    fps_match = re.search(r"(\d+(?:\.\d+)?)\s*fps", output)
    fps = float(fps_match.group(1)) if fps_match else 30.0

    has_video = "Video:" in output
    has_audio = "Audio:" in output

    return {
        "path": str(video_path),
        "duration_seconds": duration,
        "width": width,
        "height": height,
        "fps": fps,
        "has_video": has_video,
        "has_audio": has_audio,
        "file_size_bytes": video_path.stat().st_size if video_path.exists() else 0,
    }


def normalize_clip(
    input_clip: Path,
    output_clip: Path,
    target_duration: float,
    target_width: int = 1920,
    target_height: int = 1080,
    target_fps: int = 30,
) -> Path:
    """
    Normalize an input scene clip to standard broadcast specifications:
    - Exactly target_duration seconds (loops or freezes last frame if shorter, trims if longer)
    - 1920x1080 letterbox/pillarbox padding
    - 30 fps Constant Frame Rate (CFR)
    - yuv420p pixel format, libx264 high profile
    """
    output_clip.parent.mkdir(parents=True, exist_ok=True)
    info = probe_video(input_clip)
    current_dur = info["duration_seconds"]

    pad_needed = max(0.0, target_duration - current_dur)

    filters = [
        f"scale={target_width}:{target_height}:force_original_aspect_ratio=decrease",
        f"pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2:color=black",
        f"fps={target_fps}",
    ]

    if pad_needed > 0.05:
        filters.append(f"tpad=stop_mode=clone:stop_duration={pad_needed:.3f}")

    filters.append(f"trim=duration={target_duration:.3f}")
    filters.append("setpts=PTS-STARTPTS")

    vf = ",".join(filters)

    args = [
        "-i", str(input_clip.resolve()),
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-threads", "2",
        "-crf", "22",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(output_clip.resolve()),
    ]
    run_ffmpeg(args)
    return output_clip


def concatenate_clips(
    clip_paths: list[Path],
    output_path: Path,
) -> Path:
    """
    Concatenate multiple normalized scene MP4 clips into a single continuous video.
    Uses the FFmpeg concat demuxer for lossless, ultra-fast assembly.
    """
    if not clip_paths:
        raise ValueError("Cannot concatenate empty list of clips.")

    if len(clip_paths) == 1:
        shutil.copyfile(clip_paths[0], output_path)
        return output_path

    output_path.parent.mkdir(parents=True, exist_ok=True)
    concat_list_file = output_path.parent / f"concat_{output_path.stem}.txt"

    lines = []
    for p in clip_paths:
        clean_path = str(p.resolve()).replace("\\", "/")
        lines.append(f"file '{clean_path}'")

    concat_list_file.write_text("\n".join(lines), encoding="utf-8")

    try:
        args = [
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_list_file.resolve()),
            "-c", "copy",
            "-movflags", "+faststart",
            str(output_path.resolve()),
        ]
        run_ffmpeg(args)
    finally:
        if concat_list_file.exists():
            try:
                concat_list_file.unlink()
            except Exception:
                pass

    return output_path


def concatenate_audio_clips(
    audio_paths: list[Path],
    output_path: Path,
) -> Path:
    """
    Concatenate multiple audio MP3/WAV clips into a single audio file.
    """
    if not audio_paths:
        raise ValueError("Cannot concatenate empty list of audio clips.")

    if len(audio_paths) == 1:
        shutil.copyfile(audio_paths[0], output_path)
        return output_path

    output_path.parent.mkdir(parents=True, exist_ok=True)
    concat_list_file = output_path.parent / f"concat_audio_{output_path.stem}.txt"

    lines = []
    for p in audio_paths:
        clean_path = str(p.resolve()).replace("\\", "/")
        lines.append(f"file '{clean_path}'")

    concat_list_file.write_text("\n".join(lines), encoding="utf-8")

    try:
        args = [
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_list_file.resolve()),
            "-c:a", "aac",
            "-b:a", "192k",
            str(output_path.resolve()),
        ]
        run_ffmpeg(args)
    finally:
        if concat_list_file.exists():
            try:
                concat_list_file.unlink()
            except Exception:
                pass

    return output_path


def compose_final_video(
    video_path: Path,
    output_path: Path,
    audio_path: Path | None = None,
    subtitles_path: Path | None = None,
    burn_subtitles: bool = False,
    quality_profile: str = "production",
    target_width: int | None = None,
    target_height: int | None = None,
) -> Path:
    """
    Combine concatenated scene video with narration audio and optional subtitles.
    Subtitles are DISABLED by default.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    tw = target_width or (1280 if quality_profile == "preview" else 1920)
    th = target_height or (720 if quality_profile == "preview" else 1080)

    if quality_profile == "preview":
        scale_filter = f"scale={tw}:{th}:force_original_aspect_ratio=decrease,pad={tw}:{th}:(ow-iw)/2:(oh-ih)/2:color=black"
        fps_filter = "fps=15"
        crf = "26"
        preset = "ultrafast"
    else:
        scale_filter = f"scale={tw}:{th}:force_original_aspect_ratio=decrease,pad={tw}:{th}:(ow-iw)/2:(oh-ih)/2:color=black" if (target_width and target_height) else None
        fps_filter = None
        crf = "22"
        preset = "ultrafast"

    has_audio = audio_path is not None and audio_path.exists()
    has_subs = burn_subtitles and subtitles_path is not None and subtitles_path.exists()

    input_args: list[str] = ["-i", str(video_path.resolve())]
    if has_audio:
        input_args.extend(["-i", str(audio_path.resolve())])
    else:
        input_args.extend(["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"])

    vf_parts: list[str] = []
    if scale_filter:
        vf_parts.append(scale_filter)
    if fps_filter:
        vf_parts.append(fps_filter)
    if has_subs:
        sub_escaped = str(subtitles_path.resolve()).replace("\\", "/").replace(":", "\\:")
        vf_parts.append(
            f"subtitles='{sub_escaped}':force_style='FontSize=20,PrimaryColour=&H00FFFFFF,BackColour=&H80000000,BorderStyle=4'"
        )

    codec_args: list[str] = []
    if vf_parts:
        codec_args.extend(["-vf", ",".join(vf_parts)])
        codec_args.extend(["-c:v", "libx264", "-preset", preset, "-crf", crf, "-pix_fmt", "yuv420p"])
    else:
        codec_args.extend(["-c:v", "copy"])

    codec_args.extend(["-c:a", "aac", "-b:a", "192k", "-shortest"])
    codec_args.extend(["-movflags", "+faststart", str(output_path.resolve())])

    run_ffmpeg(input_args + codec_args, timeout_seconds=1800.0)
    return output_path
