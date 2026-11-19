"""End-to-End Corporate Training Video Generation Pipeline Orchestrator.

Integrates:
PowerPoint Document → Content Extraction → Lesson Grouper & Planner → Narration Script
→ 2D Vector Animation Storyboard → TTS Narration → Scene-by-Scene 2D Rendering
→ Individual Lesson MP4 Videos → Master Combined Course MP4 (16:9 Landscape, Zero Burned Subtitles)
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from ..ai.director.duration import calculate_adaptive_duration
from ..ai.director.pipeline import run_complete_director_pipeline
from ..ai.director.planner import _generate_fallback_plan
from ..ai.director.qa import validate_director_artifacts
from ..ai.director.scriptwriter import _generate_fallback_script
from ..ai.director.storyboarder import _generate_fallback_storyboard
from ..ai.models import (
    AIAnalysisResult,
    CompleteDirectorResult,
    DurationEstimate,
    EducationalPlan,
    NarrationScript,
    QualityReport,
    Storyboard,
)
from ..config import DEFAULT_TTS_VOICE, TTS_PROVIDER, VIDEO_FPS, VIDEO_HEIGHT, VIDEO_WIDTH
from ..db import get_job, update_job_status
from ..extractor.models import ExtractionManifest
from ..extractor.pipeline import process_job_extraction
from ..rendering.ffmpeg import (
    compose_final_video,
    concatenate_audio_clips,
    concatenate_clips,
    probe_video,
)
from ..rendering.models import (
    EngineNeutralSceneSpec,
    FinalVideoResult,
    RenderEngineType,
    SceneRenderResult,
    get_dimensions_for_aspect_ratio,
)
from ..rendering.registry import default_registry
from ..storage import (
    analysis_dir,
    ensure_analysis_dir,
    ensure_renders_dir,
    extracted_dir,
    final_subtitles_path,
    final_video_path,
    job_dir,
)
from ..tts.factory import AudioNarrationCoordinator, get_tts_provider

logger = logging.getLogger("explain_studio.pipeline.orchestrator")


async def run_video_generation_pipeline(job_id: str) -> FinalVideoResult:
    """
    Execute the entire corporate training video production pipeline for a job.
    Produces individual lesson videos and a master combined course MP4.
    """
    job = get_job(job_id)
    if job is None:
        raise ValueError(f"Job '{job_id}' not found.")

    update_job_status(job_id, status="processing", stage="Initializing corporate video production pipeline...")
    renders_path = ensure_renders_dir(job_id)
    out_dir = ensure_analysis_dir(job_id)

    try:
        # ── Step 1: Ensure content extraction is complete ────────────────────────
        manifest_path = extracted_dir(job_id) / "manifest.json"
        if not manifest_path.exists():
            update_job_status(job_id, status="processing", stage="Extracting presentation slides and diagrams...")
            manifest = await asyncio.to_thread(process_job_extraction, job_id)
        else:
            manifest = ExtractionManifest.model_validate(json.loads(manifest_path.read_text(encoding="utf-8")))

        # ── Step 2: Ensure AI Director artifacts exist ───────────────────────────
        plan_file = out_dir / "plan.json"
        script_file = out_dir / "script.json"
        storyboard_file = out_dir / "storyboard.json"

        if not plan_file.exists() or not script_file.exists() or not storyboard_file.exists():
            update_job_status(job_id, status="processing", stage="Grouping presentation into training lessons...")

            from ..ai.client import OllamaClient
            client = OllamaClient()
            health = client.check_connection()

            if health.get("connected"):
                await asyncio.to_thread(run_complete_director_pipeline, job_id=job_id)
            else:
                logger.info("Remote Ollama is offline. Employing local deterministic corporate training director fallback.")
                analysis_file = out_dir / "analysis.json"
                if analysis_file.exists():
                    analysis = AIAnalysisResult.model_validate(json.loads(analysis_file.read_text(encoding="utf-8")))
                else:
                    analysis = AIAnalysisResult(
                        job_id=job_id,
                        analyzed_at="2026-11-06T00:00:00Z",
                        title=manifest.sources[0].original_filename if manifest.sources else "Corporate Training Course",
                        subject="Workplace Procedures & Safety Standards",
                        summary="Comprehensive operational training for site personnel and visitors.",
                    )

                target_dur = float(job.get("target_duration_seconds") or 120.0)
                plan = _generate_fallback_plan(
                    job_id,
                    analysis,
                    audience=job.get("audience", "workers"),
                    target_duration_seconds=target_dur,
                )
                script = _generate_fallback_script(job_id, analysis, plan)
                storyboard = _generate_fallback_storyboard(job_id, analysis, plan, script)
                duration = calculate_adaptive_duration(scenes=storyboard.scenes, script=script, plan=plan)
                quality = validate_director_artifacts(manifest, analysis, plan, script, storyboard)

                (out_dir / "analysis.json").write_text(analysis.model_dump_json(indent=2), encoding="utf-8")
                (out_dir / "plan.json").write_text(plan.model_dump_json(indent=2), encoding="utf-8")
                (out_dir / "script.json").write_text(script.model_dump_json(indent=2), encoding="utf-8")
                (out_dir / "storyboard.json").write_text(storyboard.model_dump_json(indent=2), encoding="utf-8")
                (out_dir / "duration.json").write_text(duration.model_dump_json(indent=2), encoding="utf-8")
                (out_dir / "quality.json").write_text(quality.model_dump_json(indent=2), encoding="utf-8")

        plan = EducationalPlan.model_validate(json.loads(plan_file.read_text(encoding="utf-8")))
        script = NarrationScript.model_validate(json.loads(script_file.read_text(encoding="utf-8")))
        storyboard = Storyboard.model_validate(json.loads(storyboard_file.read_text(encoding="utf-8")))

        # Multi-module production guarantee:
        # If the storyboard has only 1 scene but the plan has multiple lesson modules (e.g. 5S or Driver training),
        # regenerate per-module scenes using the robust fallback storyboarder so every lesson has its own animated scene.
        if len(storyboard.scenes) < len(plan.modules) and len(plan.modules) > 1:
            logger.info("Job %s has %d modules but only %d scenes. Aligning scenes to modules.", job_id, len(plan.modules), len(storyboard.scenes))
            analysis_file = out_dir / "analysis.json"
            if analysis_file.exists():
                an_data = AIAnalysisResult.model_validate(json.loads(analysis_file.read_text(encoding="utf-8")))
            else:
                an_data = AIAnalysisResult(job_id=job_id, title=plan.modules[0].title, subject="Training", summary="Training")
            # Ensure script has segments for all modules
            if len(script.segments) < len(plan.modules):
                script = _generate_fallback_script(job_id, an_data, plan)
                (out_dir / "script.json").write_text(script.model_dump_json(indent=2), encoding="utf-8")
            storyboard = _generate_fallback_storyboard(job_id, an_data, plan, script)
            (out_dir / "storyboard.json").write_text(storyboard.model_dump_json(indent=2), encoding="utf-8")

        if not storyboard.scenes:
            raise ValueError(f"Storyboard for job '{job_id}' contains no scenes.")

        # ── Step 3: Audio Narration & Speech Synthesis ────────────────────────────
        update_job_status(job_id, status="processing", stage="Generating professional narration voiceover (TTS)...")

        tts_provider = get_tts_provider(TTS_PROVIDER)
        audio_coordinator = AudioNarrationCoordinator(provider=tts_provider)

        scene_texts: list[tuple[str, str]] = []
        for idx, scene in enumerate(storyboard.scenes):
            matched_text = scene.narration_text
            if idx < len(script.segments) and len(script.segments[idx].narration_text) > len(matched_text):
                matched_text = script.segments[idx].narration_text
            scene_texts.append((scene.scene_id, matched_text))

        audio_res = await audio_coordinator.synthesize_scenes(
            scene_texts=scene_texts,
            output_dir=renders_path,
            voice=DEFAULT_TTS_VOICE,
        )

        # ── Step 4: Resolution & Video Parameters (Strict 16:9 Landscape) ────────
        aspect_ratio = job.get("aspect_ratio", "16:9")
        if aspect_ratio not in ("16:9", "landscape"):
            aspect_ratio = "16:9"  # Enforce landscape per directive

        quality_profile = "preview" if job.get("animation_style") == "preview" else "production"
        target_w, target_h = get_dimensions_for_aspect_ratio("16:9", quality_profile)
        target_fps = 15 if quality_profile == "preview" else 30

        # Subtitles strictly disabled (zero burned subtitles)
        burn_subtitles = False

        # ── Step 5: Scene Animation Rendering ─────────────────────────────────────
        scene_clip_paths: list[Path] = []
        engines_used: list[RenderEngineType] = []

        total_scenes = len(storyboard.scenes)
        for idx, scene in enumerate(storyboard.scenes):
            if audio_res.scene_audio_paths and idx < len(audio_res.scene_audio_paths):
                audio_path = Path(audio_res.scene_audio_paths[idx])
                audio_info = probe_video(audio_path)
                scene_audio_dur = audio_info.get("duration_seconds", 5.0)
            elif audio_res.scene_audio_paths:
                audio_path = Path(audio_res.scene_audio_paths[-1])
                audio_info = probe_video(audio_path)
                scene_audio_dur = audio_info.get("duration_seconds", 5.0)
            else:
                scene_audio_dur = 5.0

            target_scene_dur = max(2.5, scene_audio_dur + 0.5)

            spec = EngineNeutralSceneSpec.from_storyboard_scene(
                scene=scene,
                duration_seconds=target_scene_dur,
                target_width=target_w,
                target_height=target_h,
                fps=target_fps,
                aspect_ratio="16:9",
                lesson_number=idx + 1,
                total_lessons=total_scenes,
            )

            primary_engine = default_registry.select_engine(spec)
            update_job_status(
                job_id,
                status="processing",
                stage=f"Rendering 2D animation scene {idx + 1}/{total_scenes}: {spec.title[:30]}...",
            )

            scene_output_path = renders_path / f"scene_{idx + 1}_{scene.scene_id}.mp4"
            render_result = await asyncio.to_thread(default_registry.render_scene, spec, scene_output_path)

            scene_clip_paths.append(Path(render_result.video_path))
            engines_used.append(render_result.engine_used)

        if not scene_clip_paths:
            raise RuntimeError(f"Rendering produced no video scenes for job '{job_id}'.")

        # ── Step 6: Generate Individual Lesson Videos ────────────────────────────
        update_job_status(job_id, status="processing", stage="Composing individual lesson training videos...")

        # Match scenes to each module/lesson in the plan
        for mod_idx, mod in enumerate(plan.modules):
            mod_scenes_indices = [
                i for i, sc in enumerate(storyboard.scenes)
                if sc.module_id == mod.module_id
            ]
            # Fallback if module_id didn't align exactly
            if not mod_scenes_indices:
                per_mod = max(1, total_scenes // max(1, len(plan.modules)))
                s_start = mod_idx * per_mod
                s_end = (mod_idx + 1) * per_mod if mod_idx < len(plan.modules) - 1 else total_scenes
                mod_scenes_indices = list(range(s_start, min(s_end, total_scenes)))

            if not mod_scenes_indices:
                mod_scenes_indices = [max(0, min(mod_idx, len(scene_clip_paths) - 1))]
            else:
                mod_scenes_indices = [max(0, min(i, len(scene_clip_paths) - 1)) for i in mod_scenes_indices]

            mod_clip_paths = [scene_clip_paths[i] for i in mod_scenes_indices]
            if audio_res.scene_audio_paths:
                mod_audio_paths = [
                    Path(audio_res.scene_audio_paths[max(0, min(i, len(audio_res.scene_audio_paths) - 1))])
                    for i in mod_scenes_indices
                ]
            else:
                mod_audio_paths = [Path(audio_res.audio_path)] if audio_res.audio_path else []

            # Concatenate visual scenes for this lesson
            lesson_visual_mp4 = renders_path / f"raw_visual_lesson_{mod_idx + 1}.mp4"
            await asyncio.to_thread(concatenate_clips, mod_clip_paths, lesson_visual_mp4)

            # Concatenate audio clips for this lesson
            lesson_audio_path = renders_path / f"audio_lesson_{mod_idx + 1}.aac"
            if mod_audio_paths:
                await asyncio.to_thread(concatenate_audio_clips, mod_audio_paths, lesson_audio_path)
            else:
                lesson_audio_path = None

            # Compose finalized lesson video with audio (zero subtitles)
            lesson_final_mp4 = renders_path / f"lesson_{mod.module_id}.mp4"
            await asyncio.to_thread(
                compose_final_video,
                video_path=lesson_visual_mp4,
                output_path=lesson_final_mp4,
                audio_path=lesson_audio_path,
                burn_subtitles=False,
                quality_profile=quality_profile,
                target_width=target_w,
                target_height=target_h,
            )

            mod.output_video_path = str(lesson_final_mp4.resolve())
            mod.render_status = "completed"
            logger.info("Lesson %s (%s) rendered successfully: %s", mod.module_id, mod.title, lesson_final_mp4.name)

        # Persist updated educational plan with lesson video paths
        (out_dir / "plan.json").write_text(plan.model_dump_json(indent=2), encoding="utf-8")

        # ── Step 7: Compose Master Combined Course MP4 ───────────────────────────
        update_job_status(job_id, status="processing", stage="Composing master combined corporate training course...")

        raw_concatenated_video = renders_path / "concatenated_visuals.mp4"
        await asyncio.to_thread(concatenate_clips, scene_clip_paths, raw_concatenated_video)

        final_mp4 = final_video_path(job_id)

        await asyncio.to_thread(
            compose_final_video,
            video_path=raw_concatenated_video,
            output_path=final_mp4,
            audio_path=Path(audio_res.audio_path),
            burn_subtitles=False,
            quality_profile=quality_profile,
            target_width=target_w,
            target_height=target_h,
        )

        # ── Step 8: Validate Final Master Video ──────────────────────────────────
        update_job_status(job_id, status="processing", stage="Validating completed course video...")

        final_info = probe_video(final_mp4)
        if final_info["file_size_bytes"] == 0 or final_info["duration_seconds"] <= 0:
            raise RuntimeError(f"Rendered course video validation failed: {final_mp4} has 0 bytes or zero duration.")

        final_result = FinalVideoResult(
            job_id=job_id,
            video_path=str(final_mp4.resolve()),
            subtitles_srt_path="",
            subtitles_vtt_path="",
            narration_audio_path=audio_res.audio_path,
            total_duration_seconds=final_info["duration_seconds"],
            scene_count=total_scenes,
            width=final_info["width"],
            height=final_info["height"],
            fps=int(round(final_info["fps"])),
            file_size_bytes=final_info["file_size_bytes"],
            engines_used=engines_used,
            has_audio=final_info["has_audio"],
            has_subtitles=False,
        )

        # Persist completion in database
        dur_str = f"{int(final_info['duration_seconds'] // 60)}m {int(final_info['duration_seconds'] % 60)}s"
        update_job_status(
            job_id,
            status="completed",
            stage=f"Corporate training course ready ({len(plan.modules)} lessons, {dur_str}, 1080p landscape).",
            output_path=str(final_mp4.resolve()),
        )

        logger.info(
            "Corporate video production completed for job %s: %s (%.1fs, %d bytes)",
            job_id,
            final_mp4.name,
            final_info["duration_seconds"],
            final_info["file_size_bytes"],
        )
        return final_result

    except Exception as exc:
        logger.exception("Corporate video production pipeline failed for job %s: %s", job_id, exc)
        update_job_status(job_id, status="failed", stage="Training video rendering failed", error=str(exc))
        raise
