"""Narration Script Generator for Explain Studio — Corporate Training Video Production.

Produces clear, spoken conversational English training narration divided into lesson segments,
thoroughly explaining every presentation slide assigned to each module.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from ..analyzer import clean_and_parse_json
from ..client import OllamaClient
from ..gpu import gpu_manager
from ..models import (
    AIAnalysisResult,
    EducationalPlan,
    NarrationScript,
    ScriptSegment,
    SourceReference,
)

logger = logging.getLogger("explain_studio.ai.director.scriptwriter")

SCRIPT_PROMPT_TEMPLATE = """You are an expert corporate safety trainer and instructional narrator.
Write a clear, direct, and engaging spoken training script in English based on the lesson plan and presentation slides.

TOPIC: {title}
AUDIENCE: {audience} (factory workers, machine operators, drivers, and site visitors)
TARGET TOTAL DURATION: {target_duration_seconds:.0f} seconds
TARGET TOTAL WORD COUNT: approximately {target_words} words total across all segments (spoken pace of ~135 words per minute).
SUMMARY: {summary}

LESSONS TO COVER (WITH ASSIGNED SLIDES):
{modules_text}

GROUNDED RULES & FACTS FROM SLIDES:
{facts_text}

PERMITTED CANONICAL BLOCK IDs:
{permitted_block_ids}

REQUIREMENTS:
- Spoken, clear, direct English. Avoid academic jargon and convoluted phrases.
- Thoroughly explain the procedures, rules, and requirements of EVERY assigned slide in each lesson.
- Emphasize practical actions: what workers must do, verify, watch out for, and wear.
- State safety limits, speed limits, and mandatory PPE clearly.
- Maintain a professional, encouraging, and authoritative safety tone.
- In source_references, use ONLY content_block_id values from the PERMITTED CANONICAL BLOCK IDs list, or leave empty if none apply.

