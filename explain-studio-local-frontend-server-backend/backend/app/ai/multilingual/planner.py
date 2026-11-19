"""Concept-Based Module Creation and Slide-by-Slide Multilingual Explanation Generator.

Guarantees 100% Strict Slide Uniqueness:
Every slide belongs to exactly one module with zero gaps and zero duplicates.
"""
from __future__ import annotations

import json
import logging
import math
import re
from pathlib import Path
from typing import Any

from .glossary import build_course_glossary
from .models import (
    CourseModule,
    MultilingualCourse,
    SlideExplanation,
    SUPPORTED_LANGUAGES,
)
from .translator import generate_multilingual_explanations

logger = logging.getLogger("explain_studio.ai.multilingual.planner")


def validate_slide_allocation(total_slides: int, modules: list[CourseModule]) -> dict[str, Any]:
    """Strict Slide Allocation Validator.
    Enforces:
    1. Every original slide belongs to at least one module.
    2. No original slide belongs to more than one module.
    3. The intersection of every pair of different modules must be empty.
    4. The union of all module slide sets must equal the original slide set {1..N}.
    """
    expected_slides = set(range(1, total_slides + 1))
    allocated_slides: set[int] = set()
    overlaps: list[dict[str, Any]] = []

    for i in range(len(modules)):
        m_i = set(modules[i].slide_numbers)
        for j in range(i + 1, len(modules)):
            m_j = set(modules[j].slide_numbers)
            intersect = m_i.intersection(m_j)
            if intersect:
                overlaps.append({
                    "module_a": modules[i].module_id,
                    "module_b": modules[j].module_id,
                    "overlapping_slides": sorted(intersect),
                })
        allocated_slides.update(m_i)

    missing_slides = sorted(expected_slides - allocated_slides)
    extra_slides = sorted(allocated_slides - expected_slides)
    is_valid = (not overlaps) and (not missing_slides) and (not extra_slides) and (len(allocated_slides) == total_slides)

    report = {
        "valid": is_valid,
        "total_presentation_slides": total_slides,
        "total_allocated_slides": len(allocated_slides),
        "module_count": len(modules),
        "missing_slides": missing_slides,
        "extra_slides": extra_slides,
        "overlaps": overlaps,
        "message": (
            "Slide allocation verified: 100% complete, strictly unique, and disjoint."
            if is_valid
            else f"Allocation violation: missing {missing_slides}, overlaps {overlaps}"
        ),
    }
    return report


def _chunk_slides_contiguously(total_slides: int) -> list[list[int]]:
    """Partition slides 1..N into 3 to 6 contiguous, strictly disjoint chunks with zero gaps."""
    if total_slides <= 3:
        target_k = total_slides
    elif total_slides <= 6:
        target_k = min(3, total_slides)
    elif total_slides <= 12:
        target_k = min(4, math.ceil(total_slides / 2.5))
    elif total_slides <= 20:
        target_k = 5
    else:
        target_k = min(6, math.ceil(total_slides / 5.0))

    target_k = max(1, min(target_k, total_slides))
    chunk_size = total_slides / float(target_k)
    chunks: list[list[int]] = []

    for k in range(target_k):
        s_idx = int(round(k * chunk_size)) + 1
        e_idx = int(round((k + 1) * chunk_size))
        if s_idx <= e_idx:
            chunks.append(list(range(s_idx, e_idx + 1)))

    # Guarantee total coverage
    flattened = [n for c in chunks for n in c]
    if len(flattened) != total_slides or set(flattened) != set(range(1, total_slides + 1)):
        # Fallback to perfect linear slices
        step = math.ceil(total_slides / target_k)
        chunks = []
        for i in range(1, total_slides + 1, step):
            chunks.append(list(range(i, min(i + step, total_slides + 1))))

    return chunks


