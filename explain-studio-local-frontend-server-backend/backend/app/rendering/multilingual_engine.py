"""Multilingual speech narration engine for Explain Studio.

Generates synchronized neural speech narration across all 6 supported languages:
1. English (en)
2. Chinese (Simplified) (zh-CN)
3. Malay (ms)
4. Tamil (ta)
5. Bangla (bn)
6. Telugu (te)

Uses original rendered PowerPoint slides as the visual foundation, high-quality
neural voices via edge-tts with automatic voice failover, and progressive
module-by-module generation for immediate playback without waiting for full exports.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Callable, Any

import edge_tts

from ..ai.multilingual.models import (
    SUPPORTED_LANGUAGES,
    CourseModule,
    MultilingualCourse,
    SlideExplanation,
)
from ..rendering.ffmpeg import probe_video
from ..storage import courses_dir

logger = logging.getLogger("explain_studio.rendering.multilingual")


async def synthesize_slide_audio(
    text: str,
    lang_code: str,
    output_path: Path,
    gender: str = "male",
    retry_count: int = 3,
) -> tuple[float, bool]:
    """
    Synthesize speech audio for a single slide explanation with automatic voice failover.
    Supports both Male and Female voices across all 6 supported languages.
    Returns a tuple of (audio duration in seconds, is_cache_hit boolean).
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Audio caching: reuse already synthesized audio if valid
    if output_path.exists() and output_path.stat().st_size > 1000:
        info = probe_video(output_path)
        dur = float(info.get("duration_seconds") or 1.0)
        if dur > 0.5:
            return dur, True

    clean_text = text.strip() if text and text.strip() else "Proceeding with this concept."

    lang_meta = SUPPORTED_LANGUAGES.get(lang_code, {})
    is_female = gender.strip().lower() == "female"
    if is_female:
        primary_voice = lang_meta.get("female_voice", "en-US-JennyNeural")
    else:
        primary_voice = lang_meta.get("male_voice", lang_meta.get("default_voice", "en-US-ChristopherNeural"))

    fallback_voices = lang_meta.get("fallback_voices", [])
    candidate_voices = [primary_voice] + [v for v in fallback_voices if v != primary_voice]

    last_error: Exception | None = None

    # Try each candidate voice with a fast 12s socket timeout to avoid indefinite stalls
    for voice in candidate_voices:
        try:
            communicate = edge_tts.Communicate(clean_text, voice)
            await asyncio.wait_for(communicate.save(str(output_path.resolve())), timeout=12.0)
            if output_path.exists() and output_path.stat().st_size > 500:
                info = probe_video(output_path)
                duration = float(info.get("duration_seconds") or 1.0)
                if duration > 0.5:
                    return max(1.0, duration), False
        except (asyncio.TimeoutError, Exception) as exc:
            last_error = exc
            if output_path.exists():
                try:
                    output_path.unlink()
                except OSError:
                    pass
            logger.debug(
                "TTS voice '%s' for '%s' failed/timed out (%s); failing over to next voice...",
                voice, lang_code, exc
            )
            await asyncio.sleep(0.3)

    # Final retry with primary voice after brief cooling period if all candidates had an issue
    try:
        communicate = edge_tts.Communicate(clean_text, primary_voice)
        await asyncio.wait_for(communicate.save(str(output_path.resolve())), timeout=15.0)
        if output_path.exists() and output_path.stat().st_size > 500:
            info = probe_video(output_path)
            duration = float(info.get("duration_seconds") or 1.0)
            return max(1.0, duration), False
    except Exception as exc:
        last_error = exc
        if output_path.exists():
            try:
                output_path.unlink()
            except OSError:
                pass

    # If all voices and retries exhausted
    err_msg = f"Edge-TTS synthesis failed for language '{lang_code}' across voices {candidate_voices}: {last_error}"
    logger.error(err_msg)
    raise RuntimeError(err_msg) from last_error


