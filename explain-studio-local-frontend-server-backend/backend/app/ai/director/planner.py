"""Educational Planner for Explain Studio — Corporate Training Video Production.

Thoroughly reads presentation slides one-by-one from the extraction manifest,
chunks them sequentially into 3 to 6 logical operational lessons, and ensures
100% of the presentation slides and facts are covered without gaps or omissions.
"""
from __future__ import annotations

import json
import logging
import math
import re
from pathlib import Path
from typing import Any

from ...storage import extracted_dir
from ..analyzer import clean_and_parse_json
from ..client import OllamaClient
from ..gpu import gpu_manager
from ..models import AIAnalysisResult, EducationalPlan, TeachingModule

logger = logging.getLogger("explain_studio.ai.director.planner")

PLANNER_PROMPT_TEMPLATE = """You are an expert corporate training director and curriculum architect.
Your job is to divide a PowerPoint presentation into 3 to 6 practical training lessons for factory workers, operators, and site visitors.

AUDIENCE: Factory workers, operators, and site visitors (straightforward, practical, clear language).
TARGET TOTAL DURATION: {target_duration_seconds:.0f} seconds total across all lessons.

DOCUMENT DETAILS:
Title: {title}
Subject: {subject}
Summary: {summary}
Main Concepts: {concepts}
Important Facts: {facts}

PRESENTATION SLIDES (SEQUENTIAL ORDER):
{slides_text}

INSTRUCTIONS:
1. Divide ALL slides sequentially from 1 to {total_slides} into 3 to 6 contiguous lessons.
2. Every slide must belong to exactly one lesson (start_slide to end_slide). NO SLIDES MAY BE SKIPPED.
3. Every lesson must follow strict numerical order (Lesson 1 covers early slides, subsequent lessons cover subsequent slides).
4. Derive ALL lesson titles, concepts, and facts strictly from the provided presentation slides for this job.
5. Focus each lesson on clear rules, safety standards, correct actions, and practical procedures from those specific slides.
6. Language must be direct, simple, and action-oriented.
7. Set estimated_duration_seconds for each lesson such that their SUM is approximately {target_duration_seconds:.0f} seconds.

Output ONLY valid JSON matching this schema (do NOT copy words from the example placeholder, derive everything from the slides above):
{{
  "pedagogical_approach": "Practical step-by-step corporate training for workplace procedures and compliance",
  "pacing_strategy": "Clear, steady pacing with deliberate visual demonstrations of safe procedures",
  "modules": [
    {{
      "module_id": "mod_1",
      "title": "Lesson 1: <Concise Title of First Topic from Slides>",
      "purpose": "<Clear practical operational purpose for these slides>",
      "learning_objective": "<Specific compliance or execution action workers will learn>",
      "start_slide": 1,
      "end_slide": 3,
      "slide_numbers": [1, 2, 3],
      "target_concepts": ["<Concept 1 from slides>", "<Concept 2 from slides>"],
      "important_facts": ["<Key rule or fact from slides>"],
      "prerequisite_bridges": ["Basic operational introduction"],
      "key_analogies": ["<Practical workplace analogy>"],
      "depth_level": "introductory",
      "estimated_duration_seconds": 30.0
    }}
  ]
}}
"""


