"""Master Educational Director Pipeline for Explain Studio.

Sequentially coordinates:
1. Content Intelligence & Multimodal Vision Analysis (Qwen2.5:14B + Qwen2.5-VL:7B)
2. Educational Planning (Pedagogical sequence adapted to audience level)
3. Narration Script Generation (Scene-by-scene spoken conversational English)
4. Storyboard Generation (Executable Motion Canvas / Manim scene specifications)
5. Adaptive Duration Planning (Cadence, cognitive pauses, and transition buffers)
6. Programmatic Quality Assurance Layer (Source grounding verification & sanity checks)
7. Multi-artifact persistence and database synchronization.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from ... import db
from ...extractor.models import ExtractionManifest
from ...storage import analysis_dir, ensure_analysis_dir, extracted_dir
from ..analyzer import process_job_analysis, resolve_model_tags
from ..client import OllamaClient
from ..models import (
    CompleteDirectorResult,
    EducationalPlan,
    NarrationScript,
    Storyboard,
)
from .duration import calculate_adaptive_duration
from .grounding import CitationResolver
from .planner import generate_educational_plan
from .qa import validate_director_artifacts
from .scriptwriter import generate_narration_script
from .storyboarder import generate_storyboard

logger = logging.getLogger("explain_studio.ai.director.pipeline")


def run_complete_director_pipeline(job_id: str) -> CompleteDirectorResult:
    """
    Execute the entire AI Educational Director suite for an extracted job.
    Guarantees strict serialized GPU access and comprehensive persistence.
    """
    start_time = time.perf_counter()
    logger.info("Starting complete AI Educational Director pipeline for job %s", job_id)

    # 1. Job inspection
    job = db.get_job(job_id)
    if not job:
        raise ValueError(f"Job {job_id} not found.")

    audience = job.get("audience") or "intermediate"
    target_duration_seconds = float(job.get("target_duration_seconds") or 120.0)

    # 2. Manifest inspection
    manifest_file = extracted_dir(job_id) / "manifest.json"
    if not manifest_file.exists():
        raise ValueError(f"Extraction manifest for job {job_id} not found. Content must be extracted first.")
    manifest_data = json.loads(manifest_file.read_text(encoding="utf-8"))
    manifest = ExtractionManifest.model_validate(manifest_data)

    client = OllamaClient()
    health = client.check_connection()
    ollama_online = health.get("connected", False)

    if ollama_online:
        try:
            text_model, vision_model = resolve_model_tags(client)
        except Exception as e:
            logger.warning("Failed to resolve Ollama model tags: %s. Using local director fallback.", e)
            ollama_online = False

    if ollama_online:
        # 3. Step 1: Content Intelligence & Multimodal Analysis
        db.update_job_status(
            job_id,
            status="analyzing",
            stage="Analyzing content concepts and visual diagrams…",
        )
        analysis = process_job_analysis(job_id)

        # 4. Step 2: Educational Planner
        db.update_job_status(
            job_id,
            status="analyzing",
            stage=f"Designing pedagogical teaching plan for {audience} audience…",
        )
        plan = generate_educational_plan(
            analysis=analysis,
            audience=audience,
            client=client,
            text_model=text_model,
            job_id=job_id,
            target_duration_seconds=target_duration_seconds,
        )

        # 5. Step 3: Narration Script Generator
        db.update_job_status(
            job_id,
            status="analyzing",
            stage="Authoring conversational narration script with scene segments…",
        )
        script = generate_narration_script(
            analysis=analysis,
            plan=plan,
            client=client,
            text_model=text_model,
            job_id=job_id,
            target_duration_seconds=target_duration_seconds,
        )

        # 6. Step 4: Storyboard Generator
        db.update_job_status(
            job_id,
            status="analyzing",
            stage="Generating structured storyboard for Motion Canvas and Manim…",
        )
        storyboard = generate_storyboard(
            analysis=analysis,
            plan=plan,
            script=script,
            client=client,
            text_model=text_model,
            job_id=job_id,
        )
    else:
        logger.info("Remote Ollama is offline. Employing local deterministic corporate training director fallback for job %s", job_id)
        from .planner import _generate_fallback_plan
        from .scriptwriter import _generate_fallback_script
        from .storyboarder import _generate_fallback_storyboard
        from ..models import AIAnalysisResult, MainConcept, ImportantFact

        db.update_job_status(
            job_id,
            status="analyzing",
            stage="Analyzing presentation structure and grouping lessons…",
        )
        analysis_file = ensure_analysis_dir(job_id) / "analysis.json"
        if analysis_file.exists():
            analysis = AIAnalysisResult.model_validate(json.loads(analysis_file.read_text(encoding="utf-8")))
        else:
            first_filename = manifest.sources[0].original_filename if manifest.sources else "Corporate Training Course"
            extracted_facts = []
            extracted_concepts = []
            for b in manifest.blocks:
                txt = (b.text or "").strip()
                if txt and len(txt) > 20 and len(extracted_facts) < 10:
                    extracted_facts.append(ImportantFact(
                        fact_id=f"fact_{len(extracted_facts)+1}",
                        statement=txt[:140],
                        verification_method="logical_deduction",
                        source_references=[],
                    ))
                if b.block_type == "heading" and txt and len(extracted_concepts) < 8:
                    extracted_concepts.append(MainConcept(
                        name=txt[:40],
                        definition=f"Operating procedure and standards for {txt[:40]}.",
                        importance_score=0.9,
                        difficulty_level="introductory",
                        source_references=[],
                    ))
            analysis = AIAnalysisResult(
                job_id=job_id,
                analyzed_at=datetime.now(timezone.utc).isoformat(),
                title=first_filename,
                subject="Workplace Procedures & Safety Standards",
                summary="Comprehensive operational training for site personnel and visitors.",
                main_concepts=extracted_concepts,
                important_facts=extracted_facts,
            )

        plan = _generate_fallback_plan(job_id, analysis, audience=audience, target_duration_seconds=target_duration_seconds)
        script = _generate_fallback_script(job_id, analysis, plan)
        storyboard = _generate_fallback_storyboard(job_id, analysis, plan, script)

    # 7. Step 5: Adaptive Duration Planner (reconciling narration, visual demos, pauses, and modules)
    duration = calculate_adaptive_duration(
        scenes=storyboard.scenes,
        script=script,
        plan=plan,
        audience_level=audience,
    )

    # 8. Step 6: Programmatic Quality Assurance & Citation Grounding
    db.update_job_status(
        job_id,
        status="analyzing",
        stage="Running programmatic quality assurance and source verification…",
    )
    quality = validate_director_artifacts(
        manifest=manifest,
        analysis=analysis,
        plan=plan,
        script=script,
        storyboard=storyboard,
    )

    # Normalize citations in artifacts to canonical source_id and content_block_id
    resolver = CitationResolver(manifest)
    for c in analysis.main_concepts:
        c.source_references, _, _ = resolver.normalize_citation_list(c.source_references, context_name=f"concept_{c.name}")
    for f in analysis.important_facts:
        f.source_references, _, _ = resolver.normalize_citation_list(f.source_references, context_name="fact")
    for seg in script.segments:
        seg.source_references, _, _ = resolver.normalize_citation_list(seg.source_references, context_name=f"script_{seg.segment_id}")
    for sc in storyboard.scenes:
        sc.source_references, _, _ = resolver.normalize_citation_list(sc.source_references, context_name=f"scene_{sc.scene_id}")

    # 9. Step 7: Persist All Structured Artifacts
    out_dir = ensure_analysis_dir(job_id)
    now_iso = datetime.now(timezone.utc).isoformat()

    master_result = CompleteDirectorResult(
        job_id=job_id,
        analyzed_at=now_iso,
        analysis=analysis,
        plan=plan,
        script=script,
        storyboard=storyboard,
        duration=duration,
        quality=quality,
    )

    # Save individual artifacts for fast targeted retrieval
    (out_dir / "analysis.json").write_text(analysis.model_dump_json(indent=2), encoding="utf-8")
    (out_dir / "plan.json").write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    (out_dir / "script.json").write_text(script.model_dump_json(indent=2), encoding="utf-8")
    (out_dir / "storyboard.json").write_text(storyboard.model_dump_json(indent=2), encoding="utf-8")
    (out_dir / "duration.json").write_text(duration.model_dump_json(indent=2), encoding="utf-8")
    (out_dir / "quality.json").write_text(quality.model_dump_json(indent=2), encoding="utf-8")
    (out_dir / "director.json").write_text(master_result.model_dump_json(indent=2), encoding="utf-8")

    elapsed = round(time.perf_counter() - start_time, 2)
    logger.info("Complete AI Educational Director pipeline finished in %ss for job %s", elapsed, job_id)

    # 10. Update SQLite Job Record & Guard Video Rendering Progression
    stage_msg = (
        f"AI Educational Director complete ({duration.formatted_duration}, "
        f"{storyboard.total_scenes} scenes, QA score {int(quality.overall_score * 100)}%)."
        if quality.passed
        else f"AI Educational Director completed with QA warnings: {'; '.join(quality.actionable_reasons[:2])}. Video rendering blocked."
    )
    db.update_job_status(
        job_id,
        status="analyzed",
        stage=stage_msg,
    )

    return master_result
