"""Abstract base class for animation renderers."""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from ..models import EngineNeutralSceneSpec, RenderEngineType, SceneRenderResult


class BaseAnimationEngine(ABC):
    """Abstract interface implemented by Manim, Motion Canvas, and Fallback renderers."""

    @property
    @abstractmethod
    def engine_type(self) -> RenderEngineType:
        """The identifier of this rendering engine."""

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if runtime dependencies (CLI, binaries, packages) are present."""

    @abstractmethod
    def can_render(self, spec: EngineNeutralSceneSpec) -> bool:
        """Return True if this engine is suited to render the specific animation type."""

    @abstractmethod
    def render_scene(
        self,
        spec: EngineNeutralSceneSpec,
        output_path: Path,
    ) -> SceneRenderResult:
        """
        Compile and render the given scene specification to a 1080p 30fps MP4 video file.
        Must write the output to output_path and return SceneRenderResult.
        """