async def generate_all_slide_audio(
    course: MultilingualCourse,
    job_id: str,
    progress_cb: Callable[[str, float], None] | None = None,
    checkpoint_cb: Callable[[MultilingualCourse], None] | None = None,
    overall_start_time: float | None = None,
) -> dict[str, Any]:
    """
    Synthesize narration audio progressively module-by-module and language-by-language.
    Flushes progress checkpoints so frontend can begin playback of Module 1 immediately.
    Returns complete performance and timing metrics.
    """
    import time
    tts_start = time.perf_counter()
    pipeline_start = overall_start_time or tts_start

    c_dir = courses_dir(job_id)
    audio_base = c_dir / "audio"

    all_slides: list[SlideExplanation] = []
    for mod in course.modules:
        all_slides.extend(mod.slides)

    total_tasks = len(all_slides) * len(SUPPORTED_LANGUAGES)
    completed_tasks = 0
    cache_hits = 0
    cache_misses = 0

    lang_timings: dict[str, float] = {l: 0.0 for l in SUPPORTED_LANGUAGES}
    module_timings: dict[str, float] = {}
    first_module_ready_seconds: float | None = None

    # Rate-limit concurrency per language for optimal Edge TTS throughput
    lang_semaphores = {l: asyncio.Semaphore(4) for l in SUPPORTED_LANGUAGES}

    async def _synth_slide_for_lang(
        slide: SlideExplanation,
        lang_code: str,
        lang_meta: dict[str, Any],
    ) -> float:
        nonlocal completed_tasks, cache_hits, cache_misses
        t0 = time.perf_counter()
        async with lang_semaphores[lang_code]:
            explanation_text = slide.explanations.get(lang_code, "")
            out_file_male = audio_base / lang_code / f"slide_{slide.slide_number}.mp3"
            out_file_female = audio_base / lang_code / f"slide_{slide.slide_number}_female.mp3"

            # Pre-generate male narration
            duration_m, was_cached_m = await synthesize_slide_audio(
                text=explanation_text,
                lang_code=lang_code,
                output_path=out_file_male,
                gender="male",
            )

            # Pre-generate female narration (Admin default)
            duration_f, was_cached_f = await synthesize_slide_audio(
                text=explanation_text,
                lang_code=lang_code,
                output_path=out_file_female,
                gender="female",
            )

            synth_time = time.perf_counter() - t0
            lang_timings[lang_code] += synth_time

            if was_cached_m and was_cached_f:
                cache_hits += 1
            else:
                cache_misses += 1

            duration = duration_f if duration_f > 0.5 else duration_m
            slide.durations_seconds[lang_code] = round(duration, 2)
            slide.audio_files[lang_code] = str(out_file_female.relative_to(c_dir)).replace("\\", "/")
            slide.audio_urls[lang_code] = f"/v1/courses/{job_id}/audio/{lang_code}/{slide.slide_number}?gender=female"

            completed_tasks += 1
            if progress_cb:
                pct = 0.50 + (completed_tasks / max(1, total_tasks)) * 0.45  # 50% -> 95%
                progress_cb(f"Synthesizing {lang_meta['native_name']} narration (Slide {slide.slide_number})...", pct)

            return duration

    # Progressive generation: process Module by Module
    for mod_idx, mod in enumerate(course.modules):
        mod_start = time.perf_counter()
        mod.module_ready = mod.module_ready or {}
        # Concurrently synthesize all slides in this module across all languages
        module_tasks = []
        for slide in mod.slides:
            for lang_code, lang_meta in SUPPORTED_LANGUAGES.items():
                module_tasks.append(
                    _synth_slide_for_lang(slide, lang_code, lang_meta)
                )

        await asyncio.gather(*module_tasks)

        # Compute module boundaries and mark module ready for each language
        for lang_code in SUPPORTED_LANGUAGES:
            cur_time = 0.0
            for slide in mod.slides:
                dur = slide.durations_seconds.get(lang_code, 1.0)
                slide.module_start_seconds[lang_code] = round(cur_time, 2)
                cur_time += dur
                slide.module_end_seconds[lang_code] = round(cur_time, 2)

            mod.durations_seconds[lang_code] = round(cur_time, 2)
            mod.module_ready[lang_code] = True

        mod_elapsed = time.perf_counter() - mod_start
        module_timings[mod.module_id] = round(mod_elapsed, 2)

        if mod_idx == 0:
            first_module_ready_seconds = round(time.perf_counter() - pipeline_start, 2)
            logger.info("[Job %s] Module 1 ready for instant playback in %.2fs!", job_id, first_module_ready_seconds)

        logger.info("[Job %s] Module %s narration ready across all 6 languages in %.2fs!", job_id, mod.module_id, mod_elapsed)

        # Trigger checkpoint callback so frontend can immediately play this module!
        if checkpoint_cb:
            try:
                checkpoint_cb(course)
            except Exception as exc:
                logger.warning("Checkpoint callback error: %s", exc)

    # Compute overall course-level timestamps
    for lang_code in SUPPORTED_LANGUAGES:
        course_time = 0.0
        for mod in course.modules:
            for slide in mod.slides:
                slide.course_start_seconds[lang_code] = round(course_time + slide.module_start_seconds[lang_code], 2)
                slide.course_end_seconds[lang_code] = round(course_time + slide.module_end_seconds[lang_code], 2)
            course_time += mod.durations_seconds.get(lang_code, 0.0)
        course.languages_ready[lang_code] = True

    if checkpoint_cb:
        checkpoint_cb(course)

    tts_total_seconds = round(time.perf_counter() - tts_start, 2)
    return {
        "tts_total_seconds": tts_total_seconds,
        "time_per_language": {k: round(v, 2) for k, v in lang_timings.items()},
        "time_per_module": module_timings,
        "first_module_ready_seconds": first_module_ready_seconds or tts_total_seconds,
        "audio_cache_hits": cache_hits,
        "audio_cache_misses": cache_misses,
        "total_audio_tasks": total_tasks,
    }
