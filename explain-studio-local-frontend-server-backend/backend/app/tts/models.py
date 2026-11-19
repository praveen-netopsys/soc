"""TTS models for voice management, speech synthesis, and word-level timing."""
from __future__ import annotations

from pathlib import Path
from pydantic import BaseModel, Field


class WordTimestamp(BaseModel):
    """Word-level timing information for synchronized subtitles."""
    word: str
    start_time: float = Field(description="Start time in seconds from beginning of audio")
    end_time: float = Field(description="End time in seconds from beginning of audio")


class TTSVoice(BaseModel):
    """Available voice representation across providers."""
    voice_id: str
    name: str
    language: str = "en-US"
    gender: str = "unknown"
    provider: str = "edge_tts"  # 'edge_tts' | 'offline_pyttsx3' | 'remote_server'


class TTSChunkResult(BaseModel):
    """Result of synthesizing a single narration segment or scene audio."""
    text: str
    audio_path: str
    duration_seconds: float
    word_timestamps: list[WordTimestamp] = Field(default_factory=list)
    provider_used: str = "auto"
    voice_used: str = "default"


class TTSAudioResult(BaseModel):
    """Aggregated result of full audio synthesis for a complete video."""
    audio_path: str
    total_duration_seconds: float
    scene_audio_paths: list[str] = Field(default_factory=list)
    word_timestamps: list[WordTimestamp] = Field(default_factory=list)
    provider_used: str = "auto"
    voice_used: str = "default"
