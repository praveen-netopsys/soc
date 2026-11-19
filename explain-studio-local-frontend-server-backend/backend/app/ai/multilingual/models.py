"""Data models for multilingual educational course generation."""
from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field

SUPPORTED_LANGUAGES: dict[str, dict[str, Any]] = {
    "en": {
        "code": "en",
        "name": "English",
        "native_name": "English",
        "male_voice": "en-US-ChristopherNeural",
        "female_voice": "en-US-JennyNeural",
        "default_voice": "en-US-ChristopherNeural",
        "fallback_voices": ["en-US-GuyNeural", "en-US-AriaNeural"],
    },
    "zh-CN": {
        "code": "zh-CN",
        "name": "Chinese (Simplified)",
        "native_name": "中文",
        "male_voice": "zh-CN-YunxiNeural",
        "female_voice": "zh-CN-XiaoxiaoNeural",
        "default_voice": "zh-CN-XiaoxiaoNeural",
        "fallback_voices": ["zh-CN-YunjianNeural", "zh-CN-XiaoyiNeural"],
    },
    "ms": {
        "code": "ms",
        "name": "Malay",
        "native_name": "Bahasa Melayu",
        "male_voice": "ms-MY-OsmanNeural",
        "female_voice": "ms-MY-YasminNeural",
        "default_voice": "ms-MY-OsmanNeural",
        "fallback_voices": ["ms-MY-YasminNeural"],
    },
    "ta": {
        "code": "ta",
        "name": "Tamil",
        "native_name": "தமிழ்",
        "male_voice": "ta-IN-ValluvarNeural",
        "female_voice": "ta-IN-PallaviNeural",
        "default_voice": "ta-IN-ValluvarNeural",
        "fallback_voices": ["ta-IN-PallaviNeural"],
    },
    "bn": {
        "code": "bn",
        "name": "Bangla",
        "native_name": "বাংলা",
        "male_voice": "bn-IN-BashkarNeural",
        "female_voice": "bn-IN-TanishaaNeural",
        "default_voice": "bn-IN-BashkarNeural",
        "fallback_voices": ["bn-BD-PradeepNeural", "bn-BD-NabanitaNeural"],
    },
    "te": {
        "code": "te",
        "name": "Telugu",
        "native_name": "తెలుగు",
        "male_voice": "te-IN-MohanNeural",
        "female_voice": "te-IN-ShrutiNeural",
        "default_voice": "te-IN-MohanNeural",
        "fallback_voices": ["te-IN-ShrutiNeural"],
    },
}


class SlideExplanation(BaseModel):
    """Structured explanation object for an individual presentation slide."""
    slide_number: int
    slide_id: str
    module_id: str
    title: str
    key_points: list[str] = Field(default_factory=list)
    table_summaries: list[str] = Field(default_factory=list)
    image_descriptions: list[str] = Field(default_factory=list)
    slide_image_path: str = ""
    slide_image_url: str = ""
    # Map of language code -> full spoken teaching explanation
    explanations: dict[str, str] = Field(default_factory=dict)
    # Map of language code -> duration of audio in seconds
    durations_seconds: dict[str, float] = Field(default_factory=dict)
    # Map of language code -> audio file relative path
    audio_files: dict[str, str] = Field(default_factory=dict)
    # Map of language code -> streamable audio URL
    audio_urls: dict[str, str] = Field(default_factory=dict)
    # Slide boundaries within the module (seconds)
    module_start_seconds: dict[str, float] = Field(default_factory=dict)
    module_end_seconds: dict[str, float] = Field(default_factory=dict)
    # Slide boundaries within the full course (seconds)
    course_start_seconds: dict[str, float] = Field(default_factory=dict)
    course_end_seconds: dict[str, float] = Field(default_factory=dict)


class CourseModule(BaseModel):
    """Concept-based learning module with unique slide assignments."""
    module_id: str
    module_number: int
    title: str
    description: str
    learning_objectives: list[str] = Field(default_factory=list)
    slide_numbers: list[int] = Field(default_factory=list)
    slides: list[SlideExplanation] = Field(default_factory=list)
    # Total duration per language
    durations_seconds: dict[str, float] = Field(default_factory=dict)
    # Progressive readiness state per language: e.g. {"en": True, "zh-CN": True, ...}
    module_ready: dict[str, bool] = Field(default_factory=dict)
    # Optional legacy video files reference
    video_files: dict[str, str] = Field(default_factory=dict)


class MultilingualCourse(BaseModel):
    """Complete course container with all modules and multilingual exports."""
    job_id: str
    title: str
    total_slides: int
    total_modules: int
    languages: list[str] = Field(default_factory=lambda: ["en", "zh-CN", "ms", "ta", "bn", "te"])
    modules: list[CourseModule] = Field(default_factory=list)
    glossary: dict[str, dict[str, str]] = Field(default_factory=dict)
    # Readiness summary per language: e.g. {"en": True, ...}
    languages_ready: dict[str, bool] = Field(default_factory=dict)
    course_video_files: dict[str, str] = Field(default_factory=dict)
    validation_report: dict[str, Any] = Field(default_factory=dict)
    performance_metrics: dict[str, Any] = Field(default_factory=dict)
