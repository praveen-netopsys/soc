"""Storyboard Generator for Explain Studio — 2D Corporate Training Video Production.

Translates corporate narration scripts and lesson plans into structured,
executable 2D animation scenes with articulated characters, industrial equipment,
and factory floor environments, grounded in presentation slides.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from ...assets.web_fetcher import resolve_scene_image
from ..analyzer import clean_and_parse_json
from ..client import OllamaClient
from ..gpu import gpu_manager
from ..models import (
    AIAnalysisResult,
    EducationalPlan,
    NarrationScript,
    SourceReference,
    Storyboard,
    StoryboardScene,
)

logger = logging.getLogger("explain_studio.ai.director.storyboarder")

STORYBOARD_PROMPT_TEMPLATE = """You are an expert 2D corporate training animation director.
Your task is to transform this narration script and lesson plan into an animated 2D training video storyboard with articulated characters, industrial equipment, safety procedures, and factory floor environments.

TOPIC: {title}
AUDIENCE: {audience}

SCRIPT SEGMENTS:
{segments_text}

AVAILABLE VISUAL ASSETS FROM SOURCE FILES:
{visuals_text}

ANIMATION INSTRUCTIONS & ART-DIRECTION BENCHMARK:
- Create real 2D animated scenes (>80% runtime animated graphics, NOT static slides or text walls).
- ART-DIRECTION BENCHMARK: Use reference and benchmark images STRICTLY as an art-direction, staging, visual tone, and vector styling benchmark — NEVER insert benchmark or reference images as static images or textures into final videos.
- Follow the 4-part industrial safety storytelling benchmark:
  * Stage 1 — Introduce the hazard: Worker approaching operating machinery, moving machine parts, flashing amber beacons, overhead gantry crane hooks.
  * Stage 2 — Explain the danger: Clearly marked floor zones (Green 'SAFE ZONE' walkway vs Red 'DANGER ZONE' hazard areas), telemetry cyan wireframe bounding boxes on mobile equipment (forklifts/cranes), animated conveyors and robotic arms.
  * Stage 3 — Demonstrate protection: 2D illustrated character wearing full PPE (hard hats, safety glasses, high-vis shirt/vest, boots, gloves).
  * Stage 4 — Show correct behaviour: Safety barriers with yellow/black chevron hazard tape, caution cones/stanchions, disciplined personnel following protocols.
- Each scene must feature articulated human characters (Worker, Supervisor, Driver, Visitor, Inspector), equipment, or workplace environments.
- Choose from these primary corporate training animation types:
  * "ppe_inspection_walkthrough" (Articulated worker demonstrating hard hat, safety glasses, high-vis vest, steel-toe boots with callouts)
  * "vehicle_inspection_routine" (Driver inspecting prime mover, trailer, tires, fluid levels, wheel chocks, lights)
  * "traffic_rules_and_speed_limits" (Factory roadways, green pedestrian walkway, forklift vs truck right-of-way, 15 km/h speed signs)
  * "loading_dock_and_cargo_securing" (Loading bay, wheel chocks, twistlocks, cargo straps, exclusion zones)
  * "workstation_5s_transformation" (Before/after workbench, tool shadow board, red tagging, Seiri/Seiton/Seiso)
  * "lockout_tagout_procedure" (Step-by-step LOTO, isolation of breakers/valves, safety padlock, danger tags)
  * "emergency_stop_and_evacuation" (Pulsing E-Stop button, machinery halt, emergency exit route to assembly point)
  * "hazard_identification_and_stop" (Identifying overhead hazards, hot metal/slag, crane operations, stop-work protocol)
- Structure on-screen text as 3-4 bullet points, plus an overarching takeaway and key metric if applicable.
- Choose layout_type from: "checklist_walkthrough", "dos_and_donts", "procedure_steps", "standards_callout", "hazard_alert", "five_s_transformation", "ppe_inspection", "equipment_action".