def heal_and_clean_bullets(raw_bullets: list[str]) -> list[str]:
    """Clean bullets, remove section/metadata noise, and stitch multi-line fragmented sentences."""
    cleaned: list[str] = []
    for b in raw_bullets:
        b_str = b.strip()
        if not b_str:
            continue
        # Filter out presentation section labels, rating noise, etc.
        if re.match(r'^(section|module|category|part)\s*:\s*', b_str, re.IGNORECASE):
            continue
        cleaned.append(b_str)

    # Prepositions, conjunctions, and articles that signal sentence fragments
    dangling_endings = (
        'by', 'our', 'the', 'a', 'an', 'of', 'to', 'in', 'for', 'with', 'and', 'or',
        'as', 'at', 'from', 'that', 'this', 'these', 'those', 'your', 'their', 'his',
        'her', 'its', 'such', 'on', 'into', 'onto', 'under', 'over', 'during', 'all', 'any'
    )
    healed: list[str] = []
    i = 0
    while i < len(cleaned):
        curr = cleaned[i]
        while i + 1 < len(cleaned):
            nxt = cleaned[i + 1]
            last_word = curr.rstrip('.,;:!?').split()[-1].lower() if curr.split() else ''
            is_dangling = last_word in dangling_endings
            is_continuation = (
                (not curr.endswith(('.', '!', '?', ';', ':')))
                and (nxt and (nxt[0].islower() or is_dangling))
            )
            if is_dangling or is_continuation:
                curr = curr + ' ' + nxt
                i += 1
            else:
                break
        healed.append(curr)
        i += 1
    return healed


def _extract_slide_summaries(manifest_data: dict[str, Any]) -> dict[int, dict[str, Any]]:
    """Extract ordered, clean slide details from universal manifest blocks."""
    slides: dict[int, dict[str, Any]] = {}
    for b in manifest_data.get("blocks", []):
        s_num = b.get("slide_number")
        if s_num is None or not isinstance(s_num, int) or s_num <= 0:
            continue
        if s_num not in slides:
            slides[s_num] = {
                "slide_number": s_num,
                "title": "",
                "headings": [],
                "bullets": [],
                "tables": [],
                "images": [],
                "notes": [],
            }

        txt = (b.get("text") or "").strip()
        txt = re.sub(r'[\x00-\x1f\x7f-\x9f]+', ' ', txt)
        txt = re.sub(r'\s+', ' ', txt).strip()
        b_type = b.get("block_type")
        asset = b.get("asset_reference")

        if asset:
            slides[s_num]["images"].append(asset)

        if txt:
            if txt in ["NATSTEEL HOLDINGS P/L", "You are here"] or (txt.isdigit() and len(txt) <= 2):
                continue
            is_table_or_noise = (
                "|" in txt
                or any(noise in txt.lower() for noise in ["rating (pt)", "how much score", "score do you give"])
                or (txt.isdigit() and len(txt) <= 2)
            )

            if b_type == "heading" and not is_table_or_noise and len(txt) > 3:
                slides[s_num]["headings"].append(txt)
                if not slides[s_num]["title"] or len(slides[s_num]["title"]) < 5:
                    slides[s_num]["title"] = txt
            elif b_type == "table":
                slides[s_num]["tables"].append(txt)
            elif b_type == "speaker_notes":
                slides[s_num]["notes"].append(txt)
            else:
                if not slides[s_num]["title"] and len(txt) > 3 and not is_table_or_noise:
                    slides[s_num]["title"] = txt
                else:
                    clean_b = re.sub(r'^[•\-\*\d\.\)\s]+', '', txt).strip()
                    if clean_b and len(clean_b) > 3 and clean_b not in slides[s_num]["bullets"]:
                        slides[s_num]["bullets"].append(clean_b)

    # Heal bullets across all extracted slides
    for s_data in slides.values():
        s_data["bullets"] = heal_and_clean_bullets(s_data["bullets"])

    return slides


