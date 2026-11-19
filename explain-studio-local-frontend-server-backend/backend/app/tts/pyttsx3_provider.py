"""Offline Windows SAPI Text-To-Speech provider using pyttsx3."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from ..rendering.ffmpeg import probe_video
from .base import BaseTTSProvider
from .models import TTSChunkResult, TTSVoice, WordTimestamp

logger = logging.getLogger("explain_studio.tts.pyttsx3")


class PyTTSx3OfflineProvider(BaseTTSProvider):
    """100% offline text-to-speech using native Windows speech API."""

    @property
    def provider_id(self) -> str:
        return "offline_pyttsx3"

    def is_available(self) -> bool:
        try:
            import pyttsx3  # noqa: F401
            return True
        except ImportError:
            return False

    def list_voices(self) -> list[TTSVoice]:
        """Enumerate native Windows SAPI voices."""
        try:
            import pyttsx3
            engine = pyttsx3.init()
            voices = engine.getProperty("voices")
            res: list[TTSVoice] = []
            for v in voices:
                gender = "Female" if "zira" in v.name.lower() or "hazel" in v.name.lower() else "Male"
                res.append(
                    TTSVoice(
                        voice_id=v.id,
                        name=v.name,
                        language="en-US" if "us" in v.id.lower() else "en-GB",
                        gender=gender,
                        provider="offline_pyttsx3",
                    )
                )
            return res if res else [
                TTSVoice(voice_id="default", name="Microsoft David Desktop", language="en-US", gender="Male", provider="offline_pyttsx3")
            ]
        except Exception as exc:
            logger.warning("Failed to list pyttsx3 voices: %s", exc)
            return [
                TTSVoice(voice_id="default", name="Microsoft David Desktop", language="en-US", gender="Male", provider="offline_pyttsx3")
            ]

    def _synthesize_sync(self, text: str, output_path: Path, voice: str | None = None) -> float:
        """Synchronous pyttsx3 execution inside thread pool."""
        import pyttsx3

        output_path.parent.mkdir(parents=True, exist_ok=True)
        # SAPI requires .wav extension for file output
        wav_path = output_path.with_suffix(".wav")

        engine = pyttsx3.init()
        engine.setProperty("rate", 145)  # 145 WPM educational pacing

        if voice:
            # Match voice ID or voice name
            for v in engine.getProperty("voices"):
                if voice.lower() in v.id.lower() or voice.lower() in v.name.lower():
                    engine.setProperty("voice", v.id)
                    break

        engine.save_to_file(text, str(wav_path.resolve()))
        engine.runAndWait()

        # If caller requested .mp3, convert or preserve
        if output_path.suffix.lower() == ".wav":
            return probe_video(wav_path).get("duration_seconds", 0.0)

        # Convert to requested format via FFmpeg if necessary
        from ..rendering.ffmpeg import run_ffmpeg
        run_ffmpeg(["-i", str(wav_path.resolve()), "-c:a", "libmp3lame", "-b:a", "192k", str(output_path.resolve())])

        if wav_path.exists() and wav_path != output_path:
            try:
                wav_path.unlink()
            except Exception:
                pass

        return probe_video(output_path).get("duration_seconds", 0.0)

    async def synthesize(
        self,
        text: str,
        output_path: Path,
        voice: str | None = None,
    ) -> TTSChunkResult:
        """Run pyttsx3 synthesis in background thread."""
        clean_text = text.strip() or "..."
        output_path.parent.mkdir(parents=True, exist_ok=True)

        duration = await asyncio.to_thread(self._synthesize_sync, clean_text, output_path, voice)

        # Generate proportional word timestamps
        words = clean_text.split()
        word_timestamps: list[WordTimestamp] = []
        if words and duration > 0:
            time_per_word = duration / len(words)
            for idx, w in enumerate(words):
                start = round(idx * time_per_word, 3)
                end = round((idx + 1) * time_per_word, 3)
                word_timestamps.append(WordTimestamp(word=w, start_time=start, end_time=end))

        return TTSChunkResult(
            text=clean_text,
            audio_path=str(output_path.resolve()),
            duration_seconds=duration,
            word_timestamps=word_timestamps,
            provider_used="offline_pyttsx3",
            voice_used=voice or "default",
        )
