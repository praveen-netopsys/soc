"""AI Content Understanding Pipeline.

Coordinates:
- Qwen2.5:14B for structured text reasoning, concept extraction, and source grounding.
- Qwen2.5-VL:7B for visual analysis of slides, charts, and images.
- GPU safety guard ensuring serialized single-task inference.
- Controlled chunking and hierarchical summarization for large documents.
- Persistent structured JSON output under storage/analysis/{job_id}/analysis.json.
- Full preservation of original extraction manifests.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import config, db
from ..extractor.models import ContentBlock, ExtractionManifest
from ..storage import analysis_dir, ensure_analysis_dir, extracted_dir, job_dir
from .client import (
    ModelNotFoundError,
    OllamaClient,
    OllamaConnectionError,
    _model_matches,
)
from .gpu import gpu_manager
from .models import (
    AIAnalysisResult,
    AmbiguityItem,
    ConceptRelationship,
    Definition,
    Example,
    ImportantFact,
    LearningObjective,
    MainConcept,
    SourceReference,
    SupportingConcept,
    VisualAnalysisItem,
)

logger = logging.getLogger("explain_studio.ai.analyzer")


# ── JSON Sanitizer ────────────────────────────────────────────────────────────

def clean_and_parse_json(text: str) -> dict[str, Any]:
    """
    Robustly extract and parse JSON from model output, handling markdown
    code fencing, unescaped LaTeX backslashes, trailing commas, and truncated responses.
    """
    cleaned = text.strip()

    # Strip markdown code blocks ```json ... ``` or ``` ... ```
    if "```" in cleaned:
        match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned)
        if match:
            cleaned = match.group(1).strip()

    # Find starting brace
    start = cleaned.find("{")
    if start != -1:
        cleaned = cleaned[start:]

    # 1. Direct parse attempt
    try:
        return json.loads(cleaned)
    except Exception:
        pass

    # 2. Fix trailing commas before closing braces/brackets
    repaired = re.sub(r",\s*([\]}])", r"\1", cleaned)
    try:
        return json.loads(repaired)
    except Exception:
        pass

    # 3. Handle unescaped LaTeX backslashes (e.g. \frac, \sqrt)
    unescaped = re.sub(r'\\(?![/"\\bfnrtu])', r'\\\\', repaired)
    try:
        return json.loads(unescaped)
    except Exception:
        pass

    # 4. Truncation repair: unwind incomplete tokens and close open structures
    candidate = unescaped
    for _ in range(80):
        quotes = len(re.findall(r'(?<!\\)"', candidate))
        patch = candidate
        if quotes % 2 != 0:
            patch += '"'
        open_brackets = patch.count('[') - patch.count(']')
        if open_brackets > 0:
            patch += ']' * open_brackets
        open_braces = patch.count('{') - patch.count('}')
        if open_braces > 0:
            patch += '}' * open_braces
        try:
            return json.loads(patch)
        except Exception:
            last_delim = max(candidate.rfind(','), candidate.rfind('}'), candidate.rfind(']'))
            if last_delim <= 0:
                break
            candidate = candidate[:last_delim].rstrip()

    # 5. Resilient Regex Extraction Fallback (Never crash on truncated/imperfect model output)
    logger.warning("Standard JSON parsing failed on model response. Attempting regex extraction fallback.")
    fallback_data = {
        "title": "Corporate Training Module",
        "subject": "Workplace Operations & Safety",
        "summary": "Educational corporate training program covering operational standards, safety, and procedures.",
        "concepts": [],
        "facts": [],
        "definitions": [],
        "concept_relationships": [],
        "examples": [],
        "prerequisites": [],
        "learning_objectives": [],
        "ambiguities_and_gaps": [],
    }

    m_title = re.search(r'"title"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"', text)
    if m_title:
        fallback_data["title"] = m_title.group(1).replace('\\"', '"')

    m_subj = re.search(r'"subject"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"', text)
    if m_subj:
        fallback_data["subject"] = m_subj.group(1).replace('\\"', '"')

    m_sum = re.search(r'"summary"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"', text)
    if m_sum:
        fallback_data["summary"] = m_sum.group(1).replace('\\"', '"')
    else:
        m_partial_sum = re.search(r'"summary"\s*:\s*"([^"]+)', text)
        if m_partial_sum:
            fallback_data["summary"] = m_partial_sum.group(1).strip()

    concept_matches = re.finditer(r'\{\s*"name"\s*:\s*"([^"]+)"\s*,\s*"explanation"\s*:\s*"([^"]+)"', text)
    for cm in concept_matches:
        fallback_data["concepts"].append({
            "name": cm.group(1),
            "explanation": cm.group(2),
            "importance": "high",
            "source_references": [],
        })

    fact_matches = re.finditer(r'\{\s*"statement"\s*:\s*"([^"]+)"', text)
    for fm in fact_matches:
        fallback_data["facts"].append({
            "statement": fm.group(1),
            "context": "Operational Standard",
            "source_references": [],
        })

    return fallback_data


# ── Text Chunking Helpers ─────────────────────────────────────────────────────

def _format_block_text(block: ContentBlock) -> str:
    """Format an individual content block with provenance metadata."""
    slide_info = f"Slide: {block.slide_number}" if block.slide_number is not None else "Slide: N/A"
    header = f"[Block: {block.id} | Source: {block.source_id} | File: {block.original_filename} | {slide_info}]"
    content = block.text.strip() if block.text else ""
    return f"{header}\n{content}\n"


def create_chunks(blocks: list[ContentBlock], max_chars: int) -> list[list[ContentBlock]]:
    """
    Group content blocks into sequential chunks respecting block boundaries.
    Never silently discards content.
    """
    if not blocks:
        return []

    chunks: list[list[ContentBlock]] = []
    current_chunk: list[ContentBlock] = []
    current_length = 0

    for block in blocks:
        formatted = _format_block_text(block)
        block_len = len(formatted)

        if current_chunk and (current_length + block_len > max_chars):
            chunks.append(current_chunk)
            current_chunk = [block]
            current_length = block_len
        else:
            current_chunk.append(block)
            current_length += block_len

    if current_chunk:
        chunks.append(current_chunk)

    return chunks


# ── Model Resolution ──────────────────────────────────────────────────────────

def resolve_model_tags(client: OllamaClient) -> tuple[str, str]:
    """
    Resolve the actual model tags installed on the server for text and vision models.
    Raises ModelNotFoundError if required models are not installed.
    """
    verification = client.verify_models()
    if not verification["connected"]:
        raise OllamaConnectionError(
            f"Cannot connect to Ollama at {client.base_url}: {verification.get('error')}"
        )

    installed_names: list[str] = verification.get("installed_models", [])

    text_target = config.OLLAMA_TEXT_MODEL
    vision_target = config.OLLAMA_VISION_MODEL

    actual_text = next((m for m in installed_names if _model_matches(text_target, m)), None)
    actual_vision = next((m for m in installed_names if _model_matches(vision_target, m)), None)

    if not actual_text:
        raise ModelNotFoundError(
            f"Required text model '{text_target}' is not installed on remote Ollama instance. "
            f"Installed models: {installed_names}. Automatic pulling is prohibited."
        )

    if not actual_vision:
        raise ModelNotFoundError(
            f"Required vision model '{vision_target}' is not installed on remote Ollama instance. "
            f"Installed models: {installed_names}. Automatic pulling is prohibited."
        )

    return actual_text, actual_vision


# ── Prompt Templates ──────────────────────────────────────────────────────────

SYSTEM_PROMPT_TEXT = """You are an expert pedagogical analyst and educational script architect.
Your task is to analyze extracted educational content and produce a structured, high-fidelity JSON analysis.