def sanitize_explanation_text(text: str) -> str:
    """
    Ensure the explanation strictly follows the AI teaching behaviour rules:
    - Never announce slide numbers, slide titles, or robotic transitional phrases.
    - Strips phrases like 'Slide 3', 'In this slide', 'The title of this slide is', 'Moving to the next slide'.
    """
    cleaned = text
    patterns = [
        r'(?i)\bwelcome to this course[\.,\s]*we begin with slide \d+[:\s,\.]*',
        r'(?i)\bconcluding our training[\.,\s]*slide \d+ covers[:\s,\.]*',
        r'(?i)\bnext[\.,\s]*slide \d+ focuses on[:\s,\.]*',
        r'(?i)\b(in|on|for)\s+slide\s+\d+[:\s,\.]*',
        r'(?i)\b(in|on)\s+this\s+slide[:\s,\.]*',
        r'(?i)\bslide\s+\d+[:\s,\.]*',
        r'(?i)\bthe\s+title\s+of\s+this\s+slide\s+is[:\s]*',
        r'(?i)\bmoving\s+to\s+the\s+next\s+slide[:\s,\.]*',
        r'(?i)\bas\s+shown\s+on\s+this\s+slide[:\s,\.]*',
        r'(?i)\bthis\s+slide\s+(covers|shows|focuses on|explains|presents|outlines)[:\s]*',
        r'(?i)\bnext\s+slide[:\s,\.]*',
    ]
    for p in patterns:
        cleaned = re.sub(p, ' ', cleaned)
    cleaned = re.sub(r'\s{2,}', ' ', cleaned).strip()
    if cleaned and cleaned[0].islower():
        cleaned = cleaned[0].upper() + cleaned[1:]
    return cleaned


