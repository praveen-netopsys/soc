"""Engine-neutral scene specification and rendering data models.

Provides a common contract consumed by Manim, Motion Canvas, and Fallback renderers.
All renderers produce uniform 1920x1080 @ 30fps H.264 video.
"""
from __future__ import annotations

import re
from enum import Enum
from pathlib import Path
from typing import Any
from pydantic import BaseModel, Field

from ..ai.models import StoryboardScene


class RenderEngineType(str, Enum):
    MANIM = "manim"
    MOTION_CANVAS = "motion_canvas"
    FALLBACK = "fallback"


class EngineNeutralSceneSpec(BaseModel):
    """
    Normalized, engine-independent scene definition ready for animation compilation.
    Transformed from AI Director StoryboardScene with concrete layout coordinates and styling.
    """
    scene_id: str
    module_id: str = "mod_1"
    scene_purpose: str = "concept_introduction"
    animation_type: str = "kinetic_typography"
    engine: str = ""
    visual_subject: str = "general_scientific"
    camera_motion: str = "push_in"
    environment: str = "cosmic_space"
    aspect_ratio: str = "16:9"
    title: str = ""
    narration_text: str = ""
    visual_description: str = ""
    duration_seconds: float = Field(default=8.0, ge=1.0)
    on_screen_text: list[str] = Field(default_factory=list)
    visual_asset_references: list[str] = Field(default_factory=list)
    equations: list[str] = Field(default_factory=list)
    diagram_nodes: list[str] = Field(default_factory=list)
    transition_instructions: str = "fade"
    module_title: str = ""
    key_points: list[str] = Field(default_factory=list)
    key_metric: str = ""
    takeaway: str = ""
    layout_type: str = "checklist_walkthrough"
    image_path: str = ""
    visual_theme: str = "safety_high_vis"
    character_role: str = "worker"
    character_action: str = "inspect"
    equipment_type: str = "forklift"
    environment_type: str = "auto"
    target_width: int = 1920
    target_height: int = 1080
    fps: int = 30
    lesson_number: int = 1
    total_lessons: int = 1
    background_color: str = "#0b0f19"  # Deep dark educational background
    primary_color: str = "#6366f1"     # Indigo
    accent_color: str = "#38bdf8"      # Sky blue
    text_color: str = "#f8fafc"        # Crisp white

    @classmethod
    def from_storyboard_scene(
        cls,
        scene: StoryboardScene,
        duration_seconds: float | None = None,
        target_width: int = 1920,
        target_height: int = 1080,
        fps: int = 30,
        aspect_ratio: str = "16:9",
        lesson_number: int | None = None,
        total_lessons: int | None = None,
    ) -> EngineNeutralSceneSpec:
        """Construct an EngineNeutralSceneSpec from an AI StoryboardScene."""
        equations: list[str] = []
        diagram_nodes: list[str] = []

        # Extract formulas/equations if present (avoid matching slash in phrases like "Lockout / Tagout" or "km/h")
        for t in scene.on_screen_text:
            if any(sym in t for sym in [" = ", "F =", "E =", "G *", "a^2", "b^2", "c^2", "\\frac", "v_esc"]):
                equations.append(t)
            else:
                diagram_nodes.append(t)

        dur = duration_seconds or scene.estimated_visual_duration or 8.0
        dur = max(2.0, float(dur))

        # Title from scene.title, scene.on_screen_text[0], or scene_purpose (never "None")
        raw_scene_title = getattr(scene, "title", None) or ""
        if raw_scene_title and str(raw_scene_title).strip() and str(raw_scene_title).strip().lower() != "none":
            title = str(raw_scene_title).strip()
        elif scene.on_screen_text and scene.on_screen_text[0] and str(scene.on_screen_text[0]).strip().lower() != "none":
            title = str(scene.on_screen_text[0]).strip()
        else:
            title = scene.scene_purpose.replace("_", " ").title()

        # Infer lesson number and total lessons
        lesson_num = lesson_number
        if lesson_num is None:
            m = re.search(r'\d+', scene.module_id or scene.scene_id or "")
            lesson_num = int(m.group()) if m else 1
        tot_lessons = total_lessons or 1

        # Infer topic-specific visual subject from description, purpose and animation type
        desc_lower = (scene.visual_description + " " + scene.narration_text + " " + scene.scene_purpose + " " + title + " " + scene.animation_type).lower()
        anim_clean = scene.animation_type.strip().lower()

        # Corporate Training Animation Types Priority
        if anim_clean in (
            "ppe_inspection_walkthrough",
            "vehicle_inspection_routine",
            "traffic_rules_and_speed_limits",
            "loading_dock_and_cargo_securing",
            "workstation_5s_transformation",
            "lockout_tagout_procedure",
            "emergency_stop_and_evacuation",
            "hazard_identification_and_stop",
        ):
            visual_subject = anim_clean
            camera_motion = "push_in"
        elif any(k in desc_lower for k in ["ppe", "helmet", "hard hat", "vest", "goggles", "glasses", "boots", "protective equipment"]):
            visual_subject = "ppe_inspection_walkthrough"
            camera_motion = "push_in"
        elif any(k in desc_lower for k in ["vehicle", "truck", "prime mover", "trailer", "pre-trip", "tires", "chock", "brakes", "coupling"]):
            visual_subject = "vehicle_inspection_routine"
            camera_motion = "pan_right"
        elif any(k in desc_lower for k in ["traffic", "speed", "walkway", "pedestrian", "forklift", "right of way", "15 km", "zebra"]):
            visual_subject = "traffic_rules_and_speed_limits"
            camera_motion = "pan_down"
        elif any(k in desc_lower for k in ["loading", "dock", "cargo", "twistlock", "strap", "exclusion zone", "unloading", "bay"]):
            visual_subject = "loading_dock_and_cargo_securing"
            camera_motion = "push_in"
        elif any(k in desc_lower for k in ["5s", "sort", "seiri", "seiton", "seiso", "shine", "standardize", "sustain", "shadow board"]):
            visual_subject = "workstation_5s_transformation"
            camera_motion = "zoom_in"
        elif any(k in desc_lower for k in ["lockout", "tagout", "loto", "padlock", "isolator", "zero energy", "breaker"]):
            visual_subject = "lockout_tagout_procedure"
            camera_motion = "push_in"
        elif any(k in desc_lower for k in ["emergency stop", "e-stop", "evacuation", "alarm", "muster", "fire exit"]):
            visual_subject = "emergency_stop_and_evacuation"
            camera_motion = "pan_right"
        elif any(k in desc_lower for k in ["hazard", "stop", "report", "incident", "supervisor", "near miss", "unsafe"]):
            visual_subject = "hazard_identification_and_stop"
            camera_motion = "push_in"
        elif any(k in desc_lower for k in ["cell", "mitosis", "meiosis", "chromosome", "cytokinesis", "spindle", "dna", "nucleus"]):
            visual_subject = "cell_division_mitosis"
            camera_motion = "zoom_in"
        elif any(k in desc_lower for k in ["sort", "quicksort", "mergesort", "bubble sort", "array", "binary search", "algorithm"]):
            visual_subject = "sorting_algorithm_bars"
            camera_motion = "pan_right"
        elif any(k in desc_lower for k in ["triangle", "pythagor", "hypotenuse", "geometry", "polygon", "trigonometr"]):
            visual_subject = "geometric_pythagoras"
            camera_motion = "push_in"
        elif any(k in desc_lower for k in ["civilization", "ancient", "egypt", "nile", "mesopotamia", "pyramid", "empire"]):
            visual_subject = "historical_civilization_map"
            camera_motion = "pan_down"
        elif any(k in desc_lower for k in ["hook", "apple", "fall", "drop", "why do objects fall"]):
            visual_subject = "falling_body_hook"
            camera_motion = "pan_down"
        elif any(k in desc_lower for k in ["inverse", "distance", "r^2", "1/r", "two masses"]):
            visual_subject = "inverse_square_law"
            camera_motion = "push_in"
        elif any(k in desc_lower for k in ["spacetime", "curvature", "fabric", "relativity", "gravity well", "warp"]):
            visual_subject = "spacetime_mesh"
            camera_motion = "orbit_around"
        elif any(k in desc_lower for k in ["escape", "spacecraft", "rocket", "hyperbolic", "suborbital"]):
            visual_subject = "escape_velocity"
            camera_motion = "zoom_reveal"
        elif any(k in desc_lower for k in ["orbit", "satellite", "centripetal", "circular path"]):
            visual_subject = "orbital_mechanics"
            camera_motion = "orbit_around"
        elif any(k in desc_lower for k in ["attract", "universal law", "pull", "newton's law", "gravitational force"]):
            visual_subject = "gravitational_attraction"
            camera_motion = "push_in"
        else:
            visual_subject = "ppe_inspection_walkthrough"
            camera_motion = "push_in"

        # Determine key points from scene attributes or on_screen_text
        key_pts = getattr(scene, "key_points", None) or []
        if not key_pts and len(scene.on_screen_text) > 1:
            key_pts = scene.on_screen_text[1:]
        elif not key_pts and scene.on_screen_text:
            key_pts = scene.on_screen_text

        key_met = getattr(scene, "key_metric", "") or ""
        take_away = getattr(scene, "takeaway", "") or ""
        lay_type = getattr(scene, "layout_type", "checklist_walkthrough") or "checklist_walkthrough"
        img_p = getattr(scene, "image_path", "") or ""
        # Benchmark and reference images must be used for art direction only, never inserted as static video images
        if img_p and any(b_tag in img_p.lower() for b_tag in [".user_uploaded", "media_", "benchmark", "reference_"]):
            img_p = ""

        # Dynamic Visual Theme detection
        raw_theme = getattr(scene, "visual_theme", "auto") or "auto"
        if raw_theme == "auto":
            text_corpus = (title + " " + scene.visual_description + " " + " ".join(key_pts)).lower()
            if any(k in text_corpus for k in ["zero harm", "environment", "health", "sustainab", "culture"]):
                v_theme = "environmental_zeroharm"
            elif any(k in text_corpus for k in ["speed", "traffic", "forklift", "truck", "driver", "vehicle", "dock", "cargo"]):
                v_theme = "warehouse_logistics"
            elif any(k in text_corpus for k in ["emergency", "stop", "hazard", "loto", "lockout", "evacuation", "fire"]):
                v_theme = "emergency_critical_loto"
            elif any(k in text_corpus for k in ["5s", "sort", "seiri", "order", "clean", "shine", "machine"]):
                v_theme = "plant_manufacturing"
            elif any(k in text_corpus for k in ["visitor", "orientation", "induction", "escort"]):
                v_theme = "corporate_compliance"
            else:
                v_theme = "safety_high_vis"
        else:
            v_theme = raw_theme

        # Dynamic Character Role detection
        raw_role = getattr(scene, "character_role", "worker") or "worker"
        if raw_role == "auto" or not raw_role:
            text_corpus = (title + " " + scene.visual_description + " " + " ".join(key_pts)).lower()
            if any(k in text_corpus for k in ["driver", "truck", "cab", "pre-trip"]):
                c_role = "driver"
            elif any(k in text_corpus for k in ["supervisor", "manager", "audit", "protocol"]):
                c_role = "supervisor"
            elif any(k in text_corpus for k in ["visitor", "contractor", "escort", "guest"]):
                c_role = "visitor"
            else:
                c_role = "worker"
        else:
            c_role = raw_role

        # Dynamic Character Action detection
        raw_action = getattr(scene, "character_action", "inspect") or "inspect"
        if raw_action == "auto" or not raw_action:
            text_corpus = (title + " " + scene.visual_description + " " + " ".join(key_pts)).lower()
            if any(k in text_corpus for k in ["walk", "crossing", "route", "evacuat"]):
                c_action = "walk"
            elif any(k in text_corpus for k in ["stop", "halt", "emergency", "danger"]):
                c_action = "stop"
            elif any(k in text_corpus for k in ["point", "standard", "takeaway", "critical", "never"]):
                c_action = "point"
            else:
                c_action = "inspect"
        else:
            c_action = raw_action

        # Dynamic Equipment Type & Environment Type detection
        raw_equip = getattr(scene, "equipment_type", "forklift") or "forklift"
        raw_env = getattr(scene, "environment_type", "auto") or "auto"

        text_corpus = (title + " " + scene.visual_description + " " + " ".join(key_pts) + " " + v_theme).lower()

        if raw_env == "auto" or not raw_env:
            if any(k in text_corpus for k in ["crane", "overhead", "suspended", "lifting", "gantry", "hazard", "fall", "molten", "debris"]):
                env_type = "hazard_zone"
                e_type = "overhead_crane" if raw_equip in ("forklift", "racks", "auto") else raw_equip
            elif any(k in text_corpus for k in ["5s", "sort", "seiri", "seiton", "seiso", "seiketsu", "shitsuke", "workbench", "pegboard", "shadow board", "organization", "cleaning"]):
                env_type = "workstation_5s"
                e_type = "workbench_5s" if raw_equip in ("forklift", "racks", "auto") else raw_equip
            elif any(k in text_corpus for k in ["truck", "trailer", "driver", "highway", "chassis", "prime mover", "road", "cargo"]):
                env_type = "highway_road"
                e_type = "prime_mover" if raw_equip in ("forklift", "racks", "auto") else raw_equip
            elif any(k in text_corpus for k in ["visitor", "orientation", "induction", "escort", "reception", "muster", "badge", "turnstile", "camera"]):
                env_type = "visitor_gate"
                e_type = "turnstile" if raw_equip in ("forklift", "racks", "auto") else raw_equip
            elif any(k in text_corpus for k in ["lockout", "tagout", "loto", "electrical", "breaker", "isolator", "zero energy", "e-stop"]):
                env_type = "critical_loto"
                e_type = "loto_station" if raw_equip in ("forklift", "racks", "auto") else raw_equip
            elif any(k in text_corpus for k in ["forklift", "pallet", "warehouse", "dock", "bay", "racks", "storage"]):
                env_type = "warehouse_dock"
                e_type = "forklift" if raw_equip == "auto" else raw_equip
            else:
                env_type = "visitor_gate" if v_theme == "corporate_compliance" else ("warehouse_dock" if v_theme == "warehouse_logistics" else "hazard_zone")
                e_type = "turnstile" if env_type == "visitor_gate" else ("forklift" if env_type == "warehouse_dock" else "overhead_crane")
        else:
            env_type = raw_env
            e_type = raw_equip

        return cls(
            scene_id=scene.scene_id,
            module_id=scene.module_id,
            scene_purpose=scene.scene_purpose,
            animation_type=scene.animation_type,
            visual_subject=visual_subject,
            camera_motion=camera_motion,
            aspect_ratio=aspect_ratio,
            title=title,
            narration_text=scene.narration_text,
            visual_description=scene.visual_description,
            duration_seconds=dur,
            on_screen_text=scene.on_screen_text,
            visual_asset_references=scene.visual_asset_references,
            equations=equations,
            diagram_nodes=diagram_nodes if diagram_nodes else scene.on_screen_text,
            transition_instructions=scene.transition_instructions or "fade",
            module_title=title,
            key_points=key_pts,
            key_metric=key_met,
            takeaway=take_away,
            layout_type=lay_type,
            image_path=img_p,
            visual_theme=v_theme,
            character_role=c_role,
            character_action=c_action,
            equipment_type=e_type,
            environment_type=env_type,
            target_width=target_width,
            target_height=target_height,
            fps=fps,
            lesson_number=lesson_num,
            total_lessons=tot_lessons,
        )