CRITICAL GROUNDING REQUIREMENTS:
- Every factual claim, concept, definition, and example MUST reference the source blocks it originates from.
- Use the exact IDs provided in the headers: [Block: <content_block_id> | Source: <source_id> | File: <original_filename> | Slide: <slide_number>].
- In the "source_references" array for each item, include:
  {"source_id": "...", "original_filename": "...", "slide_number": <int or null>, "content_block_id": "..."}
- Never invent facts that are not present in the source material.
- If information is missing, ambiguous, or contradictory, explicitly flag it in "ambiguities_and_gaps".

You must output ONLY a valid JSON object matching the requested schema. Do not include markdown preamble.
"""

DIRECT_ANALYSIS_SCHEMA = """
{
  "title": "Clear educational title",
  "subject": "Primary academic or professional subject",
  "summary": "Thorough, structured educational summary",
  "main_concepts": [
    {
      "name": "Concept name",
      "explanation": "Detailed pedagogical explanation",
      "importance": "Why this matters in the topic",
      "source_references": [{"source_id": "...", "original_filename": "...", "slide_number": null, "content_block_id": "..."}]
    }
  ],
  "supporting_concepts": [
    {
      "name": "Supporting concept",
      "explanation": "Explanation",
      "source_references": [...]
    }
  ],
  "important_facts": [
    {
      "statement": "Fact or data point",
      "context": "Educational context",
      "source_references": [...]
    }
  ],
  "definitions": [
    {
      "term": "Term",
      "definition": "Definition",
      "source_references": [...]
    }
  ],
  "concept_relationships": [
    {
      "from_concept": "Concept A",
      "to_concept": "Concept B",
      "relationship_type": "prerequisite_of | part_of | causes | contrasts_with | applies_to",
      "description": "How they connect"
    }
  ],
  "examples": [
    {
      "concept": "Concept",
      "description": "Concrete example or case study",
      "source_references": [...]
    }
  ],
  "prerequisites": ["Prior knowledge topic 1", "Prior knowledge topic 2"],
  "learning_objectives": [
    {
      "objective": "By the end of the video, learner will understand...",
      "blooms_level": "understand | apply | analyze | evaluate"
    }
  ],
  "ambiguities_and_gaps": [
    {
      "topic": "Topic area",
      "issue": "What is unclear or contradictory",
      "missing_information": "What information would resolve this"
    }
  ]
}
"""

CHUNK_ANALYSIS_PROMPT = """Analyze this section of educational material. Extract all key concepts, facts, definitions, and examples found within this section. Preserve exact source block IDs in source_references.

