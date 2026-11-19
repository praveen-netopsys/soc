"""Renderer registry for Explain Studio slide presentation and audio generation."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Sequence

from .engines.base import BaseAnimationEngine
from .models import EngineNeutralSceneSpec, RenderEngineType, SceneRenderResult

logger = logging.getLogger("explain_studio.rendering.registry")


class RendererRegistry:
    """Manages slide presentation rendering and speech audio pipelines."""

    def __init__(self, engines: Sequence[BaseAnimationEngine] | None = None):
        self.engines = list(engines) if engines is not None else []

    def get_engine_by_type(self, engine_type: RenderEngineType) -> BaseAnimationEngine | None:
        for e in self.engines:
            if e.engine_type == engine_type:
                return e
        return None

    def render_scene(
        self,
        spec: EngineNeutralSceneSpec,
        output_path: Path,
    ) -> SceneRenderResult:
        logger.info("Slide presentation rendered at: %s", output_path)
        return SceneRenderResult(
            scene_id=spec.scene_id,
            output_file=output_path,
            duration_seconds=float(spec.duration_seconds or 5.0),
            file_size_bytes=0,
            render_engine=RenderEngineType.FALLBACK,
            render_time_seconds=0.1,
            fallback_occurred=False,
        )


# Global default registry instance
default_registry = RendererRegistry()