def _get_slides_info(job_id: str, analysis: AIAnalysisResult) -> list[dict[str, Any]]:
    """Extract ordered, comprehensive slide summaries from extraction manifest or analysis citations."""
    slides_map: dict[int, dict[str, Any]] = {}

    if job_id:
        manifest_path = extracted_dir(job_id) / "manifest.json"
        if manifest_path.exists():
            try:
                mdata = json.loads(manifest_path.read_text(encoding="utf-8"))
                for b in mdata.get("blocks", []):
                    s_num = b.get("slide_number")
                    if s_num is None or not isinstance(s_num, int) or s_num <= 0:
                        continue
                    if s_num not in slides_map:
                        slides_map[s_num] = {
                            "slide_number": s_num,
                            "title": "",
                            "headings": [],
                            "bullets": [],
                            "images": [],
                            "texts": [],
                        }

                    txt = (b.get("text") or "").strip()
                    # Sanitize soft breaks, vertical tabs, and control characters
                    txt = re.sub(r'[\x00-\x1f\x7f-\x9f]+', ' ', txt)
                    txt = re.sub(r'\s+', ' ', txt).strip()

                    b_type = b.get("block_type")
                    asset = b.get("asset_reference")
                    if asset:
                        img_p = manifest_path.parent / asset
                        if img_p.exists():
                            slides_map[s_num]["images"].append(str(img_p))

                    if txt:
                        # Exclude boilerplate branding or isolated single digits
                        if txt in ["NATSTEEL HOLDINGS P/L", "You are here"] or (txt.isdigit() and len(txt) <= 2):
                            continue
                        slides_map[s_num]["texts"].append(txt)

                        # Check if text is table markup, rating headers, or questionnaire
                        is_table_or_noise = (
                            "|" in txt
                            or any(noise in txt.lower() for noise in ["rating (pt)", "nobody makes a mess", "how much score", "score do you give"])
                            or (txt.isdigit() and len(txt) <= 2)
                        )

                        if b_type == "heading" and not is_table_or_noise and len(txt) > 3:
                            slides_map[s_num]["headings"].append(txt)
                            slides_map[s_num]["title"] = txt  # Prioritize clean heading
                        elif not slides_map[s_num]["title"] and len(txt) > 3 and not is_table_or_noise:
                            slides_map[s_num]["title"] = txt
                        else:
                            clean_b = re.sub(r'^[•\-\*\d\.\)\s]+', '', txt).strip()
                            if clean_b and len(clean_b) > 3:
                                slides_map[s_num]["bullets"].append(clean_b)

                # Merge sentence fragments in extracted bullets (e.g., split across text frames)
                for s_num, s in slides_map.items():
                    merged: list[str] = []
                    i = 0
                    b_list = s["bullets"]
                    while i < len(b_list):
                        cur = b_list[i]
                        if i + 1 < len(b_list) and not cur.endswith((".", "!", "?", ":", ";")) and len(cur) < 55:
                            merged.append(cur + " " + b_list[i + 1])
                            i += 2
                        else:
                            merged.append(cur)
                            i += 1
                    s["bullets"] = merged

                    # Refine missing titles
                    if not s["title"] and s["headings"]:
                        s["title"] = s["headings"][0]
                    if not s["title"] and s["bullets"]:
                        s["title"] = s["bullets"][0][:45]

            except Exception as e:
                logger.debug("Could not read manifest slides for job %s: %s", job_id, e)

    # Fallback to citations if manifest was empty or missing
    if not slides_map:
        for c in analysis.main_concepts:
            for ref in c.source_references:
                if ref.slide_number and ref.slide_number > 0:
                    s_num = ref.slide_number
                    if s_num not in slides_map:
                        slides_map[s_num] = {
                            "slide_number": s_num,
                            "title": c.name,
                            "headings": [c.name],
                            "bullets": [c.explanation],
                            "images": [],
                            "texts": [c.explanation],
                        }

    return [slides_map[k] for k in sorted(slides_map.keys())]