def get_dimensions_for_aspect_ratio(aspect_ratio: str, quality_profile: str = "production") -> tuple[int, int]:
    """Return (width, height) for specified aspect ratio and quality profile."""
    is_preview = quality_profile == "preview"
    ar = aspect_ratio.strip().lower()
    if ar in ("9:16", "vertical", "shorts", "reels", "portrait"):
        return (720, 1280) if is_preview else (1080, 1920)
    elif ar in ("1:1", "square"):
        return (720, 720) if is_preview else (1080, 1080)
    else:  # 16:9 landscape default
        return (1280, 720) if is_preview else (1920, 1080)


class SceneRenderResult(BaseModel):
    """Result of rendering a single scene video segment."""
    scene_id: str
    video_path: str
    engine_used: RenderEngineType
    duration_seconds: float
    width: int = 1920
    height: int = 1080
    fps: int = 30
    file_size_bytes: int = 0
    fallback_occurred: bool = False
    render_log: str = ""
    error_message: str | None = None


class FinalVideoResult(BaseModel):
    """Result of full video compilation (concatenation, audio mux, subtitles)."""
    job_id: str
    video_path: str
    subtitles_srt_path: str | None = None
    subtitles_vtt_path: str | None = None
    narration_audio_path: str | None = None
    total_duration_seconds: float
    scene_count: int
    width: int = 1920
    height: int = 1080
    fps: int = 30
    file_size_bytes: int = 0
    engines_used: list[RenderEngineType] = Field(default_factory=list)
    has_audio: bool = False
    has_subtitles: bool = False