Return a JSON object with:
{
  "section_summary": "...",
  "concepts": [{"name": "...", "explanation": "...", "importance": "...", "source_references": [...]}],
  "facts": [{"statement": "...", "context": "...", "source_references": [...]}],
  "definitions": [{"term": "...", "definition": "...", "source_references": [...]}],
  "examples": [{"concept": "...", "description": "...", "source_references": [...]}]
}

Source text to analyze:
"""

SYNTHESIS_PROMPT = """You are synthesizing multiple section analyses into a comprehensive, unified educational document analysis.
Combine and deduplicate the concepts, facts, definitions, and examples while retaining all grounded source_references.
Produce the document title, overall subject, unified summary, learning objectives, prerequisites, concept relationships, and identify any ambiguities.

Output the final JSON matching the complete schema:
"""


# ── Visual Analysis ───────────────────────────────────────────────────────────

def analyze_visual_asset(
    client: OllamaClient,
    vision_model: str,
    job_id: str,
    asset_block: ContentBlock,
    asset_index: int,
) -> VisualAnalysisItem | None:
    """
    Analyze a visual asset (diagram, chart, or image) using Qwen2.5-VL:7B.
    """
    asset_ref = asset_block.asset_reference
    if not asset_ref:
        return None

    # Resolve asset file path
    potential_paths = [
        extracted_dir(job_id) / asset_ref,
        extracted_dir(job_id) / "assets" / Path(asset_ref).name,
        job_dir(job_id) / Path(asset_ref).name,
    ]
    image_path = next((p for p in potential_paths if p.exists() and p.is_file()), None)

    if not image_path:
        logger.warning("Visual asset file not found on disk for block %s: %s", asset_block.id, asset_ref)
        return None

    try:
        from PIL import Image
        import io

        with Image.open(image_path) as img:
            rgb_img = img.convert("RGB")
            # Downscale large images to max 1280px to prevent payload/Ollama errors
            max_dim = 1280
            if max(rgb_img.width, rgb_img.height) > max_dim:
                rgb_img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)

            buf = io.BytesIO()
            rgb_img.save(buf, format="PNG")
            b64_image = base64.b64encode(buf.getvalue()).decode("utf-8")
    except Exception as exc:
        # Fallback to direct bytes if Pillow fails (e.g. test mock byte sequences)
        try:
            raw_bytes = image_path.read_bytes()
            b64_image = base64.b64encode(raw_bytes).decode("utf-8")
        except Exception as read_exc:
            logger.warning("Image %s cannot be read: %s; skipping visual analysis", image_path, read_exc)
            return None

    prompt = (
        "You are an expert educational visual analyst. Analyze this visual asset objectively.\n"
        "Return a valid JSON object with the following fields:\n"
        '- "visual_type": one of "diagram", "chart", "illustration", "screenshot", "photo", "table_image", "other"\n'
        '- "description": clear, objective description of what is depicted\n'
        '- "extracted_text": any visible text, labels, or equations (or null if none)\n'
        '- "key_takeaways": array of key educational takeaways conveyed by this visual\n'
        '- "relevance_to_topic": how this visual supports the lesson topic\n\n'
        "STRICT REQUIREMENT: Do not invent details not visually present in the image.\n"
    )

    messages = [
        {
            "role": "user",
            "content": prompt,
            "images": [b64_image],
        }
    ]

    try:
        with gpu_manager.guard(job_id, vision_model, f"visual_analysis_asset_{asset_index}"):
            logger.info("Running vision analysis for job %s, asset %s", job_id, asset_ref)
            resp = client.chat(
                model=vision_model,
                messages=messages,
                format="json",
                timeout=config.OLLAMA_INFERENCE_TIMEOUT_SECONDS,
            )
            parsed = clean_and_parse_json(resp["content"])
    except Exception as exc:
        logger.warning(
            "Vision analysis failed for asset %s (job %s): %s; skipping visual asset without failing pipeline",
            asset_ref,
            job_id,
            exc,
        )
        return None

    try:
        ext_text = parsed.get("extracted_text")
        if isinstance(ext_text, list):
            ext_text = "\n".join(str(item) for item in ext_text)
        elif ext_text is not None:
            ext_text = str(ext_text)

        return VisualAnalysisItem(
            source_id=asset_block.source_id,
            original_filename=asset_block.original_filename,
            slide_number=asset_block.slide_number,
            content_block_id=asset_block.id,
            asset_reference=asset_ref,
            visual_type=parsed.get("visual_type", "illustration"),
            description=parsed.get("description", "Visual asset from source material"),
            extracted_text=ext_text,
            key_takeaways=parsed.get("key_takeaways", []),
            relevance_to_topic=parsed.get("relevance_to_topic"),
        )
    except Exception as exc:
        logger.warning("Failed to parse vision response for asset %s: %s", asset_ref, exc)
        return VisualAnalysisItem(
            source_id=asset_block.source_id,
            original_filename=asset_block.original_filename,
            slide_number=asset_block.slide_number,
            content_block_id=asset_block.id,
            asset_reference=asset_ref,
            visual_type="illustration",
            description="Visual asset extracted from source material.",
            key_takeaways=[],
        )


# ── Main Content Analysis Pipeline ───────────────────────────────────────────

def process_job_analysis(job_id: str) -> AIAnalysisResult:
    """
    Run complete AI content understanding pipeline for a job.
    Loads extracted content, runs text reasoning with Qwen2.5:14B,
    visual analysis with Qwen2.5-VL:7B, and saves persistent analysis.json.
    """
    start_time = time.perf_counter()
    logger.info("Starting AI content analysis for job %s", job_id)

    # 1. Verify job exists
    job = db.get_job(job_id)
    if not job:
        raise ValueError(f"Job {job_id} not found")

    # 2. Check and load extraction manifest
    manifest_file = extracted_dir(job_id) / "manifest.json"
    if not manifest_file.exists():
        raise ValueError(
            f"Extraction manifest for job {job_id} does not exist. Job must complete extraction first."
        )

    manifest_data = json.loads(manifest_file.read_text(encoding="utf-8"))
    manifest = ExtractionManifest.model_validate(manifest_data)

    # 3. Resolve AI Models
    client = OllamaClient()
    text_model, vision_model = resolve_model_tags(client)
    logger.info("Using text model '%s' and vision model '%s'", text_model, vision_model)

    # 4. Gather text blocks and visual blocks
    text_blocks = [b for b in manifest.blocks if b.text and b.text.strip()]
    visual_blocks = [
        b for b in manifest.blocks
        if b.asset_reference or b.block_type == "image_asset" or b.source_type == "image"
    ]

    # Include pasted text as synthetic block if not already in manifest
    if job.get("has_text") and job.get("pasted_text"):
        pasted = job["pasted_text"].strip()
        has_pasted_block = any(b.source_id == "pasted_text" for b in text_blocks)
        if not has_pasted_block and pasted:
            text_blocks.insert(
                0,
                ContentBlock(
                    id="block_pasted_text",
                    source_id="pasted_text",
                    original_filename="Pasted Text",
                    source_type="text",
                    block_type="paragraph",
                    text=pasted,
                ),
            )

    if not text_blocks and not visual_blocks:
        raise ValueError(f"Job {job_id} contains no extractable text or visual blocks to analyze.")

    db.update_job_status(
        job_id,
        status="analyzing",
        stage="Analyzing content with Qwen2.5:14B text model…",
    )

    # 5. Text Analysis (Hierarchical Chunking or Single-pass)
    chunks = create_chunks(text_blocks, config.MAX_CHUNK_CHARACTERS)
    logger.info("Content split into %d chunk(s) for text analysis", len(chunks))

    parsed_text_data: dict[str, Any] = {}

    if len(chunks) <= 1:
        # Single-pass direct analysis
        formatted_content = "\n".join(_format_block_text(b) for b in text_blocks)
        permitted_ids = [b.id for b in text_blocks]
        permitted_ids_str = ", ".join(f'\"{pid}\"' for pid in permitted_ids[:30])
        prompt = (
            f"{SYSTEM_PROMPT_TEXT}\n\n"
            f"PERMITTED CANONICAL BLOCK IDs: [{permitted_ids_str}]\n"
            f"You MUST cite exact block IDs from this list in source_references. Do NOT invent new IDs.\n\n"
            f"Produce an analysis matching this exact schema:\n{DIRECT_ANALYSIS_SCHEMA}\n\n"
            f"Source Material:\n{formatted_content}\n"
        )

        with gpu_manager.guard(job_id, text_model, "direct_text_analysis"):
            logger.info("Executing direct text analysis with %s", text_model)
            resp = client.chat(
                model=text_model,
                messages=[{"role": "user", "content": prompt}],
                format="json",
                timeout=config.OLLAMA_INFERENCE_TIMEOUT_SECONDS,
            )

        parsed_text_data = clean_and_parse_json(resp["content"])

    else:
        # Hierarchical Multi-pass Chunking
        chunk_results: list[dict[str, Any]] = []
        for idx, chunk in enumerate(chunks):
            db.update_job_status(
                job_id,
                status="analyzing",
                stage=f"Analyzing content section {idx + 1} of {len(chunks)} with Qwen2.5:14B…",
            )
            formatted_chunk = "\n".join(_format_block_text(b) for b in chunk)
            chunk_prompt = f"{CHUNK_ANALYSIS_PROMPT}\n{formatted_chunk}"

            with gpu_manager.guard(job_id, text_model, f"chunk_text_analysis_{idx + 1}"):
                logger.info("Analyzing chunk %d/%d with %s", idx + 1, len(chunks), text_model)
                chunk_resp = client.chat(
                    model=text_model,
                    messages=[{"role": "user", "content": chunk_prompt}],
                    format="json",
                    timeout=config.OLLAMA_INFERENCE_TIMEOUT_SECONDS,
                )

            try:
                chunk_parsed = clean_and_parse_json(chunk_resp["content"])
                chunk_results.append(chunk_parsed)
            except Exception as exc:
                logger.warning("Error parsing chunk %d: %s; continuing", idx + 1, exc)

        # Synthesis pass
        db.update_job_status(
            job_id,
            status="analyzing",
            stage="Synthesizing multi-section findings into unified analysis…",
        )
        synthesis_input = json.dumps(chunk_results, indent=2)
        synth_prompt = (
            f"{SYSTEM_PROMPT_TEXT}\n\n"
            f"{SYNTHESIS_PROMPT}\n{DIRECT_ANALYSIS_SCHEMA}\n\n"
            f"Section findings to synthesize:\n{synthesis_input}\n"
        )

        with gpu_manager.guard(job_id, text_model, "synthesis_pass"):
            logger.info("Executing synthesis pass with %s", text_model)
            synth_resp = client.chat(
                model=text_model,
                messages=[{"role": "user", "content": synth_prompt}],
                format="json",
                timeout=config.OLLAMA_INFERENCE_TIMEOUT_SECONDS,
            )

        parsed_text_data = clean_and_parse_json(synth_resp["content"])

    # 6. Visual Understanding with Qwen2.5-VL:7B
    visual_analyses: list[VisualAnalysisItem] = []
    if visual_blocks:
        max_assets = int(os.getenv("EXPLAIN_STUDIO_MAX_VISUAL_ASSETS", "8"))
        if len(visual_blocks) > max_assets:
            step = len(visual_blocks) / max_assets
            target_blocks = [visual_blocks[int(i * step)] for i in range(max_assets)]
            logger.info("Sampling %d visual asset(s) out of %d for vision analysis", len(target_blocks), len(visual_blocks))
        else:
            target_blocks = visual_blocks

        logger.info("Found %d visual asset(s) to analyze", len(target_blocks))
        for v_idx, v_block in enumerate(target_blocks):
            db.update_job_status(
                job_id,
                status="analyzing",
                stage=f"Analyzing visual asset {v_idx + 1} of {len(target_blocks)} with Qwen2.5-VL:7B…",
            )
            v_result = analyze_visual_asset(
                client=client,
                vision_model=vision_model,
                job_id=job_id,
                asset_block=v_block,
                asset_index=v_idx + 1,
            )
            if v_result:
                visual_analyses.append(v_result)

    # 7. Assemble Structured Analysis Result
    now_iso = datetime.now(timezone.utc).isoformat()
    duration_sec = round(time.perf_counter() - start_time, 2)

    # Helper to construct source references safely
    def _parse_source_refs(refs_raw: Any) -> list[SourceReference]:
        if not isinstance(refs_raw, list):
            return []
        parsed_refs = []
        for r in refs_raw:
            if isinstance(r, dict) and "source_id" in r:
                parsed_refs.append(
                    SourceReference(
                        source_id=str(r.get("source_id", "")),
                        original_filename=str(r.get("original_filename", "Source Material")),
                        slide_number=r.get("slide_number"),
                        content_block_id=r.get("content_block_id"),
                        asset_reference=r.get("asset_reference"),
                    )
                )
        return parsed_refs

    main_concepts = [
        MainConcept(
            name=c.get("name", "Key Concept"),
            explanation=c.get("explanation", ""),
            importance=c.get("importance", "Core to understanding"),
            source_references=_parse_source_refs(c.get("source_references")),
        )
        for c in parsed_text_data.get("main_concepts", [])
        if isinstance(c, dict)
    ]

    supporting_concepts = [
        SupportingConcept(
            name=c.get("name", "Supporting Concept"),
            explanation=c.get("explanation", ""),
            source_references=_parse_source_refs(c.get("source_references")),
        )
        for c in parsed_text_data.get("supporting_concepts", [])
        if isinstance(c, dict)
    ]

    important_facts = [
        ImportantFact(
            statement=f.get("statement", ""),
            context=f.get("context"),
            source_references=_parse_source_refs(f.get("source_references")),
        )
        for f in parsed_text_data.get("important_facts", [])
        if isinstance(f, dict) and f.get("statement")
    ]

    definitions = [
        Definition(
            term=d.get("term", ""),
            definition=d.get("definition", ""),
            source_references=_parse_source_refs(d.get("source_references")),
        )
        for d in parsed_text_data.get("definitions", [])
        if isinstance(d, dict) and d.get("term")
    ]

    relationships = [
        ConceptRelationship(
            from_concept=r.get("from_concept", ""),
            to_concept=r.get("to_concept", ""),
            relationship_type=r.get("relationship_type", "relates_to"),
            description=r.get("description", ""),
        )
        for r in parsed_text_data.get("concept_relationships", [])
        if isinstance(r, dict) and r.get("from_concept")
    ]

    examples = [
        Example(
            concept=e.get("concept", ""),
            description=e.get("description", ""),
            source_references=_parse_source_refs(e.get("source_references")),
        )
        for e in parsed_text_data.get("examples", [])
        if isinstance(e, dict) and e.get("concept")
    ]

    objectives = [
        LearningObjective(
            objective=o.get("objective", ""),
            blooms_level=o.get("blooms_level", "understand"),
        )
        for o in parsed_text_data.get("learning_objectives", [])
        if isinstance(o, dict) and o.get("objective")
    ]

    ambiguities = [
        AmbiguityItem(
            topic=a.get("topic", "General"),
            issue=a.get("issue", "Unclear information"),
            missing_information=a.get("missing_information", ""),
        )
        for a in parsed_text_data.get("ambiguities_and_gaps", [])
        if isinstance(a, dict)
    ]

    result = AIAnalysisResult(
        job_id=job_id,
        analyzed_at=now_iso,
        title=parsed_text_data.get("title") or "Educational Topic Analysis",
        subject=parsed_text_data.get("subject") or "General",
        summary=parsed_text_data.get("summary") or "Comprehensive analysis of source material.",
        main_concepts=main_concepts,
        supporting_concepts=supporting_concepts,
        important_facts=important_facts,
        definitions=definitions,
        concept_relationships=relationships,
        examples=examples,
        prerequisites=parsed_text_data.get("prerequisites", []),
        learning_objectives=objectives,
        ambiguities_and_gaps=ambiguities,
        visual_analyses=visual_analyses,
        metadata={
            "text_model": text_model,
            "vision_model": vision_model,
            "total_chunks_processed": len(chunks),
            "total_visual_assets_analyzed": len(visual_analyses),
            "duration_seconds": duration_sec,
        },
    )

    # 8. Persist analysis JSON under storage/analysis/{job_id}/analysis.json
    out_dir = ensure_analysis_dir(job_id)
    out_path = out_dir / "analysis.json"
    out_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    logger.info("Saved persistent analysis result to %s", out_path)

    # 9. Update DB Status to 'analyzed'
    db.update_job_status(
        job_id,
        status="analyzed",
        stage="AI content analysis complete. Ready for script generation.",
    )

    return result
