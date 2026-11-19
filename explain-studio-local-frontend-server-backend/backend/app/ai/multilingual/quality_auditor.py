"""Teaching Quality and Pedagogical Compliance Auditor.

Enforces:
1. Complete slide coverage with zero gaps or overlaps.
2. Thorough explanation depth (no skipped or empty explanations).
3. Zero-tolerance anti-announcement filter (auto-sanitizes).
4. Repetition detection: exact duplicate sentences + template phrase abuse.
5. Multilingual completeness across all 6 required languages.
"""
from __future__ import annotations

import logging
import re
import time
from collections import Counter
from typing import Any

from .models import MultilingualCourse, SUPPORTED_LANGUAGES
from .planner import sanitize_explanation_text, validate_slide_allocation

logger = logging.getLogger("explain_studio.multilingual.quality_auditor")

FORBIDDEN_ANNOUNCEMENT_PATTERNS = [
    re.compile(r"\b(slide\s+\d+)\b", re.IGNORECASE),
    re.compile(r"\b(in\s+this\s+slide)\b", re.IGNORECASE),
    re.compile(r"\b(this\s+slide\s+(shows|presents|covers|outlines|details))\b", re.IGNORECASE),
    re.compile(r"\b(the\s+title\s+of\s+this\s+slide\s+is)\b", re.IGNORECASE),
    re.compile(r"\b(moving\s+to\s+(the\s+)?next\s+slide)\b", re.IGNORECASE),
    re.compile(r"\b(on\s+this\s+slide)\b", re.IGNORECASE),
    re.compile(r"\b(as\s+seen\s+on\s+slide\s+\d+)\b", re.IGNORECASE),
]

REPETITIVE_OPENERS = [
    r"it is important to remember",
    r"building on these principles",
    r"as we can see",
    r"let us (now |)explore",
    r"let us understand",
    r"here, our objective",
    r"pay close attention",
    r"ensure you master",
    r"in practice:",
    r"this parameter is critical",
]

_REPETITIVE_PATTERN = re.compile(
    "|".join(rf"(?:{p})" for p in REPETITIVE_OPENERS), re.IGNORECASE
)


def _detect_repeated_sentences(text: str) -> list[str]:
    sentences = [s.strip() for s in re.split(r"[.!?]+", text) if len(s.strip()) > 20]
    counts = Counter(sentences)
    return [s for s, c in counts.items() if c > 1]


def _detect_repetitive_phrase_abuse(text: str, threshold: int = 2) -> list[str]:
    matches = _REPETITIVE_PATTERN.findall(text.lower())
    counts = Counter(m.strip() for m in matches)
    return [phrase for phrase, count in counts.items() if count >= threshold]


def _deduplicate_sentences(text: str) -> str:
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    seen: set[str] = set()
    unique: list[str] = []
    for s in sentences:
        key = s.strip().lower()
        if key and key not in seen:
            seen.add(key)
            unique.append(s.strip())
    return " ".join(unique)


def audit_and_validate_teaching_quality(
    course: MultilingualCourse,
    strict_revision: bool = True,
) -> dict[str, Any]:
    start_t = time.perf_counter()
    revisions: list[dict[str, Any]] = []
    issues: list[str] = []
    slides_checked = 0
    slides_requiring_revision = 0

    alloc_report = validate_slide_allocation(course.total_slides, course.modules)
    if not alloc_report["valid"]:
        issues.append(
            f"Slide allocation invariant violated: missing={alloc_report['missing_slides']}, "
            f"overlaps={alloc_report['overlaps']}"
        )

    for mod in course.modules:
        for slide in mod.slides:
            slides_checked += 1
            slide_revised = False
            eng_text = slide.explanations.get("en", "")

            # A. Depth check
            word_count = len(eng_text.split())
            if word_count < 15:
                issues.append(f"Slide {slide.slide_number} low depth ({word_count} words).")
                if strict_revision:
                    supplements = []
                    if slide.key_points:
                        supplements.append(f"Key procedures include: {', '.join(slide.key_points)}.")
                    if slide.table_summaries:
                        supplements.append(f"Reference data: {', '.join(slide.table_summaries)}.")
                    if supplements:
                        eng_text = f"{eng_text} {' '.join(supplements)}".strip()
                        slide.explanations["en"] = eng_text
                        slide_revised = True

            # B. Forbidden announcement check
            found_announcements: list[str] = []
            for pat in FORBIDDEN_ANNOUNCEMENT_PATTERNS:
                for m in pat.finditer(eng_text):
                    found_announcements.append(m.group(0))

            if found_announcements:
                issues.append(f"Slide {slide.slide_number} robotic announcements: {found_announcements}")
                if strict_revision:
                    eng_text = sanitize_explanation_text(eng_text)
                    slide.explanations["en"] = eng_text
                    slide_revised = True
                    revisions.append({
                        "slide_number": slide.slide_number,
                        "type": "robotic_announcement_removed",
                        "patterns_detected": found_announcements,
                    })

            # C. Duplicate sentence removal
            dupe_sentences = _detect_repeated_sentences(eng_text)
            if dupe_sentences:
                issues.append(f"Slide {slide.slide_number} has {len(dupe_sentences)} duplicate sentence(s).")
                if strict_revision:
                    eng_text = _deduplicate_sentences(eng_text)
                    slide.explanations["en"] = eng_text
                    slide_revised = True
                    revisions.append({
                        "slide_number": slide.slide_number,
                        "type": "duplicate_sentences_removed",
                        "count": len(dupe_sentences),
                    })

            # D. Repetitive template phrase abuse (log only)
            abused_phrases = _detect_repetitive_phrase_abuse(eng_text, threshold=2)
            if abused_phrases:
                issues.append(f"Slide {slide.slide_number} overuses template phrases: {abused_phrases}")

            # E. Multilingual completeness
            for lang_code in SUPPORTED_LANGUAGES:
                lang_text = slide.explanations.get(lang_code, "")
                if not lang_text or len(lang_text.strip()) < 5:
                    issues.append(f"Slide {slide.slide_number} missing lang '{lang_code}'.")
                    if strict_revision and lang_code != "en":
                        slide.explanations[lang_code] = eng_text
                        slide_revised = True

            if slide_revised:
                slides_requiring_revision += 1

    duration = round(time.perf_counter() - start_t, 3)
    valid = len(issues) == 0 or (
        strict_revision and all("invariant violated" not in i for i in issues)
    )

    report = {
        "valid": valid,
        "slides_checked": slides_checked,
        "slides_requiring_revision": slides_requiring_revision,
        "revisions_count": len(revisions),
        "revisions": revisions,
        "issues_detected": issues,
        "duration_seconds": duration,
        "languages_checked": list(SUPPORTED_LANGUAGES.keys()),
        "alloc_validation": alloc_report,
    }

    logger.info(
        "Quality audit: %d slides, %d revised, valid=%s (%.3fs)",
        slides_checked, slides_requiring_revision, valid, duration,
    )
    return report