def _build_slide_teaching_explanation(s_info: dict[str, Any], slide_idx: int, total_slides: int) -> str:
    """
    Compose an authentic, natural, pedagogically rich English spoken explanation for a slide.

    Strict Pedagogical Rules:
    - Never announce slide numbers, slide titles, or robotic transitional phrases.
    - Begin explaining the actual subject naturally, as an expert instructor would.
    - Do not wrap arbitrary text in repetitive templates ("It is important to remember that").
    - Intelligently recognize imperatives, hazard descriptions, PPE lists, and declarative standards.
    - Heal broken fragments into complete, grammatical spoken sentences.
    - End smoothly when the slide concept is fully conveyed.
    """
    title = (s_info.get("title") or "").strip()
    clean_title = re.sub(
        r'^(Please comply with our|Please look out for|Important reminder:?|For your own safety:?|Lesson \d+:?)\s*',
        '', title, flags=re.IGNORECASE,
    ).strip()
    clean_title = re.sub(r'[:\-–—\.]+$', '', clean_title).strip()

    raw_bullets = s_info.get("bullets", [])
    valid_bullets = heal_and_clean_bullets(raw_bullets)
    tables = s_info.get("tables", [])
    notes = s_info.get("notes", [])

    sentences: list[str] = []

    # 1. Natural Subject-First Opening
    if clean_title:
        title_lower = clean_title[0].lower() + clean_title[1:] if clean_title[0].isupper() else clean_title
        if slide_idx == 1:
            sentences.append(
                f"{clean_title} outlines the critical standards and procedures we will cover today. "
                f"Understanding these foundational principles is essential for maintaining safety across all operations."
            )
        elif slide_idx == total_slides:
            sentences.append(
                f"To conclude our session, let us review the key responsibilities regarding {title_lower}. "
                f"Every person on site is expected to uphold these standards consistently."
            )
        elif tables:
            sentences.append(
                f"Turning to {title_lower}, the structured criteria shown here establish mandatory controls that apply to daily activities."
            )
        elif re.search(r'\b(ppe|protective equipment|gear)\b', clean_title, re.IGNORECASE):
            sentences.append(
                f"Regarding {title_lower}, strict personal protective equipment requirements must be followed before entering any operational area."
            )
        else:
            mid_openers = [
                f"Looking now at {title_lower}, compliance with these specific instructions is mandatory.",
                f"{clean_title} requires close attention to ensure safe and orderly operations.",
                f"Next, let us focus on {title_lower} and the specific protocols required.",
                f"In this section, {title_lower} sets out clear operational expectations.",
            ]
            sentences.append(mid_openers[(slide_idx - 2) % len(mid_openers)])
    elif valid_bullets:
        sentences.append("The following operational standards are essential for safety and must be followed carefully.")

    # Special handling for single bullet on title slide (e.g. topic subtitle)
    if slide_idx == 1 and len(valid_bullets) == 1:
        b0 = valid_bullets[0].strip().rstrip('.')
        has_verb0 = any(re.search(rf'\b{v}\b', b0, re.IGNORECASE) for v in ['is', 'are', 'covers', 'include', 'provides'])
        if not has_verb0:
            sentences.append(f"This briefing covers {b0[0].lower() + b0[1:]}.")
            raw_text = " ".join(sentences)
            return sanitize_explanation_text(raw_text)

    # 2. Substantive Bullet Explanation (Grammatically Harmonized)
    imperatives = (
        'do not', 'never', 'always', 'report', 'keep', 'notify', 'maintain',
        'follow', 'ensure', 'please', 'stop', 'use', 'wear', 'avoid', 'check',
        'review', 'obtain', 'stay', 'carry', 'display', 'segregate'
    )
    is_ppe_slide = bool(re.search(r'\b(ppe|protective equipment|gear)\b', clean_title, re.IGNORECASE))

    for idx, b in enumerate(valid_bullets):
        clean_b = b.strip().rstrip('.')
        if not clean_b:
            continue

        # Handle colon-separated hazard/condition statements
        if ':' in clean_b and not clean_b.lower().startswith(('http', 'note:', 'e.g.')):
            parts = clean_b.split(':', 1)
            lbl = parts[0].strip()
            det = parts[1].strip()
            if det:
                lbl_is_imp = any(lbl.lower().startswith(imp) for imp in imperatives) or 'responsibility' in lbl.lower()
                if lbl_is_imp:
                    det_low = det[0].lower() + det[1:] if det[0].isupper() else det
                    sentences.append(f"{lbl[0].upper() + lbl[1:]}; {det_low}.")
                    continue
                elif len(lbl.split()) <= 5:
                    if idx == 0:
                        sentences.append(f"First, regarding {lbl.lower()}, note that {det}.")
                    elif idx == len(valid_bullets) - 1:
                        sentences.append(f"Finally, with respect to {lbl.lower()}, remember that {det}.")
                    else:
                        conns = [
                            f"Next, regarding {lbl.lower()}, watch out for {det}.",
                            f"In terms of {lbl.lower()}, be mindful that {det}.",
                            f"For {lbl.lower()}, {det}.",
                        ]
                        sentences.append(conns[idx % len(conns)])
                    continue

        # For PPE item lists without verbs, form natural spoken requirements
        has_verb = any(
            re.search(rf'\b{v}\b', clean_b, re.IGNORECASE)
            for v in ['must', 'is', 'are', 'required', 'wear', 'worn', 'use', 'used', 'equipped', 'keep']
        )
        if is_ppe_slide and not has_verb:
            ppe_variants = [
                f"You are required to wear {clean_b[0].lower() + clean_b[1:]}.",
                f"In addition, a {clean_b[0].lower() + clean_b[1:]} is mandatory.",
                f"Make sure you also have {clean_b[0].lower() + clean_b[1:]}.",
                f"For full protection, {clean_b[0].lower() + clean_b[1:]} must be worn at all times.",
            ]
            sentences.append(ppe_variants[idx % len(ppe_variants)])
            continue

        is_imp = any(clean_b.lower().startswith(imp) for imp in imperatives)
        b_cap = clean_b[0].upper() + clean_b[1:]
        b_low = clean_b[0].lower() + clean_b[1:]

        if idx == 0:
            sentences.append(f"{b_cap}.")
        elif idx == 1:
            if is_imp:
                sentences.append(f"In addition, {b_low}.")
            else:
                sentences.append(f"Furthermore, {b_low}.")
        elif idx == 2:
            if is_imp:
                sentences.append(f"Also, {b_low}.")
            else:
                sentences.append(f"Also note that {b_low}.")
        elif idx == len(valid_bullets) - 1:
            sentences.append(f"Lastly, {b_low}.")
        else:
            sentences.append(f"{b_cap}.")

    # 3. Table and Structured Data Explanations
    if tables:
        sentences.append(
            "Review each entry in the table carefully to confirm the required actions and corresponding conditions."
        )

    # 4. Practical Speaker Notes
    if notes:
        clean_note = notes[0].strip().rstrip('.')
        if len(clean_note.split()) >= 4:
            sentences.append(f"In practice, {clean_note[0].lower() + clean_note[1:]}.")

    # 5. Natural Instructional Closing
    if slide_idx == total_slides:
        sentences.append("Please review these procedures carefully and report any safety concerns immediately.")
    elif len(valid_bullets) >= 3 or tables:
        sentences.append("Ensure all of these procedures are fully understood before starting work in this zone.")

    raw_text = " ".join(sentences)
    return sanitize_explanation_text(raw_text)

    # Edge case: nothing extracted at all
    if not sentences:
        if clean_title:
            sentences.append(
                f"{clean_title} is an important part of this training. "
                f"Review the original material carefully to ensure you understand all requirements."
            )
        else:
            sentences.append(
                "Review this section carefully to understand all requirements before proceeding."
            )

    raw_text = " ".join(sentences)
    return sanitize_explanation_text(raw_text)


