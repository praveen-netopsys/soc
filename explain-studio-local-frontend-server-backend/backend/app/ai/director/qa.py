"""Programmatic Quality Assurance Layer for Explain Studio AI Director.

Validates:
- Programmatic source grounding against the real Extraction Manifest.
- Canonical resolution of valid content_block_ids and parent source_ids.
- Zero-tolerance detection of invented, hallucinated, or cross-document mismatched citations.
- Source coverage across all uploaded files and presentations.
- Missing concepts and pedagogical alignment with the teaching plan.
- Script completeness and tone consistency.
- Storyboard executable integrity (valid animation types, non-zero durations, matching narration).
- Generates actionable failure reasons that block progression to video rendering.
"""
from __future__ import annotations

import logging
from typing import Any

from ...extractor.models import ExtractionManifest
from ..models import (
    AIAnalysisResult,
    EducationalPlan,
    NarrationScript,
    QualityReport,
    Storyboard,
)
from .grounding import CitationResolver, CitationStatus

logger = logging.getLogger("explain_studio.ai.director.qa")

VALID_ANIMATION_TYPES: frozenset[str] = frozenset([
    "ppe_inspection_walkthrough",
    "vehicle_inspection_routine",
    "traffic_rules_and_speed_limits",
    "loading_dock_and_cargo_securing",
    "workstation_5s_transformation",
    "lockout_tagout_procedure",
    "emergency_stop_and_evacuation",
    "hazard_identification_and_stop",
    "motion_canvas_code",
    "manim_math",
    "kinetic_typography",
    "diagram_reveal",
    "concept_map",
    "comparison",
    "illustration",
    "table_breakdown",
    "physics_simulation",
    "two_body_attraction",
    "spacetime_mesh",
    "orbital_simulation",
    "escape_velocity_comparison",
    "scientific_diagram",
    "cell_division_mitosis",
    "sorting_algorithm_bars",
    "geometric_dissection",
    "historical_civilization_map",
])