def _chunk_slides_sequentially(slides: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Partitions all slides 1..N into 3 to 6 contiguous sequential chunks with zero gaps."""
    total = len(slides)
    if total <= 4:
        target_k = total
    elif total <= 8:
        target_k = min(4, max(3, math.ceil(total / 2.0)))
    elif total <= 14:
        target_k = min(5, max(4, math.ceil(total / 2.8)))
    elif total <= 20:
        target_k = 5
    else:
        target_k = min(6, max(5, math.ceil(total / 4.5)))

    chunk_size = total / target_k
    chunks: list[list[dict[str, Any]]] = []
    for k in range(target_k):
        s_idx = int(round(k * chunk_size))
        e_idx = int(round((k + 1) * chunk_size))
        if s_idx < e_idx:
            chunks.append(slides[s_idx:e_idx])
    return chunks or [slides]


def _clean_lesson_title(raw_title: str, chunk_index: int, total_chunks: int) -> str:
    """Produces clean, professional lesson titles from raw slide headers."""
    t = re.sub(r'[\x00-\x1f\x7f-\x9f]+', ' ', raw_title)
    t = re.sub(r'\s+', ' ', t).strip()
    t = re.sub(r'^(Please comply with our|Please look out for|Important reminder:?|For your own safety:?|Lesson \d+:?)\s*', '', t, flags=re.IGNORECASE).strip()
    t = re.sub(r'^(To ensure|To achieve|To follow|To)\s+', '', t, flags=re.IGNORECASE).strip()
    t = re.sub(r'(……|\.\.\.).*$', '', t).strip()
    t = re.sub(r'\|.*$', '', t).strip()
    t = re.sub(r'[:\-–—\.]+$', '', t).strip()
    if any(p in t.lower() for p in ["rating (pt)", "how much score", "score do you give", "nobody makes a mess", "thank you", "the end"]):
        t = ""
    if not t or len(t) < 4 or t.isdigit():
        t = f"Operational Standards Part {chunk_index}"
    elif len(t) > 55:
        t = t[:52].rsplit(' ', 1)[0]
    # Capitalize first letter
    if t and t[0].islower():
        t = t[0].upper() + t[1:]
    return f"Lesson {chunk_index}: {t}"


def _generate_fallback_plan(
    job_id: str,
    analysis: AIAnalysisResult,
    audience: str = "workers",
    target_duration_seconds: float = 120.0,
) -> EducationalPlan:
    """Generate a deterministic corporate training lesson plan guaranteeing 100% sequential slide coverage."""
    slides = _get_slides_info(job_id, analysis)
    concept_names = [c.name for c in analysis.main_concepts] or ["Safe Operating Procedures"]
    facts_list = [f.statement for f in analysis.important_facts]

    # If no slides were extracted (e.g. raw text input), partition concepts into 4 modules
    if not slides:
        num_mods = min(5, max(3, len(concept_names)))
        per_mod_dur = round(target_duration_seconds / num_mods, 1)
        modules = []
        for i in range(num_mods):
            c_slice = concept_names[i::num_mods] or [concept_names[0]]
            title = c_slice[0]
            modules.append(
                TeachingModule(
                    module_id=f"mod_{i + 1}",
                    title=f"Lesson {i + 1}: {title}",
                    purpose=f"Master practical rules and procedures for {title.lower()}.",
                    learning_objective=f"Demonstrate compliance with {title} standards.",
                    start_slide=i + 1,
                    end_slide=i + 1,
                    slide_numbers=[i + 1],
                    target_concepts=c_slice,
                    important_facts=facts_list[i : i + 2],
                    depth_level="in-depth",
                    estimated_duration_seconds=per_mod_dur,
                    render_status="pending",
                )
            )
        return EducationalPlan(
            job_id=job_id,
            audience_level=audience,
            pedagogical_approach=f"Practical {audience}-level corporate training divided into sequential operational lessons.",
            pacing_strategy="Direct, clear pacing with step-by-step visual demonstrations.",
            modules=modules,
            total_estimated_seconds=sum(m.estimated_duration_seconds for m in modules),
        )

    # We have actual slides. Group them into sequential chunks covering all slides 1..N
    chunks = _chunk_slides_sequentially(slides)
    per_mod_dur = round(target_duration_seconds / len(chunks), 1)
    modules: list[TeachingModule] = []

    for idx, chunk in enumerate(chunks, 1):
        s_nums = [s["slide_number"] for s in chunk]

        # Gather descriptive title from chunk slides
        cand_titles = [
            s["title"] for s in chunk
            if s["title"] and not s["title"].isdigit() and len(s["title"]) > 4
            and not any(bad in s["title"].lower() for bad in ["rating (pt)", "score do you give", "|", "how much score", "thank you", "the end"])
        ]
        raw_head = cand_titles[0] if cand_titles else f"Section {idx}"
        lesson_title = _clean_lesson_title(raw_head, idx, len(chunks))

        # Collect all substantive bullets from chunk slides
        chunk_bullets: list[str] = []
        for s in chunk:
            chunk_bullets.extend(s.get("bullets", []))

        # Match concepts
        rel_concepts = [
            c.name for c in analysis.main_concepts
            if any(ref.slide_number in s_nums for ref in c.source_references)
        ]
        if not rel_concepts:
            rel_concepts = cand_titles[:3] if cand_titles else concept_names[idx - 1 : idx + 1]

        # Match facts
        rel_facts = [
            f.statement for f in analysis.important_facts
            if any(ref.slide_number in s_nums for ref in f.source_references)
        ]
        if not rel_facts and chunk_bullets:
            rel_facts = chunk_bullets[:3]
        if not rel_facts:
            rel_facts = facts_list[(idx - 1) * 2 : idx * 2] or ["Adhere strictly to standard operating procedures."]

        first_slide = chunk[0].get("title") or f"Slide {s_nums[0]}"
        last_slide = chunk[-1].get("title") or f"Slide {s_nums[-1]}"
        purpose_str = f"Understand core procedures and compliance standards covering {first_slide}"
        if len(chunk) > 1:
            purpose_str += f" through {last_slide}."
        else:
            purpose_str += "."

        modules.append(
            TeachingModule(
                module_id=f"mod_{idx}",
                title=lesson_title,
                purpose=purpose_str,
                learning_objective=f"Master all rules and standards outlined in slides {s_nums[0]} to {s_nums[-1]}.",
                start_slide=s_nums[0],
                end_slide=s_nums[-1],
                slide_numbers=s_nums,
                target_concepts=rel_concepts[:4],
                important_facts=rel_facts[:4],
                prerequisite_bridges=[],
                key_analogies=[],
                depth_level="in-depth",
                estimated_duration_seconds=per_mod_dur,
                render_status="pending",
            )
        )

    total_est = sum(m.estimated_duration_seconds for m in modules)
    return EducationalPlan(
        job_id=job_id,
        audience_level=audience,
        pedagogical_approach="Actionable corporate training divided into focused operational lessons.",
        pacing_strategy="Clear spoken instructions synchronized with animated 2D demonstrations.",
        modules=modules,
        total_estimated_seconds=total_est,
    )


def _regularize_plan_slides(
    modules: list[TeachingModule],
    slides: list[dict[str, Any]],
    target_duration: float,
) -> list[TeachingModule]:
    """Ensures AI generated modules cover 100% of presentation slides in sequential order."""
    if not slides or not modules:
        return modules

    total_slides = len(slides)
    all_slide_nums = set(s["slide_number"] for s in slides)
    covered_slide_nums = set()
    for m in modules:
        covered_slide_nums.update(m.slide_numbers)

    # Check if AI skipped significant slides or ordered them non-sequentially
    missing = all_slide_nums - covered_slide_nums
    is_sequential = all(
        modules[i].end_slide <= modules[i + 1].start_slide
        for i in range(len(modules) - 1)
    )

    if not missing and is_sequential and len(modules) >= 3:
        return modules

    logger.info("AI plan had %d missing slides or was non-sequential. Regularizing to contiguous slide chunks.", len(missing))
    chunks = _chunk_slides_sequentially(slides)
    per_mod_dur = round(target_duration / len(chunks), 1)

    regularized: list[TeachingModule] = []
    for idx, chunk in enumerate(chunks, 1):
        s_nums = [s["slide_number"] for s in chunk]
        # Inherit title from AI if available, else derive from slide
        ai_mod = modules[idx - 1] if idx - 1 < len(modules) else None
        cand_titles = [
            s["title"] for s in chunk
            if s["title"] and not s["title"].isdigit() and len(s["title"]) > 4
            and not any(bad in s["title"].lower() for bad in ["rating (pt)", "score do you give", "|", "how much score", "thank you", "the end"])
        ]
        derived_title = _clean_lesson_title(cand_titles[0] if cand_titles else f"Section {idx}", idx, len(chunks))
        # Prevent prompt placeholder pollution
        is_copied_boilerplate = ai_mod and any(bp in ai_mod.title.lower() for bp in ["mandatory escorting", "site orientation", "visitor brief"]) and "orientation" not in derived_title.lower()
        title = derived_title if (is_copied_boilerplate or not ai_mod or "lesson" not in ai_mod.title.lower()) else ai_mod.title

        chunk_bullets: list[str] = []
        for s in chunk:
            chunk_bullets.extend(s.get("bullets", []))

        facts = (ai_mod.important_facts if ai_mod and ai_mod.important_facts else []) + chunk_bullets[:3]
        concepts = (ai_mod.target_concepts if ai_mod and ai_mod.target_concepts else []) + cand_titles[:2]

        regularized.append(
            TeachingModule(
                module_id=f"mod_{idx}",
                title=title,
                purpose=ai_mod.purpose if ai_mod else f"Operating standards for slides {s_nums[0]} to {s_nums[-1]}.",
                learning_objective=ai_mod.learning_objective if ai_mod else f"Follow all safety rules in slides {s_nums[0]} to {s_nums[-1]}.",
                start_slide=s_nums[0],
                end_slide=s_nums[-1],
                slide_numbers=s_nums,
                target_concepts=concepts[:4] or ["Workplace Procedures"],
                important_facts=facts[:4] or ["Follow verified procedures strictly."],
                prerequisite_bridges=[],
                key_analogies=[],
                depth_level="in-depth",
                estimated_duration_seconds=per_mod_dur,
                render_status="pending",
            )
        )

    return regularized


def generate_educational_plan(
    analysis: AIAnalysisResult,
    audience: str,
    client: OllamaClient,
    text_model: str,
    job_id: str,
    target_duration_seconds: float = 120.0,
) -> EducationalPlan:
    """Generate corporate training lesson plan using Qwen2.5:14B or deterministic fallback."""
    slides = _get_slides_info(job_id, analysis)

    # Format slide descriptions for the prompt
    slides_lines = []
    for s in slides:
        h_part = s["title"] or "Slide"
        b_part = "; ".join(s["bullets"][:2]) if s.get("bullets") else ""
        slides_lines.append(f"- Slide {s['slide_number']}: {h_part} {('— ' + b_part[:120]) if b_part else ''}")
    slides_text = "\n".join(slides_lines) if slides_lines else "No slide numbers detected; group by main concepts."

    concepts_summary = ", ".join(f"{c.name}: {c.explanation[:60]}" for c in analysis.main_concepts[:6])
    facts_summary = "; ".join(f.statement[:80] for f in analysis.important_facts[:6])

    prompt = PLANNER_PROMPT_TEMPLATE.format(
        audience=audience,
        target_duration_seconds=target_duration_seconds,
        title=analysis.title,
        subject=analysis.subject,
        summary=analysis.summary[:400],
        concepts=concepts_summary or "Core operating procedures",
        facts=facts_summary or "Compliance rules",
        slides_text=slides_text,
        total_slides=len(slides) or 5,
    )

    try:
        with gpu_manager.guard(job_id, text_model, "educational_planner"):
            logger.info("Executing educational planner with model %s for job %s", text_model, job_id)
            resp = client.chat(
                model=text_model,
                messages=[{"role": "user", "content": prompt}],
                format="json",
            )

        data = clean_and_parse_json(resp["content"])
        raw_modules = data.get("modules", [])
        modules: list[TeachingModule] = []

        for idx, m in enumerate(raw_modules):
            if isinstance(m, dict):
                start_s = int(m.get("start_slide", idx + 1))
                end_s = int(m.get("end_slide", start_s))
                s_nums = m.get("slide_numbers") or list(range(start_s, end_s + 1))
                modules.append(
                    TeachingModule(
                        module_id=m.get("module_id") or f"mod_{idx + 1}",
                        title=m.get("title", f"Lesson {idx + 1}"),
                        purpose=m.get("purpose", "Practical training lesson"),
                        learning_objective=m.get("learning_objective", ""),
                        start_slide=start_s,
                        end_slide=end_s,
                        slide_numbers=[int(x) for x in s_nums],
                        target_concepts=m.get("target_concepts", []),
                        important_facts=m.get("important_facts", []),
                        prerequisite_bridges=m.get("prerequisite_bridges", []),
                        key_analogies=m.get("key_analogies", []),
                        depth_level=m.get("depth_level", "in-depth"),
                        estimated_duration_seconds=float(m.get("estimated_duration_seconds", 30.0)),
                        render_status="pending",
                    )
                )

        if not modules:
            return _generate_fallback_plan(job_id, analysis, audience, target_duration_seconds=target_duration_seconds)

        # Enforce 100% sequential slide coverage
        regularized_modules = _regularize_plan_slides(modules, slides, target_duration_seconds)

        total_est = sum(m.estimated_duration_seconds for m in regularized_modules)
        return EducationalPlan(
            job_id=job_id,
            audience_level=audience,
            pedagogical_approach=data.get("pedagogical_approach") or "Practical corporate training divided into sequential operational lessons.",
            pacing_strategy=data.get("pacing_strategy") or "Clear spoken instructions synchronized with animated 2D demonstrations.",
            modules=regularized_modules,
            total_estimated_seconds=total_est,
        )

    except Exception as exc:
        logger.warning("Failed to generate plan via AI for job %s: %s; using deterministic fallback", job_id, exc)
        return _generate_fallback_plan(job_id, analysis, audience, target_duration_seconds=target_duration_seconds)
