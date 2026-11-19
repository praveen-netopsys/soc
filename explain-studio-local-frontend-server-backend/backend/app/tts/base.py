"""Abstract base class for Text-To-Speech providers."""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from .models import TTSChunkResult, TTSVoice


class BaseTTSProvider(ABC):
    """Abstract interface implemented by local, offline, and remote TTS engines."""

    @property
    @abstractmethod
    def provider_id(self) -> str:
        """Unique provider identifier (e.g. 'edge_tts', 'offline_pyttsx3')."""

    @abstractmethod
    def is_available(self) -> bool:
        """Check if this provider is operational on the current system."""

    @abstractmethod
    def list_voices(self) -> list[TTSVoice]:
        """List available voices for this provider."""

    @abstractmethod
    async def synthesize(
        self,
        text: str,
        output_path: Path,
        voice: str | None = None,
    ) -> TTSChunkResult:
        """
        Synthesize text into audio file, returning path, duration, and word timestamps.
        Must handle retries and error recovery.
        """
