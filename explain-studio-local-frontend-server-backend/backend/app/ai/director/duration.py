"""Adaptive Duration Planner for Explain Studio.

Estimates realistic video duration based on:
- Narration word count and natural educational speaking rate (125-145 WPM based on audience).
- Real measured TTS audio duration override when available.
- Cognitive pauses for complex concepts and technical definitions.
- Visual reading dwell time not already accounted for during concurrent speech (no double-counting).
- Scene transition durations.
- Distinguishes scene duration, chapter/module duration, visual demo duration, and total runtime.
- Explicitly reconciles teaching-plan module durations with overall video runtime.
"""
from __future__ import annotations

import logging
from typing import Any

from ..models import (
    DurationEstimate,
    EducationalPlan,
    ModuleDurationReconciliation,
    NarrationScript,
    SceneDurationBreakdown,
    Storyboard,
    StoryboardScene,
)

logger = logging.getLogger("explain_studio.ai.director.duration")

DEFAULT_WORDS_PER_MINUTE: float = 135.0


def format_duration_string(seconds: float) -> str:
    """Format total seconds into human-readable educational video runtime."""
    total_sec = max(0, int(round(seconds)))
    m = total_sec // 60
    s = total_sec % 60
    if m > 0:
        return f"{m} min {s} sec"
    return f"{s} sec"


