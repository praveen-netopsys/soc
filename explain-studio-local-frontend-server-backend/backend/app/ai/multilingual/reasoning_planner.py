"""Reasoning and Curriculum Planner powered by Qwen2.5:14B.

Performs:
1. Holistic presentation concept analysis
2. Concept-based module partitioning (Phase 1)
3. Per-slide teaching explanation generation (Phase 2 — dedicated pass)
4. Strict slide allocation validation & repair (zero gaps, zero duplicates)
5. Smooth fallback to high-quality deterministic engine when Ollama is offline
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from ... import config
from ..client import OllamaClient
from ..gpu import gpu_manager
from ..analyzer import clean_and_parse_json
from .glossary import build_course_glossary
from .models import CourseModule, MultilingualCourse, SlideExplanation, SUPPORTED_LANGUAGES
from .planner import (
    _chunk_slides_contiguously,
    _build_slide_teaching_explanation,
    sanitize_explanation_text,
    validate_slide_allocation,
)
from .translator import generate_multilingual_explanations
from .visual_analyzer import SlideVisualAnalysis

logger = logging.getLogger("explain_studio.ai.multilingual.reasoning_planner")


def repair_module_slide_assignments(
    total_slides: int,
    raw_modules: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Ensure the LLM-generated modules strictly satisfy:
    1. Union of all modules == {1..total_slides}
    2. Intersection of distinct modules == empty set
    3. Every slide appears exactly once
    If any violation exists, repairs the partitions contiguously.
    """
    if not raw_modules:
        chunks = _chunk_slides_contiguously(total_slides)
        return [
            {
                "module_number": idx,
                "title": f"Module {idx}: Core Operational Concepts",
                "description": f"Covers operational standards for slides {chunk[0]} to {chunk[-1]}.",
                "learning_objectives": [f"Understand procedures on slides {chunk[0]} to {chunk[-1]}"],
                "slide_numbers": chunk,
            }
            for idx, chunk in enumerate(chunks, 1)
        ]

    # Validate raw module slide assignments
    expected_set = set(range(1, total_slides + 1))
    seen_slides: set[int] = set()
    cleaned_modules: list[dict[str, Any]] = []

    for m in raw_modules:
        m_num = m.get("module_number") or len(cleaned_modules) + 1
        m_title = m.get("title") or f"Module {m_num}"
        m_desc = m.get("description") or f"Comprehensive guide for module {m_num}."
        m_objs = m.get("learning_objectives") or [f"Master concepts in module {m_num}"]

        raw_slides = m.get("slide_numbers", [])
        valid_slides = []
        for s in raw_slides:
            if isinstance(s, int) and 1 <= s <= total_slides and s not in seen_slides:
                valid_slides.append(s)
                seen_slides.add(s)

        if valid_slides:
            cleaned_modules.append({
                "module_number": m_num,
                "title": m_title,
                "description": m_desc,
                "learning_objectives": m_objs,
                "slide_numbers": sorted(valid_slides),
            })

    # If any slides were skipped or duplicate-dropped, assign them to the nearest or final module
    missing_slides = sorted(expected_set - seen_slides)
    if missing_slides:
        if cleaned_modules:
            cleaned_modules[-1]["slide_numbers"].extend(missing_slides)
            cleaned_modules[-1]["slide_numbers"].sort()
        else:
            chunks = _chunk_slides_contiguously(total_slides)
            return [
                {
                    "module_number": idx,
                    "title": f"Module {idx}: Core Concepts",
                    "description": f"Covers slides {chunk[0]} through {chunk[-1]}.",
                    "learning_objectives": [f"Master slides {chunk[0]}–{chunk[-1]}"],
                    "slide_numbers": chunk,
                }
                for idx, chunk in enumerate(chunks, 1)
            ]

    return cleaned_modules


