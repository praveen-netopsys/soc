"""Runtime configuration — all tunable via environment variables."""
from __future__ import annotations

import os
from pathlib import Path

# Load local .env or SkillBridge Backend/.env if present
for _env_candidate in [
    Path(__file__).resolve().parent.parent / ".env",
    Path(__file__).resolve().parents[4] / "Backend" / ".env",
    Path(__file__).resolve().parents[3] / "Backend" / ".env",
    Path("C:/Users/pavankumar/Music/Skill-Bridge/Backend/.env"),
]:
    if _env_candidate.exists():
        for _line in _env_candidate.read_text(encoding="utf-8").splitlines():
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                _v_clean = _v.strip().strip('"').strip("'")
                os.environ.setdefault(_k.strip(), _v_clean)

# ── Storage ───────────────────────────────────────────────────────────────────
# All files, database and extraction manifests live under STORAGE_DIR.
# Override with EXPLAIN_STUDIO_STORAGE_DIR for production or testing.
_default_storage = Path(__file__).resolve().parent.parent / "storage"
STORAGE_DIR: Path = Path(os.getenv("EXPLAIN_STUDIO_STORAGE_DIR", str(_default_storage))).resolve()
DB_PATH: Path = STORAGE_DIR / "explain_studio.db"
FILES_DIR: Path = STORAGE_DIR / "files"
EXTRACTED_DIR: Path = STORAGE_DIR / "extracted"
ANALYSIS_DIR: Path = STORAGE_DIR / "analysis"
RENDERS_DIR: Path = STORAGE_DIR / "renders"
VIDEOS_DIR: Path = STORAGE_DIR / "videos"
COURSES_DIR: Path = STORAGE_DIR / "courses"
TMP_DIR: Path = STORAGE_DIR / "tmp"

# ── Video rendering specifications ───────────────────────────────────────────
VIDEO_WIDTH: int = int(os.getenv("EXPLAIN_STUDIO_VIDEO_WIDTH", "1920"))
VIDEO_HEIGHT: int = int(os.getenv("EXPLAIN_STUDIO_VIDEO_HEIGHT", "1080"))
VIDEO_FPS: int = int(os.getenv("EXPLAIN_STUDIO_VIDEO_FPS", "30"))

# ── Narration / TTS Configuration ────────────────────────────────────────────
TTS_PROVIDER: str = os.getenv("EXPLAIN_STUDIO_TTS_PROVIDER", "auto")  # 'auto' | 'edge' | 'offline' | 'remote'
DEFAULT_TTS_VOICE: str = os.getenv("EXPLAIN_STUDIO_TTS_VOICE", "en-US-ChristopherNeural")


# ── Upload limits ─────────────────────────────────────────────────────────────
MAX_FILE_BYTES: int = int(
    os.getenv("EXPLAIN_STUDIO_MAX_FILE_BYTES", str(50 * 1024 * 1024))  # 50 MB
)
MAX_TOTAL_BYTES: int = int(
    os.getenv("EXPLAIN_STUDIO_MAX_TOTAL_BYTES", str(100 * 1024 * 1024))  # 100 MB
)
MAX_FILES: int = int(os.getenv("EXPLAIN_STUDIO_MAX_FILES", "15"))

# ── Allowed file types ────────────────────────────────────────────────────────
ALLOWED_EXTENSIONS: frozenset[str] = frozenset(
    ["txt", "md", "markdown", "pptx", "png", "jpg", "jpeg", "webp"]
)

# ── Remote AI / Unified Qwen Gateway Configuration ──────────────────────────
# Base URL for Ollama / Remote Qwen Gateway
OLLAMA_BASE_URL: str = os.getenv(
    "AI_GATEWAY_URL",
    os.getenv(
        "QWEN_GATEWAY_URL",
        os.getenv(
            "EXPLAIN_STUDIO_OLLAMA_BASE_URL",
            os.getenv("OLLAMA_BASE_URL", "http://192.168.50.6:8001")
        )
    )
).rstrip("/")

QWEN_API_KEY: str = os.getenv(
    "AI_GATEWAY_API_KEY",
    os.getenv(
        "AI_API_KEY",
        os.getenv(
            "QWEN_API_KEY",
            os.getenv("BRUTALLYHIRED_LLM_API_KEY", "d52edbbeb11ee8b323282d10fe030b6f71667984d8199b8e4f67ed7513fb7387")
        )
    )
).strip()

OLLAMA_TIMEOUT_SECONDS: float = float(
    os.getenv("AI_GATEWAY_TIMEOUT", os.getenv("EXPLAIN_STUDIO_OLLAMA_TIMEOUT_SECONDS", "300.0"))
)

OLLAMA_INFERENCE_TIMEOUT_SECONDS: float = float(
    os.getenv("EXPLAIN_STUDIO_OLLAMA_INFERENCE_TIMEOUT_SECONDS", "180.0")
)

# Unified Single Model: Qwen 3.5 9B
_UNIFIED_DEFAULT_MODEL = os.getenv("QWEN_MODEL", os.getenv("OLLAMA_MODEL", "qwen3.5:9b"))

OLLAMA_TEXT_MODEL: str = os.getenv(
    "EXPLAIN_STUDIO_OLLAMA_TEXT_MODEL",
    _UNIFIED_DEFAULT_MODEL,
)

OLLAMA_VISION_MODEL: str = os.getenv(
    "EXPLAIN_STUDIO_OLLAMA_VISION_MODEL",
    _UNIFIED_DEFAULT_MODEL,
)

EXPECTED_OLLAMA_MODELS: list[str] = [
    _UNIFIED_DEFAULT_MODEL,
    "qwen3.5:9b",
    "qwen2.5:14b",
    "qwen2.5vl:7b",
]

# Configurable token limits and inference parameters
OLLAMA_NUM_CTX: int = int(os.getenv("EXPLAIN_STUDIO_OLLAMA_NUM_CTX", "16384"))
OLLAMA_NUM_PREDICT: int = int(os.getenv("EXPLAIN_STUDIO_OLLAMA_NUM_PREDICT", "4096"))
OLLAMA_TEMPERATURE: float = float(os.getenv("EXPLAIN_STUDIO_OLLAMA_TEMPERATURE", "0.2"))

# Maximum characters per chunk for hierarchical processing
MAX_CHUNK_CHARACTERS: int = int(
    os.getenv("EXPLAIN_STUDIO_MAX_CHUNK_CHARACTERS", "7000")
)

# ── CORS ──────────────────────────────────────────────────────────────────────
_ORIGINS_RAW: str = os.getenv(
    "EXPLAIN_STUDIO_ALLOWED_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173",
)


def allowed_origins() -> list[str]:
    return [o.strip() for o in _ORIGINS_RAW.split(",") if o.strip()]