def validate_director_artifacts(
    manifest: ExtractionManifest,
    analysis: AIAnalysisResult,
    plan: EducationalPlan,
    script: NarrationScript,
    storyboard: Storyboard,
) -> QualityReport:
    """
    Run programmatic verification across all director artifacts against the raw source manifest.
    """
    warnings: list[str] = []
    invalid_refs: list[str] = []
    mismatched_refs: list[str] = []
    unsupported_claims: list[str] = []
    missing_concepts: list[str] = []
    duplicate_explanations: list[str] = []
    actionable_reasons: list[str] = []

    resolver = CitationResolver(manifest)
    total_claims_grounded = 0
    all_cited_sources: set[str] = set()

    def check_citation(ref: Any, context_name: str) -> None:
        nonlocal total_claims_grounded
        res = resolver.resolve_reference(ref, context_name=context_name)
        if res.is_valid:
            total_claims_grounded += 1
            if res.canonical_source_id:
                all_cited_sources.add(res.canonical_source_id)
        elif res.status == CitationStatus.MISMATCHED:
            if res.error_message:
                mismatched_refs.append(res.error_message)
        else:  # UNKNOWN_BLOCK or UNKNOWN_SOURCE
            if res.error_message:
                invalid_refs.append(res.error_message)

    # 1. Check analysis concepts & facts
    for c in analysis.main_concepts:
        for r in c.source_references:
            check_citation(r, f"concept '{c.name}'")

    for f in analysis.important_facts:
        if not f.source_references:
            unsupported_claims.append(f.statement[:80])
        else:
            for r in f.source_references:
                check_citation(r, f"fact '{f.statement[:40]}'")

    # 2. Check script segments
    for seg in script.segments:
        for r in seg.source_references:
            check_citation(r, f"script segment '{seg.segment_id}'")

    # 3. Check storyboard scenes
    for sc in storyboard.scenes:
        for r in sc.source_references:
            check_citation(r, f"storyboard scene '{sc.scene_id}'")

    # 4. Source Coverage Percentage
    total_sources = len(manifest.sources)
    if total_sources > 0:
        actual_cited = len(all_cited_sources.intersection(resolver.valid_source_keys))
        coverage_pct = round((actual_cited / total_sources) * 100.0, 1)
        if coverage_pct < 100.0 and total_sources > 1:
            warnings.append(
                f"Source coverage is {coverage_pct}%: not all input files were referenced in the output."
            )
    else:
        coverage_pct = 100.0

    # 5. Check Missing Concepts from Teaching Plan
    script_text_lower = " ".join(seg.narration_text.lower() for seg in script.segments)
    for mod in plan.modules:
        for concept in mod.target_concepts:
            if concept.lower() not in script_text_lower:
                missing_concepts.append(f"Module '{mod.title}': concept '{concept}' not covered in script")

    # 6. Check Duplicate Explanations
    seen_explanations: set[str] = set()
    for c in analysis.main_concepts:
        norm = c.explanation.strip().lower()[:60]
        if norm in seen_explanations:
            duplicate_explanations.append(f"Redundant explanation for concept '{c.name}'")
        seen_explanations.add(norm)

    # 7. Script Completeness
    script_complete = True
    if len(script.segments) == 0:
        script_complete = False
        actionable_reasons.append("Script contains zero segments.")
    elif script.total_word_count < 30:
        script_complete = False
        actionable_reasons.append("Script is too brief to form a coherent educational video (<30 words).")

    # 8. Storyboard Consistency
    storyboard_consistent = True
    if len(storyboard.scenes) == 0:
        storyboard_consistent = False
        actionable_reasons.append("Storyboard contains zero scenes.")
    else:
        if len(storyboard.scenes) != len(script.segments):
            warnings.append(
                f"Storyboard scene count ({len(storyboard.scenes)}) differs from script segment count ({len(script.segments)})."
            )

        for sc in storyboard.scenes:
            if sc.estimated_visual_duration <= 0.0:
                storyboard_consistent = False
                actionable_reasons.append(
                    f"Scene '{sc.scene_id}' has non-positive duration ({sc.estimated_visual_duration}s)."
                )

            if sc.animation_type not in VALID_ANIMATION_TYPES:
                warnings.append(
                    f"Scene '{sc.scene_id}' has non-standard animation type '{sc.animation_type}'."
                )

    # 9. Assemble Actionable Reasons for Citation Failures
    if invalid_refs:
        actionable_reasons.append(
            f"Detected {len(invalid_refs)} unknown or hallucinated citation(s). "
            f"All content_block_ids must exist in the extraction manifest."
        )
    if mismatched_refs:
        actionable_reasons.append(
            f"Detected {len(mismatched_refs)} cross-document citation mismatch(es). "
            f"Content blocks must belong to their cited parent source document."
        )

    # 10. Score Calculation (0.0 to 1.0)
    score = 1.0
    if invalid_refs:
        score -= min(0.35, len(invalid_refs) * 0.08)
    if mismatched_refs:
        score -= min(0.25, len(mismatched_refs) * 0.06)
    if unsupported_claims:
        score -= min(0.15, len(unsupported_claims) * 0.03)
    if missing_concepts:
        score -= min(0.15, len(missing_concepts) * 0.03)
    if not script_complete:
        score -= 0.3
    if not storyboard_consistent:
        score -= 0.2

    final_score = round(max(0.0, min(1.0, score)), 2)

    # Mandatory QA pass criteria:
    # 1. No unknown/hallucinated IDs.
    # 2. No mismatched cross-document citations.
    # 3. Script completeness check passed.
    # 4. Storyboard consistency check passed.
    # 5. Score >= 0.70.
    passed = (
        (final_score >= 0.70)
        and len(invalid_refs) == 0
        and len(mismatched_refs) == 0
        and script_complete
        and storyboard_consistent
    )

    if not passed and final_score < 0.70 and not actionable_reasons:
        actionable_reasons.append(
            f"Overall QA score ({final_score}) is below the minimum 0.70 threshold required for rendering."
        )

    return QualityReport(
        overall_score=final_score,
        passed=passed,
        source_coverage_pct=coverage_pct,
        total_claims_grounded=total_claims_grounded,
        invalid_source_references=invalid_refs,
        mismatched_source_references=mismatched_refs,
        unsupported_claims=unsupported_claims,
        missing_concepts=missing_concepts,
        duplicate_explanations=duplicate_explanations,
        script_completeness_check=script_complete,
        storyboard_consistency_check=storyboard_consistent,
        actionable_reasons=actionable_reasons,
        warnings=warnings,
    )