Output ONLY valid JSON matching this schema:
{{
  "title": "{title}",
  "segments": [
    {{
      "segment_id": "seg_1",
      "scene_id": "scene_1",
      "module_id": "mod_1",
      "speaker_tone": "engaging | explanatory | deliberate | authoritative",
      "narration_text": "Spoken sentences verbatim for this lesson...",
      "source_references": []
    }}
  ]
}}
"""


def _generate_fallback_script(job_id: str, analysis: AIAnalysisResult, plan: EducationalPlan) -> NarrationScript:
    """Generate a slide-grounded corporate training narration script explaining all slides in each module."""
    from .planner import _get_slides_info
    all_slides = _get_slides_info(job_id, analysis)
    slides_by_num = {s["slide_number"]: s for s in all_slides}

    segments: list[ScriptSegment] = []
    wpm = 135.0

    # Build scene segments from modules and concepts
    for idx, mod in enumerate(plan.modules):
        scene_id = f"scene_{idx + 1}"
        seg_id = f"seg_{idx + 1}"

        # Retrieve slides belonging to this module
        mod_slides = [slides_by_num[n] for n in mod.slide_numbers if n in slides_by_num]

        sentences: list[str] = []
        if idx == 0:
            sentences.append(f"Welcome to this training course on {analysis.title}.")
            sentences.append(f"In this first lesson, we cover {mod.title}.")
        elif idx == len(plan.modules) - 1:
            sentences.append(f"In this concluding lesson, we cover {mod.title}.")
        else:
            sentences.append(f"Moving on to {mod.title}.")

        if mod.purpose:
            clean_purpose = mod.purpose.rstrip(".") + "."
            sentences.append(clean_purpose)

        # Articulate instructions from each assigned slide
        for s in mod_slides:
            s_title = s.get("title", "").strip()
            s_bullets = s.get("bullets", [])
            b_clean = [b.rstrip(".") for b in s_bullets if len(b) > 4 and not b.isdigit()]
            if s_title and len(s_title) > 4 and not s_title.isdigit():
                clean_title = re.sub(r'[:\-–]+$', '', s_title).strip()
                if b_clean:
                    sentences.append(f"Regarding {clean_title}: {'. '.join(b_clean[:2])}.")
                else:
                    sentences.append(f"Please observe all requirements for {clean_title}.")
            elif b_clean:
                sentences.append(f"{'. '.join(b_clean[:2])}.")

        if mod.important_facts:
            sentences.append(f"Key takeaway: {mod.important_facts[0]}.")

        if idx == len(plan.modules) - 1:
            sentences.append("Remember, everyone on site has full authority to stop work if an unsafe condition occurs. Report any hazards immediately to your supervisor.")
        else:
            sentences.append("Ensure you follow each safety check carefully before proceeding.")

        text = " ".join(sentences).replace("..", ".").replace("  ", " ").strip()
        words = len(text.split())
        duration = round((words / (wpm / 60.0)), 1)
        tone = "engaging" if idx == 0 else ("reflective" if idx == len(plan.modules) - 1 else "explanatory")

        # Attach concept source references matching this module
        refs: list[SourceReference] = []
        if analysis.main_concepts:
            for c in analysis.main_concepts:
                for r in c.source_references:
                    if r.slide_number in mod.slide_numbers and r not in refs:
                        refs.append(r)
        if not refs and analysis.main_concepts:
            concept_idx = min(idx, len(analysis.main_concepts) - 1)
            refs = analysis.main_concepts[concept_idx].source_references

        segments.append(
            ScriptSegment(
                segment_id=seg_id,
                scene_id=scene_id,
                module_id=mod.module_id,
                speaker_tone=tone,
                narration_text=text,
                word_count=words,
                estimated_duration_seconds=duration,
                source_references=refs,
            )
        )

    total_words = sum(s.word_count for s in segments)
    total_time = sum(s.estimated_duration_seconds for s in segments)

    return NarrationScript(
        job_id=job_id,
        title=analysis.title,
        target_audience=plan.audience_level,
        total_word_count=total_words,
        estimated_reading_time_seconds=total_time,
        segments=segments,
    )


def generate_narration_script(
    analysis: AIAnalysisResult,
    plan: EducationalPlan,
    client: OllamaClient,
    text_model: str,
    job_id: str,
    target_duration_seconds: float = 120.0,
) -> NarrationScript:
    """Generate conversational narration script using Qwen2.5:14B or fallback."""
    from .planner import _get_slides_info
    all_slides = _get_slides_info(job_id, analysis)
    slides_by_num = {s["slide_number"]: s for s in all_slides}

    target_words = max(50, int((target_duration_seconds / 60.0) * 135))
    modules_text_lines = []
    for m in plan.modules:
        m_slides = [slides_by_num[n] for n in m.slide_numbers if n in slides_by_num]
        slide_descs = []
        for s in m_slides:
            bullets_preview = "; ".join(s.get("bullets", [])[:3])
            s_title = s.get("title", "")
            if bullets_preview:
                slide_descs.append(f"Slide {s['slide_number']} '{s_title}' ({bullets_preview})")
            else:
                slide_descs.append(f"Slide {s['slide_number']} '{s_title}'")
        s_summary = " | ".join(slide_descs)
        modules_text_lines.append(f"- Lesson {m.module_id} ({m.title}): Covers [{s_summary}]. Purpose: {m.purpose}")
    modules_text = "\n".join(modules_text_lines)

    facts_text = "\n".join(
        f"- Fact: {f.statement} (Block: {f.source_references[0].content_block_id if f.source_references else 'N/A'})"
        for f in analysis.important_facts[:8]
    )

    permitted_ids: list[str] = []
    for c in analysis.main_concepts:
        for r in c.source_references:
            if r.content_block_id and r.content_block_id not in permitted_ids:
                permitted_ids.append(r.content_block_id)
    for f in analysis.important_facts:
        for r in f.source_references:
            if r.content_block_id and r.content_block_id not in permitted_ids:
                permitted_ids.append(r.content_block_id)
    permitted_str = ", ".join(permitted_ids) if permitted_ids else "None specified (leave source_references empty)"

    prompt = SCRIPT_PROMPT_TEMPLATE.format(
        title=analysis.title,
        audience=plan.audience_level,
        target_duration_seconds=target_duration_seconds,
        target_words=target_words,
        summary=analysis.summary[:300],
        modules_text=modules_text,
        facts_text=facts_text or "Follow all standard operating procedures strictly.",
        permitted_block_ids=permitted_str,
    )

    try:
        with gpu_manager.guard(job_id, text_model, "narration_scriptwriter"):
            logger.info("Executing narration scriptwriter with model %s for job %s", text_model, job_id)
            resp = client.chat(
                model=text_model,
                messages=[{"role": "user", "content": prompt}],
                format="json",
            )

        data = clean_and_parse_json(resp["content"])
        raw_segments = data.get("segments", [])
        segments: list[ScriptSegment] = []

        for idx, seg in enumerate(raw_segments):
            if isinstance(seg, dict):
                text = seg.get("narration_text", "")
                words = len(text.split())
                dur = round((words / (135.0 / 60.0)), 1) if words else 15.0

                raw_refs = seg.get("source_references", [])
                valid_refs: list[SourceReference] = []
                for r in raw_refs:
                    if isinstance(r, dict):
                        bid = r.get("content_block_id")
                        if bid in permitted_ids or not permitted_ids:
                            valid_refs.append(SourceReference.model_validate(r))

                mod_id = seg.get("module_id") or (plan.modules[idx].module_id if idx < len(plan.modules) else f"mod_{idx + 1}")
                segments.append(
                    ScriptSegment(
                        segment_id=seg.get("segment_id") or f"seg_{idx + 1}",
                        scene_id=seg.get("scene_id") or f"scene_{idx + 1}",
                        module_id=mod_id,
                        speaker_tone=seg.get("speaker_tone", "explanatory"),
                        narration_text=text,
                        word_count=words,
                        estimated_duration_seconds=dur,
                        source_references=valid_refs,
                    )
                )

        if not segments:
            return _generate_fallback_script(job_id, analysis, plan)

        total_words = sum(s.word_count for s in segments)
        total_time = sum(s.estimated_duration_seconds for s in segments)
        return NarrationScript(
            job_id=job_id,
            title=data.get("title") or analysis.title,
            target_audience=plan.audience_level,
            total_word_count=total_words,
            estimated_reading_time_seconds=total_time,
            segments=segments,
        )

    except Exception as exc:
        logger.warning("Failed to generate narration script via AI for job %s: %s; using deterministic fallback", job_id, exc)
        return _generate_fallback_script(job_id, analysis, plan)