def calculate_adaptive_duration(
    scenes: list[StoryboardScene],
    script: NarrationScript | None = None,
    plan: EducationalPlan | None = None,
    wpm: float = DEFAULT_WORDS_PER_MINUTE,
    audience_level: str = "intermediate",
    measured_audio_duration_seconds: float | None = None,
) -> DurationEstimate:
    """
    Compute mathematically consistent adaptive video duration from script, storyboard, and audience level.

    Formula:
      narration_duration = (total_words / speaking_rate) * 60.0
      estimated_runtime  = narration_duration + cognitive_pauses + reading_time_not_already_accounted_for + transition_duration

    When actual TTS audio exists later, measured_audio_duration_seconds overrides estimated narration duration.
    """
    # 1. Speaking rate calibrated by audience level
    aud = audience_level.lower()
    if aud == "beginner":
        speaking_rate = 125.0
        pause_factor = 1.2
    elif aud == "advanced":
        speaking_rate = 145.0
        pause_factor = 0.9
    else:
        speaking_rate = 135.0 if wpm == DEFAULT_WORDS_PER_MINUTE else wpm
        pause_factor = 1.0

    # 2. Total word count from authoritative narration script if provided
    if script is not None and script.segments:
        total_words = script.total_word_count or sum(
            len(seg.narration_text.split()) for seg in script.segments
        )
    else:
        total_words = sum(len(sc.narration_text.split()) for sc in scenes)

    # 3. Overall narration duration (or measured TTS audio override)
    if measured_audio_duration_seconds is not None and measured_audio_duration_seconds > 0.0:
        overall_narration_duration = round(float(measured_audio_duration_seconds), 2)
    else:
        overall_narration_duration = round((total_words / speaking_rate) * 60.0, 2)

    # Map scenes to corresponding script segments if available
    script_segments_by_scene: dict[str, Any] = {}
    if script is not None:
        for idx, seg in enumerate(script.segments):
            script_segments_by_scene[seg.scene_id] = seg
            # Fallback by index
            script_segments_by_scene[f"idx_{idx}"] = seg

    # 4. Compute scene-by-scene duration breakdowns
    scene_breakdowns: list[SceneDurationBreakdown] = []
    total_cognitive_pauses = 0.0
    total_reading_extra = 0.0
    total_transitions = 0.0
    total_visual_demos = 0.0

    for idx, sc in enumerate(scenes):
        matched_seg = script_segments_by_scene.get(sc.scene_id) or script_segments_by_scene.get(f"idx_{idx}")

        # Scene word count
        if matched_seg is not None and matched_seg.narration_text:
            sc_words = len(matched_seg.narration_text.split())
        else:
            sc_words = len(sc.narration_text.split())

        # Scene narration speech time
        sc_narration_dur = round((sc_words / speaking_rate) * 60.0, 2)

        # Visual demonstration duration (procedural demo, graph build, code animation)
        visual_demo_dur = round(float(sc.estimated_visual_duration or 0.0), 2)
        total_visual_demos += visual_demo_dur

        # Cognitive pause based on scene pedagogical purpose
        purpose = (sc.scene_purpose or "").lower()
        if purpose in {"mechanism_breakdown", "concept_introduction"}:
            base_pause = 2.0
        elif purpose in {"summary_recap", "hook"}:
            base_pause = 1.0
        else:
            base_pause = 1.5
        sc_pause = round(base_pause * pause_factor, 2)
        total_cognitive_pauses += sc_pause

        # Reading time for on-screen text not already absorbed during speech (no double-counting)
        # While the narrator speaks, the viewer is already reading along concurrently.
        # Extra dwell time is only allocated for dense mathematical formulas, code lines, or labels.
        has_formulas = any(
            any(sym in txt for sym in ("=", "^", "\\", "+", "/", "->", "{", "}"))
            for txt in sc.on_screen_text
        )
        ost_words = sum(len(txt.split()) for txt in sc.on_screen_text)
        # If formula-dense or heavy label count, give 1.5s extra dwell time; otherwise 0.0s (absorbed)
        extra_reading_dwell = 1.5 * pause_factor if has_formulas or ost_words > 12 else 0.0
        extra_reading_dwell = round(extra_reading_dwell, 2)
        total_reading_extra += extra_reading_dwell

        # Transition duration (fade, slide, dissolve)
        trans_dur = 0.75
        total_transitions += trans_dur

        # Total scene duration: max of speech or visual demo + intentional pauses + transition
        content_duration = max(sc_narration_dur, visual_demo_dur)
        scene_total = round(content_duration + sc_pause + extra_reading_dwell + trans_dur, 2)

        scene_breakdowns.append(
            SceneDurationBreakdown(
                scene_id=sc.scene_id,
                narration_duration_seconds=sc_narration_dur,
                visual_demonstration_duration_seconds=visual_demo_dur,
                cognitive_pause_seconds=sc_pause,
                reading_time_seconds=extra_reading_dwell,
                transition_duration_seconds=trans_dur,
                total_scene_duration_seconds=scene_total,
            )
        )

    # 5. Compute mathematically exact total runtime
    total_estimated_runtime = round(sum(b.total_scene_duration_seconds for b in scene_breakdowns), 1)

    # 6. Reconcile teaching-plan module durations with actual video runtime
    module_reconciliations: list[ModuleDurationReconciliation] = []
    if plan is not None and plan.modules:
        # Group scenes by module_id
        module_scene_durations: dict[str, float] = {}
        for b in scene_breakdowns:
            # Find scene's module
            matching_sc = next((s for s in scenes if s.scene_id == b.scene_id), None)
            m_id = matching_sc.module_id if matching_sc else "mod_1"
            module_scene_durations[m_id] = module_scene_durations.get(m_id, 0.0) + b.total_scene_duration_seconds

        for mod in plan.modules:
            planned = round(float(mod.estimated_duration_seconds or 0.0), 1)
            actual = round(module_scene_durations.get(mod.module_id, 0.0), 1)
            diff = round(actual - planned, 1)

            if abs(diff) <= max(15.0, planned * 0.15):
                note = f"Actual runtime ({actual}s) closely aligns with planned modular pace ({planned}s)."
            elif diff < 0:
                note = (
                    f"Direct narration delivers high-density instruction in {actual}s "
                    f"(planned: {planned}s). Remaining {abs(diff)}s buffer is available "
                    f"for pause-and-reflect activities or classroom discussion."
                )
            else:
                note = (
                    f"Detailed spoken explanation ({actual}s) expands on initial module "
                    f"estimate ({planned}s) to ensure complete concept coverage."
                )

            module_reconciliations.append(
                ModuleDurationReconciliation(
                    module_id=mod.module_id,
                    title=mod.title,
                    planned_module_duration_seconds=planned,
                    actual_calculated_duration_seconds=actual,
                    reconciliation_note=note,
                )
            )

    # Summary pacing note
    pacing_note = (
        f"Calculated at {int(speaking_rate)} WPM educational cadence: "
        f"{overall_narration_duration}s narration + {round(total_cognitive_pauses, 1)}s cognitive pauses + "
        f"{round(total_reading_extra, 1)}s formula dwell + {round(total_transitions, 1)}s transitions = "
        f"{total_estimated_runtime}s total runtime."
    )

    return DurationEstimate(
        total_duration_seconds=total_estimated_runtime,
        formatted_duration=format_duration_string(total_estimated_runtime),
        narration_duration_seconds=overall_narration_duration,
        visual_demonstration_duration_seconds=round(total_visual_demos, 1),
        cognitive_pause_seconds=round(total_cognitive_pauses, 1),
        reading_time_not_already_accounted_for_seconds=round(total_reading_extra, 1),
        transition_duration_seconds=round(total_transitions, 1),
        visual_reading_pause_seconds=round(total_cognitive_pauses + total_reading_extra, 1),
        transition_padding_seconds=round(total_transitions, 1),
        words_per_minute=speaking_rate,
        total_words=total_words,
        pacing_note=pacing_note,
        scene_breakdowns=scene_breakdowns,
        module_reconciliations=module_reconciliations,
        measured_audio_duration_override=measured_audio_duration_seconds,
    )
