"""Microsoft Edge Neural TTS provider with word-level boundary synchronization."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from ..rendering.ffmpeg import probe_video
from .base import BaseTTSProvider
from .models import TTSChunkResult, TTSVoice, WordTimestamp

logger = logging.getLogger("explain_studio.tts.edge_tts")

DEFAULT_EDGE_VOICES = [
    TTSVoice(voice_id="en-US-ChristopherNeural", name="Christopher (Educational)", language="en-US", gender="Male", provider="edge_tts"),
    TTSVoice(voice_id="en-US-JennyNeural", name="Jenny (Conversational)", language="en-US", gender="Female", provider="edge_tts"),
    TTSVoice(voice_id="en-US-GuyNeural", name="Guy (Enthusiastic)", language="en-US", gender="Male", provider="edge_tts"),
    TTSVoice(voice_id="en-GB-SoniaNeural", name="Sonia (British)", language="en-GB", gender="Female", provider="edge_tts"),
]


class EdgeTTSProvider(BaseTTSProvider):
    """High-quality neural speech synthesis using Microsoft Edge TTS with word timestamps."""

    @property
    def provider_id(self) -> str:
        return "edge_tts"

    def is_available(self) -> bool:
        try:
            import edge_tts  # noqa: F401
            return True
        except ImportError:
            return False

    def list_voices(self) -> list[TTSVoice]:
        return list(DEFAULT_EDGE_VOICES)

    async def synthesize(
        self,
        text: str,
        output_path: Path,
        voice: str | None = None,
    ) -> TTSChunkResult:
        """Synthesize text to MP3 audio capturing word-level timestamps."""
        import edge_tts

        selected_voice = voice or "en-US-ChristopherNeural"
        output_path.parent.mkdir(parents=True, exist_ok=True)

        clean_text = text.strip()
        if not clean_text:
            clean_text = "..."

        last_err: Exception | None = None
        for attempt in range(1, 4):
            try:
                # Conversational human speech: slightly relaxed cadence (-3%) with natural sentence intonation
                communicate = edge_tts.Communicate(
                    text=clean_text,
                    voice=selected_voice,
                    rate="-3%",
                    pitch="+0Hz",
                    boundary="SentenceBoundary",
                )
                audio_bytes = bytearray()
                word_timestamps: list[WordTimestamp] = []

                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        audio_bytes.extend(chunk["data"])
                    elif chunk["type"] in ("WordBoundary", "SentenceBoundary"):
                        # DEF-06: Edge TTS emits SentenceBoundary chunks (not WordBoundary).
                        # Both carry offset/duration in 100-nanosecond ticks.
                        offset_sec = chunk["offset"] / 10_000_000.0
                        dur_sec = chunk["duration"] / 10_000_000.0
                        word_timestamps.append(
                            WordTimestamp(
                                word=chunk["text"],
                                start_time=round(offset_sec, 3),
                                end_time=round(offset_sec + dur_sec, 3),
                            )
                        )

                if not audio_bytes:
                    raise RuntimeError("No audio bytes received from Edge TTS stream.")

                output_path.write_bytes(audio_bytes)

                # Measure actual duration from the audio file
                info = probe_video(output_path)
                dur = info.get("duration_seconds", 0.0)
                if dur <= 0.0 and word_timestamps:
                    dur = word_timestamps[-1].end_time

                return TTSChunkResult(
                    text=clean_text,
                    audio_path=str(output_path.resolve()),
                    duration_seconds=dur,
                    word_timestamps=word_timestamps,
                    provider_used="edge_tts",
                    voice_used=selected_voice,
                )
            except Exception as exc:
                last_err = exc
                logger.warning("Edge TTS attempt %d failed: %s. Retrying...", attempt, exc)
                await asyncio.sleep(1.0 * attempt)

        raise RuntimeError(f"Edge TTS failed after 3 attempts: {last_err}")