def _build_explanation_prompt(
    presentation_title: str,
    slides_info: dict[int, dict[str, Any]],
    visual_analyses: dict[int, SlideVisualAnalysis],
) -> str:
    """Build a focused, information-rich per-slide explanation prompt."""
    total_slides = max(slides_info.keys()) if slides_info else 1

    lines = [
        f"You are an expert instructor delivering spoken teaching narration for a {total_slides}-slide training presentation titled '{presentation_title}'.",
        "",
        "For EACH slide below, write a natural, substantive spoken explanation a knowledgeable human instructor would give.",
        "",
        "STRICT RULES:",
        "- NEVER say 'Slide 1', 'In this slide', 'This slide shows', 'The title is', 'Moving to the next slide'.",
        "- Begin directly with the subject matter — not with a generic opener.",
        "- Do NOT use the same opening sentence structure for every slide.",
        "- Explain what the content MEANS and why it matters, not just what it SAYS.",
        "- For numerical values, explain why they are set at that level and what happens if violated.",
        "- For tables: describe what each column/row means and the key relationships.",
        "- For process flows or diagrams: describe the sequence and what each step means in practice.",
        "- Do NOT invent facts. Base explanations only on the provided content.",
        "- Do NOT use these repetitive phrases: 'It is important to remember that', 'Building on these principles', 'As we can see', 'Let us understand'.",
        "- Each explanation should be 3–6 natural spoken sentences.",
        "- Incomplete bullet points (e.g. 'by our staff', 'of your visit') are sentence fragments — do not use them verbatim.",
        "",
        "SLIDES:",
    ]

    for sn in sorted(slides_info.keys()):
        s_data = slides_info[sn]
        vis = visual_analyses.get(sn)
        title_str = s_data.get("title", "")
        all_bullets = s_data.get("bullets", [])
        tables = s_data.get("tables", [])
        notes = s_data.get("notes", [])

        entry = [f"--- Slide {sn} ---"]
        if title_str:
            entry.append(f"Title: {title_str}")
        if all_bullets:
            for b in all_bullets:
                entry.append(f"  • {b}")
        if tables:
            # Include full table so LLM can interpret rows/columns properly
            entry.append(f"  Table:\n{tables[0][:400]}")
        if notes:
            entry.append(f"  Speaker notes: {notes[0][:200]}")
        if vis and vis.has_visual_elements:
            if vis.visual_summary:
                entry.append(f"  Visual: {vis.visual_summary}")
            if vis.charts_and_data:
                entry.append(f"  Chart data: {'; '.join(vis.charts_and_data[:4])}")
            if vis.process_flow:
                entry.append(f"  Process flow steps: {'; '.join(vis.process_flow[:4])}")
            if vis.unextracted_text:
                entry.append(f"  Additional text in image: {'; '.join(vis.unextracted_text[:3])}")
        lines.extend(entry)

    lines.extend([
        "",
        "Return ONLY valid JSON:",
        '{"slide_explanations": {"1": "explanation for slide 1 ...", "2": "explanation ...", ...}}',
        "Include every slide number. No extra keys.",
    ])
    return "\n".join(lines)


def _validate_llm_explanation(text: str, min_words: int = 20) -> bool:
    """Check that an LLM-generated explanation meets minimum quality standards."""
    if not text or not text.strip():
        return False
    words = text.strip().split()
    if len(words) < min_words:
        return False
    # Reject if it still starts with the exact bad phrases
    bad_starts = [
        "building on these principles",
        "it is important to remember that",
        "in this slide",
        "slide ",
        "this slide",
        "the title of this slide",
    ]
    low = text.strip().lower()
    for bad in bad_starts:
        if low.startswith(bad):
            return False
    return True


