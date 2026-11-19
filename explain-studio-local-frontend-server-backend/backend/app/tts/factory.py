"""TTS Provider Factory and multi-scene narration synthesis coordinator."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Sequence

from ..rendering.ffmpeg import run_ffmpeg, probe_video
from .base import BaseTTSProvider
from .edge_tts_provider import EdgeTTSProvider
from .models import TTSAudioResult, TTSChunkResult, TTSVoice, WordTimestamp
from .pyttsx3_provider import PyTTSx3OfflineProvider
from .remote_provider import RemoteServerTTSProvider

logger = logging.getLogger("explain_studio.tts.factory")


def get_tts_provider(provider_type: str = "auto") -> BaseTTSProvider:
    """
    Instantiate the requested TTS provider.
    'auto': Prefers Edge Neural TTS, with graceful fallback to Offline Windows SAPI.
    'offline': Uses native offline Windows pyttsx3.
    'edge': Uses Microsoft Edge TTS.
    'remote': Uses remote server provider (marked pending server access).
    """
    p_type = provider_type.lower()
    if p_type == "offline":
        return PyTTSx3OfflineProvider()
    if p_type == "remote":
        return RemoteServerTTSProvider()
    if p_type == "edge":
        return EdgeTTSProvider()

    # 'auto': Try Edge TTS first if available
    edge = EdgeTTSProvider()
    if edge.is_available():
        return edge

    return PyTTSx3OfflineProvider()


class AudioNarrationCoordinator:
    """Coordinates synthesizing all scene script segments into synchronized audio files."""

    def __init__(self, provider: BaseTTSProvider | None = None):
        self.provider = provider or get_tts_provider("auto")
        self.offline_provider = PyTTSx3OfflineProvider()

    async def synthesize_scenes(
        self,
        scene_texts: list[tuple[str, str]],  # [(scene_id, narration_text)]
        output_dir: Path,
        voice: str | None = None,
    ) -> TTSAudioResult:
        """
        Synthesize audio for each scene, handling chunking, retries, and concatenation.
        Returns aggregated TTSAudioResult with per-scene and global word timestamps.
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        chunk_results: list[TTSChunkResult] = []
        global_word_timestamps: list[WordTimestamp] = []
        scene_audio_paths: list[str] = []

        running_time_offset = 0.0

        for scene_id, text in scene_texts:
            scene_audio_path = output_dir / f"audio_{scene_id}.mp3"
            try:
                chunk = await self.provider.synthesize(text, scene_audio_path, voice=voice)
            except Exception as exc:
                logger.warning(
                    "Primary TTS provider '%s' failed for scene '%s': %s. Using offline fallback.",
                    self.provider.provider_id,
                    scene_id,
                    exc,
                )
                chunk = await self.offline_provider.synthesize(text, scene_audio_path, voice=voice)

            chunk_results.append(chunk)
            scene_audio_paths.append(str(Path(chunk.audio_path).resolve()))

            # Offset word timestamps to align with global video timeline
            for wt in chunk.word_timestamps:
                global_word_timestamps.append(
                    WordTimestamp(
                        word=wt.word,
                        start_time=round(wt.start_time + running_time_offset, 3),
                        end_time=round(wt.end_time + running_time_offset, 3),
                    )
                )

            running_time_offset += chunk.duration_seconds

        # Concatenate scene audio files into a single master audio track
        master_audio_path = output_dir / "master_narration.mp3"
        concat_txt = output_dir / "audio_concat.txt"
        lines = [f"file '{Path(p).resolve().as_posix()}'" for p in scene_audio_paths]
        concat_txt.write_text("\n".join(lines), encoding="utf-8")

        try:
            run_ffmpeg([
                "-f", "concat",
                "-safe", "0",
                "-i", str(concat_txt.resolve()),
                "-c:a", "libmp3lame",
                "-b:a", "192k",
                str(master_audio_path.resolve()),
            ])
        finally:
            if concat_txt.exists():
                try:
                    concat_txt.unlink()
                except Exception:
                    pass

        info = probe_video(master_audio_path)
        total_duration = info.get("duration_seconds", running_time_offset)

        return TTSAudioResult(
            audio_path=str(master_audio_path.resolve()),
            total_duration_seconds=total_duration,
            scene_audio_paths=scene_audio_paths,
            word_timestamps=global_word_timestamps,
            provider_used=self.provider.provider_id,
            voice_used=voice or "default",
        )
