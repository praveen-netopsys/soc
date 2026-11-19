"""Remote Company Server TTS Provider interface.

PENDING SERVER ACCESS:
Designed to connect to the company's Ubuntu server TTS service (e.g. Piper, Kokoro, XTTS)
over private SSH tunnel once server access returns.
"""
from __future__ import annotations

import logging
from pathlib import Path

from .base import BaseTTSProvider
from .models import TTSChunkResult, TTSVoice

logger = logging.getLogger("explain_studio.tts.remote")


class RemoteServerTTSProvider(BaseTTSProvider):
    """Production remote TTS adapter connecting to the company Ubuntu server."""

    def __init__(self, endpoint_url: str | None = None):
        self.endpoint_url = endpoint_url or "http://127.0.0.1:8001/tts"

    @property
    def provider_id(self) -> str:
        return "remote_server"

    def is_available(self) -> bool:
        """
        Remote server is marked unavailable during current offline development phase.
        Will be activated once SSH tunnel and server service are restored.
        """
        return False

    def list_voices(self) -> list[TTSVoice]:
        return [
            TTSVoice(voice_id="company-qwen-audio", name="Company Neural Voice (Ubuntu)", language="en-US", gender="Male", provider="remote_server"),
            TTSVoice(voice_id="company-kokoro", name="Company Kokoro High-Fidelity", language="en-US", gender="Female", provider="remote_server"),
        ]

    async def synthesize(
        self,
        text: str,
        output_path: Path,
        voice: str | None = None,
    ) -> TTSChunkResult:
        """Pending server access — raises clean informative error if called directly."""
        raise NotImplementedError(
            "Remote Company Server TTS is currently unavailable while the Ubuntu server is offline. "
            "Use 'auto', 'edge_tts', or 'offline_pyttsx3' local providers instead."
        )
