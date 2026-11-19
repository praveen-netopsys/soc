"""Visual Presentation Analyser powered by Qwen2.5-VL:7B.

Inspects rendered slide images to extract:
- Text embedded in diagrams or images missed by basic extraction
- Chart labels, numerical data points, and trends
- Diagram workflows, hierarchical flowcharts, and component relationships
- Visual safety icons, warning symbols, and callout badges
"""
from __future__ import annotations

import base64
import io
import json
import logging
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ... import config
from ..client import OllamaClient
from ..gpu import gpu_manager
from ..analyzer import clean_and_parse_json

logger = logging.getLogger("explain_studio.ai.multilingual.visual_analyzer")


class SlideVisualAnalysis(BaseModel):
    slide_number: int
    has_visual_elements: bool = False
    visual_type: str = "text"  # 'diagram' | 'chart' | 'process_flow' | 'table' | 'icon_dense' | 'text'
    visual_summary: str = ""
    charts_and_data: list[str] = Field(default_factory=list)
    process_flow: list[str] = Field(default_factory=list)
    icons_and_symbols: list[str] = Field(default_factory=list)
    unextracted_text: list[str] = Field(default_factory=list)


def should_inspect_slide_visually(slide_info: dict[str, Any]) -> bool:
    """
    Intelligently determine if a slide requires visual inspection with Qwen2.5-VL.
    Inspect slides with:
    - Structured tables
    - Embedded images, photos, drawings, or maps
    - Sparse text slides (pure photo/diagram slide or sparse callout)
    Do NOT inspect ordinary text-only slides where all bullets and text are already extracted.
    """
    # 1. Structured tables
    if slide_info.get("tables") and len(slide_info["tables"]) > 0:
        return True

    # 2. Embedded images
    if slide_info.get("images") and len(slide_info["images"]) > 0:
        return True

    bullets = slide_info.get("bullets", [])
    title = (slide_info.get("title") or "").lower()

    # 3. Slide has no text or minimal text (pure photo/diagram slide or sparse callout)
    if len(bullets) <= 1:
        return True

    # 4. Slide text explicitly indicates a graphic diagram, chart, matrix, or flow
    combined_text = (title + " " + " ".join(bullets)).lower()
    visual_cues = [r"\bchart\b", r"\bdiagram\b", r"\bgraph\b", r"\bflowchart\b", r"\bprocess flow\b", r"\bmap\b", r"\bpyramid\b", r"\bmatrix\b"]
    if len(bullets) <= 2 and any(re.search(cue, combined_text) for cue in visual_cues):
        return True

    return False


def encode_image_for_vision(image_path: Path, max_dim: int = 768) -> str | None:
    """Load image, resize to compact dimension (768px JPEG 85%), and return base64 string."""
    if not image_path.exists() or not image_path.is_file():
        return None
    try:
        from PIL import Image
        with Image.open(image_path) as img:
            rgb_img = img.convert("RGB")
            if max(rgb_img.width, rgb_img.height) > max_dim:
                rgb_img.thumbnail((max_dim, max_dim), Image.Resampling.BILINEAR)
            buf = io.BytesIO()
            rgb_img.save(buf, format="JPEG", quality=85, optimize=True)
            return base64.b64encode(buf.getvalue()).decode("utf-8")
    except Exception as exc:
        logger.warning("Failed to encode image %s for vision: %s", image_path, exc)
        return None


async def analyze_slide_visuals(
    slide_number: int,
    slide_image_path: Path,
    slide_info: dict[str, Any],
    job_id: str = "multilingual_job",
    client: OllamaClient | None = None,
) -> SlideVisualAnalysis:
    """
    Execute visual inspection of a rendered slide PNG using Qwen2.5-VL:7B.
    If Ollama is offline or times out, gracefully returns an estimated visual profile.
    """
    if not should_inspect_slide_visually(slide_info):
        return SlideVisualAnalysis(
            slide_number=slide_number,
            has_visual_elements=False,
            visual_type="text",
            visual_summary=f"Slide {slide_number} is primarily text-based.",
        )

    b64_img = encode_image_for_vision(slide_image_path)
    if not b64_img:
        return SlideVisualAnalysis(
            slide_number=slide_number,
            has_visual_elements=bool(slide_info.get("images") or slide_info.get("tables")),
            visual_type="table" if slide_info.get("tables") else "diagram",
            visual_summary=f"Slide {slide_number} contains visual elements.",
        )

    ollama = client or OllamaClient()
    vision_model = config.OLLAMA_VISION_MODEL

    prompt = (
        "You are an expert educational presentation analyst. Analyze this rendered PowerPoint slide.\n"
        "Identify and extract all visual elements concisely.\n"
        "Return a JSON object with exactly these fields:\n"
        '- "visual_type": one of "diagram", "chart", "process_flow", "table", "icon_dense", "text"\n'
        '- "visual_summary": a 1-2 sentence explanation of the visual layout and core message\n'
        '- "charts_and_data": array of data points, trends, or numerical facts visible in charts/tables\n'
        '- "process_flow": array of steps or sequential relationships indicated by arrows or boxes\n'
        '- "icons_and_symbols": array of safety badges, warning icons, or status indicators\n'
        '- "unextracted_text": array of any important text or labels present in drawings that a text parser might miss\n'
        "STRICT: Respond ONLY with valid JSON."
    )

    messages = [
        {
            "role": "user",
            "content": prompt,
            "images": [b64_img],
        }
    ]

    try:
        with gpu_manager.guard(job_id, vision_model, f"visual_slide_{slide_number}", timeout=10.0):
            resp = ollama.chat(
                model=vision_model,
                messages=messages,
                format="json",
                timeout=config.OLLAMA_INFERENCE_TIMEOUT_SECONDS,
            )
            parsed = clean_and_parse_json(resp["content"])
            return SlideVisualAnalysis(
                slide_number=slide_number,
                has_visual_elements=True,
                visual_type=parsed.get("visual_type", "diagram"),
                visual_summary=parsed.get("visual_summary", f"Slide {slide_number} visual content analyzed."),
                charts_and_data=parsed.get("charts_and_data") or [],
                process_flow=parsed.get("process_flow") or [],
                icons_and_symbols=parsed.get("icons_and_symbols") or [],
                unextracted_text=parsed.get("unextracted_text") or [],
            )
    except Exception as exc:
        logger.info(
            "Qwen2.5-VL inference skipped or timed out for slide %d (%s); using heuristic visual metadata",
            slide_number,
            exc,
        )
        # Graceful fallback: construct from metadata
        charts = []
        if slide_info.get("tables"):
            charts.append("Structured table data present on slide.")
        if slide_info.get("images"):
            charts.append(f"{len(slide_info['images'])} embedded visual asset(s).")

        return SlideVisualAnalysis(
            slide_number=slide_number,
            has_visual_elements=True,
            visual_type="table" if slide_info.get("tables") else "diagram",
            visual_summary=f"Visual layout for slide {slide_number}: contains structured diagrams and key operational indicators.",
            charts_and_data=charts,
        )
