"""Subtitle generation and formatting for SRT and WebVTT formats.

Produces synchronized subtitles from word-level timestamps or sentence timing.
Formats lines cleanly for educational video overlays.
"""
from __future__ import annotations

import math
from pathlib import Path
from pydantic import BaseModel

from ..tts.models import WordTimestamp


class SubtitleCue(BaseModel):
    """An individual subtitle display segment."""
    index: int
    start_time: float
    end_time: float
    text: str


def format_srt_time(seconds: float) -> str:
    """Format seconds into SRT timestamp: HH:MM:SS,mmm"""
    sec = max(0.0, seconds)
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    ms = int(round((sec - int(sec)) * 1000))
    if ms >= 1000:
        s += 1
        ms = 0
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def format_vtt_time(seconds: float) -> str:
    """Format seconds into WebVTT timestamp: HH:MM:SS.mmm"""
    sec = max(0.0, seconds)
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    ms = int(round((sec - int(sec)) * 1000))
    if ms >= 1000:
        s += 1
        ms = 0
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def generate_cues_from_words(
    words: list[WordTimestamp],
    max_words_per_cue: int = 7,
    max_chars_per_cue: int = 42,
) -> list[SubtitleCue]:
    """
    Cluster individual word timestamps into readable educational subtitle cues (1-2 lines).
    Respects sentence boundaries (periods, question marks, commas).
    """
    if not words:
        return []

    cues: list[SubtitleCue] = []
    current_words: list[WordTimestamp] = []
    current_len = 0
    cue_idx = 1

    for wt in words:
        current_words.append(wt)
        current_len += len(wt.word) + 1

        is_sentence_end = any(wt.word.endswith(punct) for punct in [".", "?", "!", ";"])
        is_limit_reached = len(current_words) >= max_words_per_cue or current_len >= max_chars_per_cue

        if is_sentence_end or is_limit_reached:
            start_t = current_words[0].start_time
            end_t = max(current_words[-1].end_time, start_t + 0.8)
            cue_text = " ".join(w.word for w in current_words).strip()

            cues.append(
                SubtitleCue(
                    index=cue_idx,
                    start_time=start_t,
                    end_time=end_t,
                    text=cue_text,
                )
            )
            cue_idx += 1
            current_words = []
            current_len = 0

    # Remaining words
    if current_words:
        start_t = current_words[0].start_time
        end_t = max(current_words[-1].end_time, start_t + 0.8)
        cue_text = " ".join(w.word for w in current_words).strip()
        cues.append(
            SubtitleCue(
                index=cue_idx,
                start_time=start_t,
                end_time=end_t,
                text=cue_text,
            )
        )

    return cues


def generate_cues_from_sentences(
    sentences: list[tuple[str, float]],  # [(sentence_text, duration_seconds)]
) -> list[SubtitleCue]:
    """
    Generate subtitle cues when only sentence-level durations are available.
    """
    cues: list[SubtitleCue] = []
    current_time = 0.0
    cue_idx = 1

    for text, dur in sentences:
        text = text.strip()
        if not text:
            current_time += dur
            continue

        cues.append(
            SubtitleCue(
                index=cue_idx,
                start_time=round(current_time, 3),
                end_time=round(current_time + dur, 3),
                text=text,
            )
        )
        cue_idx += 1
        current_time += dur

    return cues


def cues_to_srt(cues: list[SubtitleCue]) -> str:
    """Format subtitle cues into standard SubRip (.srt) text."""
    blocks: list[str] = []
    for c in cues:
        start = format_srt_time(c.start_time)
        end = format_srt_time(c.end_time)
        blocks.append(f"{c.index}\n{start} --> {end}\n{c.text}\n")
    return "\n".join(blocks).strip() + "\n"


def cues_to_vtt(cues: list[SubtitleCue]) -> str:
    """Format subtitle cues into WebVTT (.vtt) text."""
    blocks: list[str] = ["WEBVTT\n"]
    for c in cues:
        start = format_vtt_time(c.start_time)
        end = format_vtt_time(c.end_time)
        blocks.append(f"{c.index}\n{start} --> {end}\n{c.text}\n")
    return "\n".join(blocks).strip() + "\n"


def write_subtitles(
    cues: list[SubtitleCue],
    srt_path: Path,
    vtt_path: Path,
) -> tuple[Path, Path]:
    """Write both SRT and VTT subtitle files to disk."""
    srt_path.parent.mkdir(parents=True, exist_ok=True)
    vtt_path.parent.mkdir(parents=True, exist_ok=True)

    srt_path.write_text(cues_to_srt(cues), encoding="utf-8")
    vtt_path.write_text(cues_to_vtt(cues), encoding="utf-8")
    return srt_path, vtt_path