async def plan_curriculum_with_reasoning(
    job_id: str,
    presentation_title: str,
    slides_info: dict[int, dict[str, Any]],
    visual_analyses: dict[int, SlideVisualAnalysis],
    target_languages: list[str] | None = None,
    client: OllamaClient | None = None,
) -> MultilingualCourse:
    """
    Two-phase LLM curriculum planning:
      Phase 1 — Module grouping (fast, structural).
      Phase 2 — Per-slide explanation generation (quality, full content).
    Falls back gracefully to deterministic engine if Ollama is unavailable or times out.
    """
    total_slides = max(slides_info.keys()) if slides_info else 1
    langs = target_languages or list(SUPPORTED_LANGUAGES.keys())

    # Build Technical Course Glossary
    all_texts: list[str] = []
    for s in slides_info.values():
        all_texts.append(s.get("title", ""))
        all_texts.extend(s.get("bullets", []))
    glossary = build_course_glossary(all_texts)

    ollama = client or OllamaClient()
    conn_check = ollama.check_connection()

    raw_modules_data: list[dict[str, Any]] = []
    llm_explanations: dict[int, str] = {}
    module_phase_ok = False

    if conn_check["connected"]:
        logger.info(
            "Ollama connected at %s. Running two-phase curriculum planning for job %s",
            ollama.base_url, job_id,
        )

        # ── Phase 1: Module Structure Planning ────────────────────────────────
        slide_summaries: list[str] = []
        for sn in sorted(slides_info.keys()):
            s_data = slides_info[sn]
            vis = visual_analyses.get(sn)
            vis_note = (
                f"\n   Visual: {vis.visual_summary}"
                if (vis and vis.visual_summary and vis.has_visual_elements)
                else ""
            )
            all_bullets = s_data.get("bullets", [])
            b_text = "; ".join(all_bullets) if all_bullets else "(text slide)"
            slide_summaries.append(
                f"Slide {sn}: [{s_data.get('title', 'Untitled')}]{vis_note}\n   Content: {b_text}"
            )

        module_prompt = (
            f"You are an expert curriculum designer analyzing a {total_slides}-slide presentation titled '{presentation_title}'.\n\n"
            "SLIDES:\n" + "\n".join(slide_summaries) + "\n\n"
            "Task: Group these slides into 3–5 logical, concept-based learning modules.\n"
            "Rules:\n"
            f"- Every slide from 1 to {total_slides} must appear in exactly one module. No gaps, no duplicates.\n"
            "- Module titles should describe the concept being taught, not generic labels.\n"
            "- Slides covering related topics should be grouped together.\n"
            "Return ONLY valid JSON:\n"
            '{\n  "modules": [\n    {\n      "module_number": 1,\n      "title": "Module 1: Descriptive Title",\n'
            '      "description": "What this module teaches.",\n      "learning_objectives": ["Objective 1"],\n'
            '      "slide_numbers": [1, 2, 3]\n    }\n  ]\n}'
        )

        try:
            with gpu_manager.guard(job_id, config.OLLAMA_TEXT_MODEL, "module_planning", timeout=15.0):
                resp_m = ollama.chat(
                    model=config.OLLAMA_TEXT_MODEL,
                    messages=[{"role": "user", "content": module_prompt}],
                    format="json",
                    timeout=min(60.0, config.OLLAMA_INFERENCE_TIMEOUT_SECONDS),
                )
                parsed_m = clean_and_parse_json(resp_m["content"])
                if parsed_m.get("modules") and isinstance(parsed_m["modules"], list):
                    raw_modules_data = parsed_m["modules"]
                    module_phase_ok = True
                    logger.info("Phase 1 complete: %d modules planned.", len(raw_modules_data))
        except Exception as exc:
            logger.warning("Phase 1 module planning failed (%s); will use deterministic grouping.", exc)

        # ── Phase 2: Per-Slide Teaching Explanation Generation ────────────────
        explanation_prompt = _build_explanation_prompt(
            presentation_title, slides_info, visual_analyses
        )

        try:
            with gpu_manager.guard(job_id, config.OLLAMA_TEXT_MODEL, "explanation_generation", timeout=20.0):
                resp_e = ollama.chat(
                    model=config.OLLAMA_TEXT_MODEL,
                    messages=[{"role": "user", "content": explanation_prompt}],
                    format="json",
                    timeout=min(150.0, config.OLLAMA_INFERENCE_TIMEOUT_SECONDS),
                )
                parsed_e = clean_and_parse_json(resp_e["content"])
                raw_exps = parsed_e.get("slide_explanations", {})
                success_count = 0
                for k, v in raw_exps.items():
                    try:
                        s_idx = int(k)
                        if isinstance(v, str):
                            cleaned = sanitize_explanation_text(v.strip())
                            if _validate_llm_explanation(cleaned):
                                llm_explanations[s_idx] = cleaned
                                success_count += 1
                    except ValueError:
                        pass
                logger.info(
                    "Phase 2 complete: %d/%d slide explanations generated by LLM.",
                    success_count, total_slides,
                )
                # Mark overall success if module phase worked or explanations were returned
                if not module_phase_ok and success_count > 0:
                    module_phase_ok = True  # Explanations but no modules is fine — deterministic grouping handles it
        except Exception as exc:
            logger.warning(
                "Phase 2 explanation generation failed (%s); deterministic fallback will fill gaps.",
                exc,
            )

    # ── Enforce strict slide allocation ───────────────────────────────────────
    repaired_modules_spec = repair_module_slide_assignments(
        total_slides,
        raw_modules_data if module_phase_ok else [],
    )

    # ── Assemble modules with best-available explanations ────────────────────
    modules: list[CourseModule] = []

    llm_gaps = [sn for sn in range(1, total_slides + 1) if sn not in llm_explanations]
    if llm_gaps:
        logger.info(
            "Deterministic fallback will generate explanations for %d slide(s): %s",
            len(llm_gaps), llm_gaps,
        )

    for m_data in repaired_modules_spec:
        m_num = m_data["module_number"]
        mod_id = f"module_{m_num:02d}"
        s_nums = m_data["slide_numbers"]
        raw_mod_title = m_data.get("title", f"Module {m_num}")
        mod_title = raw_mod_title if raw_mod_title.startswith("Module") else f"Module {m_num}: {raw_mod_title}"

        slide_exps: list[SlideExplanation] = []
        for sn in s_nums:
            s_det = slides_info.get(sn, {
                "slide_number": sn,
                "title": f"Slide {sn}",
                "bullets": [],
                "tables": [],
                "images": [],
                "notes": [],
            })

            # Use LLM explanation if available and valid; otherwise use improved deterministic engine
            eng_exp = llm_explanations.get(sn)
            if not eng_exp:
                eng_exp = _build_slide_teaching_explanation(s_det, sn, total_slides)
            eng_exp = sanitize_explanation_text(eng_exp)

            # Generate all 6 language versions
            multilingual_exps = generate_multilingual_explanations(
                english_explanation=eng_exp,
                target_languages=langs,
                glossary=glossary,
                slide_number=sn,
            )

            slide_exps.append(
                SlideExplanation(
                    slide_number=sn,
                    slide_id=f"slide_{sn}",
                    module_id=mod_id,
                    title=s_det.get("title") or f"Slide {sn}",
                    key_points=s_det.get("bullets", [])[:4],
                    table_summaries=s_det.get("tables", [])[:1],
                    image_descriptions=s_det.get("images", [])[:2],
                    slide_image_path=f"slides/slide_{sn}.png",
                    explanations=multilingual_exps,
                    durations_seconds={},
                    audio_files={},
                )
            )

        modules.append(
            CourseModule(
                module_id=mod_id,
                module_number=m_num,
                title=mod_title,
                description=m_data.get(
                    "description",
                    f"Covers operational standards for slides {s_nums[0]} to {s_nums[-1]}.",
                ),
                learning_objectives=m_data.get(
                    "learning_objectives",
                    [f"Master all procedures specified in slides {s_nums[0]} to {s_nums[-1]}."],
                ),
                slide_numbers=s_nums,
                slides=slide_exps,
                durations_seconds={},
                video_files={},
            )
        )

    # Final Strict Uniqueness Validation
    validation = validate_slide_allocation(total_slides, modules)
    if not validation["valid"]:
        logger.error("Critical slide uniqueness violation after repair: %s", validation)
    else:
        logger.info(
            "Slide uniqueness verified: 100%% complete, strictly unique and disjoint across %d modules.",
            len(modules),
        )

    course = MultilingualCourse(
        job_id=job_id,
        title=presentation_title,
        total_slides=total_slides,
        total_modules=len(modules),
        languages=langs,
        modules=modules,
        glossary=glossary,
        course_video_files={},
        validation_report=validation,
    )
    return course