def generate_multilingual_course_plan(
    job_id: str,
    manifest_data: dict[str, Any] | None = None,
    pptx_path: Path | None = None,
    presentation_title: str = "Training Course",
    target_languages: list[str] | None = None,
) -> MultilingualCourse:
    """Analyze entire presentation, divide into disjoint modules, and generate full multilingual explanations."""
    if manifest_data is None:
        manifest_data = {"blocks": []}
        # Attempt to load from extracted directory first
        from ...storage import extracted_dir
        m_file = extracted_dir(job_id) / "manifest.json"
        if m_file.exists():
            try:
                manifest_data = json.loads(m_file.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("Failed to load existing manifest.json for job %s: %s", job_id, exc)

        # Fallback to direct PPTX extraction if blocks are empty
        if not manifest_data.get("blocks") and pptx_path and pptx_path.exists():
            from ...extractor.pptx import extract_from_pptx_file
            from ...storage import ensure_extracted_dir
            e_dir = ensure_extracted_dir(job_id)
            blocks = extract_from_pptx_file(
                pptx_path,
                source_id="pptx_source",
                original_filename=pptx_path.name,
                assets_dir=e_dir / "assets",
            )
            manifest_data = {"blocks": [b.model_dump() for b in blocks]}

    slides_info = _extract_slide_summaries(manifest_data)
    total_slides = max(slides_info.keys()) if slides_info else len(manifest_data.get("blocks", []))
    if pptx_path and pptx_path.exists():
        try:
            from pptx import Presentation
            prs = Presentation(pptx_path)
            total_slides = max(total_slides, len(prs.slides))
        except Exception:
            pass
    total_slides = max(1, total_slides)

    # 1. Build Technical Course Glossary
    all_texts = []
    for s in slides_info.values():
        all_texts.append(s.get("title", ""))
        all_texts.extend(s.get("bullets", []))
    glossary = build_course_glossary(all_texts)

    # 2. Strict Contiguous Chunking
    chunks = _chunk_slides_contiguously(total_slides)
    modules: list[CourseModule] = []
    langs = target_languages or list(SUPPORTED_LANGUAGES.keys())

    for m_idx, s_nums in enumerate(chunks, 1):
        mod_id = f"module_{m_idx:02d}"
        first_slide_info = slides_info.get(s_nums[0], {})
        raw_title = first_slide_info.get("title") or f"Operational Standards Part {m_idx}"
        clean_title = re.sub(r'^(Please comply with our|Please look out for|Important reminder:?|For your own safety:?|Lesson \d+:?)\s*', '', raw_title, flags=re.IGNORECASE).strip()
        clean_title = re.sub(r'[:\-–—\.]+$', '', clean_title).strip()
        mod_title = f"Module {m_idx}: {clean_title}"

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

            # Generate in-depth English teaching explanation
            eng_explanation = _build_slide_teaching_explanation(s_det, sn, total_slides)

            # Generate all 6 language versions
            multilingual_exps = generate_multilingual_explanations(
                english_explanation=eng_explanation,
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
                module_number=m_idx,
                title=mod_title,
                description=f"Covers comprehensive operational instructions and compliance standards for slides {s_nums[0]} through {s_nums[-1]}.",
                learning_objectives=[f"Master all procedures specified in slides {s_nums[0]} to {s_nums[-1]}."],
                slide_numbers=s_nums,
                slides=slide_exps,
                durations_seconds={},
                video_files={},
            )
        )

    # 3. Validate Slide Uniqueness (Zero Gaps, Zero Overlaps)
    validation = validate_slide_allocation(total_slides, modules)
    if not validation["valid"]:
        logger.error("Slide uniqueness validation failed: %s", validation)

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