Output ONLY valid JSON matching this schema:
{{
  "job_id": "{job_id}",
  "title": "{title}",
  "scenes": [
    {{
      "scene_id": "scene_1",
      "module_id": "mod_1",
      "scene_purpose": "hook | concept_introduction | procedure_demonstration | rule_enforcement | summary_recap",
      "narration_text": "Spoken sentences from script segment verbatim...",
      "visual_description": "Detailed description of 2D animation, articulated characters, actions, and environment...",
      "animation_type": "ppe_inspection_walkthrough | vehicle_inspection_routine | traffic_rules_and_speed_limits | loading_dock_and_cargo_securing | workstation_5s_transformation | lockout_tagout_procedure | emergency_stop_and_evacuation | hazard_identification_and_stop",
      "on_screen_text": ["Primary Header", "Point 1", "Point 2", "Point 3"],
      "visual_asset_references": ["filename or asset id"],
      "source_references": [],
      "estimated_visual_duration": 15.0,
      "transition_instructions": "fade | slide_left",
      "key_points": ["Point 1", "Point 2", "Point 3"],
      "key_metric": "Optional metric like '100% PPE COMPLIANT' or '15 KM/H LIMIT'",
      "takeaway": "Key safety rule or takeaway",
      "layout_type": "checklist_walkthrough | dos_and_donts | procedure_steps | standards_callout | hazard_alert | five_s_transformation | ppe_inspection | equipment_action"
    }}
  ]
}}
"""


def _detect_layout_and_metric(slide_title: str, bullets: list[str]) -> tuple[str, str, str]:
    """Inspects text content to determine the most effective visual layout and highlight metric."""
    text_corpus = (slide_title + " " + " ".join(bullets)).lower()

    # 1. Detect metric
    metric_match = re.search(r'(\d+(?:\.\d+)?\s*(?:km\s*/\s*h|km/h|mins?|minutes?|%|seconds?|secs?|hrs?|hours?|m|points?))', text_corpus)
    metric = metric_match.group(1).upper() if metric_match else ""

    # 2. Detect layout
    if any(k in text_corpus for k in ["stop", "think", "observe", "proceed", "step 1", "step 2", "cycle check", "jcc", "steps"]):
        layout = "procedure_steps"
    elif any(k in text_corpus for k in ["do ", "don't", "never", "prohibit", "golden rule", "permissible", "unsafe", "smoking", "camera", "photo"]):
        layout = "dos_and_donts"
    elif metric or any(k in text_corpus for k in ["speed limit", "cost", "waste", "statistics", "35 min", "30%", "rate", "15 km"]):
        layout = "standards_callout"
    elif any(k in text_corpus for k in ["lockout", "tagout", "loto", "padlock", "zero energy", "switch", "breaker"]):
        layout = "equipment_action"
    elif any(k in text_corpus for k in ["truck", "trailer", "driver", "twistlock", "chock", "pre-trip", "forklift"]):
        layout = "equipment_action"
    elif any(k in text_corpus for k in ["hazard", "emergency", "evacuation", "spill", "e-stop", "alarm", "fire", "assembly", "muster"]):
        layout = "hazard_alert"
    elif any(k in text_corpus for k in ["sort", "seiri", "red tag", "seiton", "order", "seiso", "shine"]):
        layout = "five_s_transformation"
    elif any(k in text_corpus for k in ["ppe", "helmet", "glasses", "vest", "boots", "goggles"]):
        layout = "ppe_inspection"
    else:
        layout = "checklist_walkthrough"

    # 3. Takeaway
    takeaway = ""
    for b in bullets:
        if any(w in b.lower() for w in ["never", "always", "must", "golden rule", "protocol", "report", "eliminate", "ensure"]):
            takeaway = b.strip()
            break
    if not takeaway and bullets:
        takeaway = bullets[0].strip()

    return layout, metric, takeaway


def _detect_scene_character_and_theme(
    title: str,
    bullets: list[str],
    idx: int = 0,
) -> tuple[str, str, str, str]:
    """Detect dynamic visual theme, character role, character action, and equipment type."""
    text_corpus = (title + " " + " ".join(bullets)).lower()

    # 1. Visual Theme
    if any(k in text_corpus for k in ["zero harm", "environment", "health", "sustainab", "culture", "well-being"]):
        theme = "environmental_zeroharm"
    elif any(k in text_corpus for k in ["speed", "traffic", "forklift", "truck", "driver", "vehicle", "dock", "cargo", "trailer"]):
        theme = "warehouse_logistics"
    elif any(k in text_corpus for k in ["emergency", "stop", "hazard", "loto", "lockout", "evacuation", "fire", "alarm", "assembly"]):
        theme = "emergency_critical_loto"
    elif any(k in text_corpus for k in ["5s", "sort", "seiri", "order", "clean", "shine", "machine", "standardize"]):
        theme = "plant_manufacturing"
    elif any(k in text_corpus for k in ["visitor", "orientation", "induction", "escort", "guest"]):
        theme = "corporate_compliance"
    else:
        themes_cycle = ["safety_high_vis", "corporate_compliance", "emergency_critical_loto", "environmental_zeroharm"]
        theme = themes_cycle[idx % len(themes_cycle)]

    # 2. Character Role
    if any(k in text_corpus for k in ["driver", "truck", "cab", "pre-trip", "cargo"]):
        role = "driver"
    elif any(k in text_corpus for k in ["supervisor", "manager", "audit", "protocol", "compliance", "inspection"]):
        role = "supervisor"
    elif any(k in text_corpus for k in ["visitor", "contractor", "escort", "guest"]):
        role = "visitor"
    else:
        role = "worker"

    # 3. Character Action
    if any(k in text_corpus for k in ["walk", "crossing", "route", "evacuat"]):
        action = "walk"
    elif any(k in text_corpus for k in ["stop", "halt", "emergency", "danger"]):
        action = "stop"
    elif any(k in text_corpus for k in ["clean", "wipe", "shine"]):
        action = "clean"
    elif any(k in text_corpus for k in ["point", "standard", "takeaway", "critical", "never"]):
        action = "point"
    else:
        action = "inspect"

    # 4. Equipment Type (Domain-specific, never forced onto unrelated topics)
    if any(k in text_corpus for k in ["truck", "trailer", "container", "chassis", "prime mover"]):
        equip = "prime_mover"
    elif any(k in text_corpus for k in ["forklift", "mast", "pallet"]):
        equip = "forklift"
    elif any(k in text_corpus for k in ["crane", "overhead", "suspended", "lifting", "gantry"]):
        equip = "overhead_crane"
    elif any(k in text_corpus for k in ["5s", "sort", "workbench", "pegboard", "shadow board"]):
        equip = "workbench"
    elif any(k in text_corpus for k in ["visitor", "turnstile", "gate", "badge", "escort", "kiosk"]):
        equip = "turnstile"
    elif any(k in text_corpus for k in ["loto", "lockout", "switch", "isolator", "breaker"]):
        equip = "disconnect_switch"
    else:
        equip = "none"

    return theme, role, action, equip


def _load_slides_content(job_id: str) -> dict[int, dict[str, Any]]:
    """Load extraction manifest to get exact slide bullets, headings, and image file paths."""
    from ...storage import extracted_dir
    slides_content: dict[int, dict[str, Any]] = {}
    if not job_id:
        return slides_content
    manifest_path = extracted_dir(job_id) / "manifest.json"
    if manifest_path.exists():
        try:
            mdata = json.loads(manifest_path.read_text(encoding="utf-8"))
            for b in mdata.get("blocks", []):
                s_num = b.get("slide_number")
                if s_num is not None and isinstance(s_num, int) and s_num > 0:
                    if s_num not in slides_content:
                        slides_content[s_num] = {"title": "", "bullets": [], "images": []}
                    txt = (b.get("text") or "").strip()
                    # Sanitize soft breaks, vertical tabs, and control characters
                    txt = re.sub(r'[\x00-\x1f\x7f-\x9f]+', ' ', txt)
                    txt = re.sub(r'\s+', ' ', txt).strip()

                    asset = b.get("asset_reference")
                    if asset:
                        img_p = manifest_path.parent / asset
                        if img_p.exists():
                            slides_content[s_num]["images"].append(str(img_p))
                    if txt:
                        if txt in ["NATSTEEL HOLDINGS P/L", "You are here"] or (txt.isdigit() and len(txt) <= 2):
                            continue
                        is_table_or_noise = (
                            "|" in txt
                            or any(noise in txt.lower() for noise in ["rating (pt)", "nobody makes a mess", "how much score", "score do you give"])
                            or (txt.isdigit() and len(txt) <= 2)
                        )
                        if b.get("block_type") == "heading" and not is_table_or_noise and len(txt) > 3:
                            slides_content[s_num]["title"] = txt
                        elif not slides_content[s_num]["title"] and len(txt) > 3 and not is_table_or_noise:
                            slides_content[s_num]["title"] = txt
                        else:
                            clean_b = re.sub(r'^[•\-\*\d\.\)\s]+', '', txt).strip()
                            if clean_b and len(clean_b) > 4 and clean_b not in slides_content[s_num]["bullets"]:
                                slides_content[s_num]["bullets"].append(clean_b)

            # Merge sentence fragments in bullets
            for s_num, s in slides_content.items():
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
        except Exception as e:
            logger.debug("Could not read manifest slides for storyboarder: %s", e)
    return slides_content


def _get_bullets_for_scene(
    slides_content: dict[int, dict[str, Any]],
    slide_numbers: list[int],
    fallback_title: str,
    fallback_bullets: list[str],
) -> tuple[str, list[str]]:
    """Extract ordered bullets and title for a specific set of slides."""
    extracted_title = ""
    extracted_bullets: list[str] = []
    for s_num in slide_numbers:
        if s_num in slides_content:
            s_info = slides_content[s_num]
            if not extracted_title and s_info.get("title"):
                extracted_title = s_info["title"]
            for b in s_info.get("bullets", []):
                if b not in extracted_bullets:
                    extracted_bullets.append(b)

    title = extracted_title or fallback_title
    bullets = extracted_bullets if extracted_bullets else fallback_bullets
    return title, bullets


def _generate_fallback_storyboard(
    job_id: str,
    analysis: AIAnalysisResult,
    plan: EducationalPlan,
    script: NarrationScript,
) -> Storyboard:
    """Generate slide-grounded, diverse 2D corporate training storyboard scenes matching each plan module."""
    scenes: list[StoryboardScene] = []
    slides_content = _load_slides_content(job_id)

    for idx, mod in enumerate(plan.modules):
        seg = next((s for s in script.segments if s.module_id == mod.module_id), None)
        if seg is None and idx < len(script.segments):
            seg = script.segments[idx]
        slide_nums = mod.slide_numbers if mod.slide_numbers else [idx + 1]

        # Gather real bullets and title for this scene
        scene_title, scene_bullets = _get_bullets_for_scene(
            slides_content,
            slide_nums,
            fallback_title=mod.title if mod else f"Lesson {idx + 1}",
            fallback_bullets=mod.important_facts if mod and mod.important_facts else [],
        )

        # Enforce that title is never empty or None
        if not scene_title or scene_title.lower() == "none":
            scene_title = mod.title

        # Resolve primary image asset directly from this module's slides
        # NOTE: Benchmark images and user uploads are art-direction references only, never inserted as static images.
        img_str = ""
        for s_num in slide_nums:
            if s_num in slides_content and slides_content[s_num].get("images"):
                candidate = slides_content[s_num]["images"][0]
                if not any(b_tag in candidate.lower() for b_tag in [".user_uploaded", "media_", "benchmark", "reference_"]):
                    img_str = candidate
                    break
        if not img_str:
            resolved_img = resolve_scene_image(
                scene_title=scene_title,
                key_points=scene_bullets,
                job_id=job_id,
                slide_numbers=slide_nums,
            )
            if resolved_img:
                candidate = str(resolved_img.resolve())
                if not any(b_tag in candidate.lower() for b_tag in [".user_uploaded", "media_", "benchmark", "reference_"]):
                    img_str = candidate

        # Fallback to module facts if slide extraction had no bullets
        if not scene_bullets and mod and mod.important_facts:
            scene_bullets = mod.important_facts[:]
        if not scene_bullets and mod and mod.target_concepts:
            scene_bullets = [f"Focus on {c}" for c in mod.target_concepts]
        if not scene_bullets:
            scene_bullets = [
                "Follow all verified workplace safety procedures strictly.",
                "Maintain complete vigilance and report any observed hazards immediately.",
                "Ensure standard operational protocols are verified before task execution."
            ]

        # Detect layout, metric, and takeaway
        layout_type, metric, takeaway = _detect_layout_and_metric(scene_title, scene_bullets)

        # Map layout_type and content to animation_type
        corpus = (scene_title + " " + " ".join(scene_bullets)).lower()
        if any(k in corpus for k in ["ppe", "helmet", "glasses", "vest", "boots", "protective equipment"]):
            anim_type = "ppe_inspection_walkthrough"
        elif any(k in corpus for k in ["assembly", "muster", "evacuat", "emergency", "fire"]):
            anim_type = "emergency_stop_and_evacuation"
        elif any(k in corpus for k in ["hazard", "crane", "lifting", "molten", "falling", "risk"]):
            anim_type = "hazard_identification_and_stop"
        elif any(k in corpus for k in ["camera", "smoking", "phone", "prohibit", "rule", "restriction"]):
            anim_type = "dos_and_donts"
        elif any(k in corpus for k in ["walkway", "crossing", "speed", "traffic", "road"]):
            anim_type = "traffic_rules_and_speed_limits"
        elif any(k in corpus for k in ["5s", "sort", "seiri", "shine", "shadow board", "clutter"]):
            anim_type = "workstation_5s_transformation"
        elif any(k in corpus for k in ["lockout", "tagout", "loto", "switch", "isolator"]):
            anim_type = "lockout_tagout_procedure"
        elif any(k in corpus for k in ["truck", "trailer", "driver", "chassis"]):
            anim_type = "vehicle_inspection_routine"
        elif any(k in corpus for k in ["dock", "bay", "cargo"]):
            anim_type = "loading_dock_and_cargo_securing"
        else:
            anim_type = "ppe_inspection_walkthrough" if idx == 0 else "hazard_identification_and_stop"

        purpose = "concept_introduction" if idx == 0 else ("summary_recap" if idx == len(script.segments) - 1 else "mechanism_breakdown")
        visual_desc = f"Articulated corporate 2D animation showing {layout_type.replace('_', ' ')} with visual cards for {scene_title}."

        matched_assets = []
        if analysis.visual_analyses:
            asset = analysis.visual_analyses[idx % len(analysis.visual_analyses)]
            matched_assets.append(asset.asset_reference)

        theme, role, action, equip = _detect_scene_character_and_theme(scene_title, scene_bullets, idx)

        scenes.append(
            StoryboardScene(
                scene_id=seg.scene_id if seg else f"scene_{idx + 1}",
                module_id=mod.module_id,
                scene_purpose=purpose,
                narration_text=seg.narration_text if seg else f"Lesson {idx + 1} training.",
                visual_description=visual_desc,
                animation_type=anim_type,
                on_screen_text=[scene_title] + scene_bullets[:3],
                visual_asset_references=matched_assets,
                source_references=seg.source_references if seg else [],
                estimated_visual_duration=max(10.0, (seg.estimated_duration_seconds + 1.5) if seg else 15.0),
                transition_instructions="fade" if idx == 0 else "slide_left",
                key_points=scene_bullets[:4],
                key_metric=metric or "100% COMPLIANT",
                takeaway=takeaway or scene_bullets[0],
                layout_type=layout_type,
                image_path=img_str,
                visual_theme=theme,
                character_role=role,
                character_action=action,
                equipment_type=equip,
            )
        )

    total_est = sum(s.estimated_visual_duration for s in scenes)
    return Storyboard(
        job_id=job_id,
        title=analysis.title,
        total_scenes=len(scenes),
        estimated_total_duration_seconds=total_est,
        scenes=scenes,
    )


def generate_storyboard(
    analysis: AIAnalysisResult,
    plan: EducationalPlan,
    script: NarrationScript,
    client: OllamaClient,
    text_model: str,
    job_id: str,
) -> Storyboard:
    """Generate diverse 2D corporate training animation storyboard using Qwen2.5:14B or fallback."""
    segments_text = "\n".join(
        f"- Scene {s.scene_id} ({s.module_id}) [{s.speaker_tone}]: {s.narration_text[:120]}... (~{s.estimated_duration_seconds:.0f}s)"
        for s in script.segments
    )

    visuals_text = "No diagrams extracted from source; compose procedurally."
    if analysis.visual_analyses:
        visuals_text = "\n".join(
            f"- Asset: {v.asset_reference} (Slide {v.slide_number or 'N/A'}): {v.description[:100]} | Type: {v.visual_type}"
            for v in analysis.visual_analyses[:6]
        )

    prompt = STORYBOARD_PROMPT_TEMPLATE.format(
        job_id=job_id,
        title=analysis.title,
        audience=plan.audience_level,
        segments_text=segments_text,
        visuals_text=visuals_text,
    )

    try:
        with gpu_manager.guard(job_id, text_model, "storyboard_generator"):
            logger.info("Executing storyboard generator with model %s for job %s", text_model, job_id)
            resp = client.chat(
                model=text_model,
                messages=[{"role": "user", "content": prompt}],
                format="json",
            )

        data = clean_and_parse_json(resp["content"])
        raw_scenes = data.get("scenes", [])
        scenes: list[StoryboardScene] = []
        slides_content = _load_slides_content(job_id)

        for idx, sc in enumerate(raw_scenes):
            if isinstance(sc, dict):
                sid = sc.get("scene_id") or f"scene_{idx + 1}"
                mod_id = sc.get("module_id") or (plan.modules[idx].module_id if idx < len(plan.modules) else f"mod_{idx + 1}")
                mod = next((m for m in plan.modules if m.module_id == mod_id), None)
                s_nums = mod.slide_numbers if mod else [idx + 1]

                # Ensure title and key points are set
                s_title = sc.get("title") or (mod.title if mod else f"Lesson {idx + 1}")
                k_points = sc.get("key_points") or sc.get("on_screen_text", [])[1:4]
                if not k_points and mod and mod.important_facts:
                    k_points = mod.important_facts[:3]

                # Resolve image from slide numbers if missing
                img_path = sc.get("image_path", "")
                if img_path and any(b_tag in img_path.lower() for b_tag in [".user_uploaded", "media_", "benchmark", "reference_"]):
                    img_path = ""
                if not img_path:
                    for sn in s_nums:
                        if sn in slides_content and slides_content[sn].get("images"):
                            candidate = slides_content[sn]["images"][0]
                            if not any(b_tag in candidate.lower() for b_tag in [".user_uploaded", "media_", "benchmark", "reference_"]):
                                img_path = candidate
                                break

                theme, role, action, equip = _detect_scene_character_and_theme(s_title, k_points, idx)

                scenes.append(
                    StoryboardScene(
                        scene_id=sid,
                        module_id=mod_id,
                        scene_purpose=sc.get("scene_purpose", "mechanism_breakdown"),
                        narration_text=sc.get("narration_text", ""),
                        visual_description=sc.get("visual_description", "2D safety animation"),
                        animation_type=sc.get("animation_type", "hazard_identification_and_stop"),
                        on_screen_text=[s_title] + k_points[:3],
                        visual_asset_references=sc.get("visual_asset_references", []),
                        source_references=[],
                        estimated_visual_duration=float(sc.get("estimated_visual_duration", 15.0)),
                        transition_instructions=sc.get("transition_instructions", "fade"),
                        key_points=k_points[:4],
                        key_metric=sc.get("key_metric", "100% COMPLIANT"),
                        takeaway=sc.get("takeaway", ""),
                        layout_type=sc.get("layout_type", "checklist_walkthrough"),
                        image_path=img_path,
                        visual_theme=theme,
                        character_role=role,
                        character_action=action,
                        equipment_type=equip,
                    )
                )

        if not scenes:
            return _generate_fallback_storyboard(job_id, analysis, plan, script)

        total_est = sum(s.estimated_visual_duration for s in scenes)
        return Storyboard(
            job_id=job_id,
            title=data.get("title") or analysis.title,
            total_scenes=len(scenes),
            estimated_total_duration_seconds=total_est,
            scenes=scenes,
        )

    except Exception as exc:
        logger.warning("Failed to generate storyboard via AI for job %s: %s; using deterministic fallback", job_id, exc)
        return _generate_fallback_storyboard(job_id, analysis, plan, script)
