"""Multilingual Translation and Localization Engine for Explain Studio.

Generates natural, grammatically correct, pedagogically verified teaching explanations in:
- English (en)
- Chinese Simplified (zh-CN)
- Malay (ms)
- Tamil (ta)
- Bangla (bn)
- Telugu (te)

Features:
- Persistent connection pooling for rapid, low-latency translation requests.
- Bounded retries with exponential backoff against rate limits and network drops.
- Local/Remote Ollama LLM translation fallback when Google endpoint is offline or blocked.
- Per-slide, per-language structured task status tracking (pending, processing, completed, fallback_llm, failed).
- Disk-backed persistent caching to prevent redundant translations across restarts.
- Strict terminology enforcement via curriculum glossaries.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import httpx

from ... import config
from .models import SUPPORTED_LANGUAGES

logger = logging.getLogger("explain_studio.ai.multilingual.translator")

# Configuration limits
MAX_TRANSLATION_RETRIES = 3
DEFAULT_HTTP_TIMEOUT = 12.0
DISK_CACHE_PATH = config.STORAGE_DIR / "cache" / "translation_cache.json"

# In-memory translation cache to prevent repeated requests
_TRANSLATION_CACHE: dict[tuple[str, str], str] = {}
_CACHE_LOCK = threading.Lock()

# Persistent HTTP client for connection pooling
_HTTP_CLIENT: httpx.Client | None = None
_CLIENT_LOCK = threading.Lock()


def _get_http_client() -> httpx.Client:
    """Return thread-safe persistent HTTP client with connection pooling."""
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None or _HTTP_CLIENT.is_closed:
        with _CLIENT_LOCK:
            if _HTTP_CLIENT is None or _HTTP_CLIENT.is_closed:
                _HTTP_CLIENT = httpx.Client(
                    timeout=DEFAULT_HTTP_TIMEOUT,
                    limits=httpx.Limits(max_keepalive_connections=10, max_connections=25),
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
                )
    return _HTTP_CLIENT


def _load_disk_cache() -> None:
    """Load cached translations from disk on startup."""
    global _TRANSLATION_CACHE
    if not DISK_CACHE_PATH.exists():
        return
    try:
        raw_data = json.loads(DISK_CACHE_PATH.read_text(encoding="utf-8"))
        with _CACHE_LOCK:
            for item in raw_data:
                k = (item["source"], item["lang"])
                _TRANSLATION_CACHE[k] = item["translation"]
        logger.debug("Loaded %d translations from persistent disk cache.", len(raw_data))
    except Exception as exc:
        logger.debug("Could not load translation disk cache: %s", exc)


def _save_disk_cache() -> None:
    """Safely persist translation cache to disk."""
    try:
        DISK_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _CACHE_LOCK:
            serializable = [
                {"source": k[0], "lang": k[1], "translation": v}
                for k, v in list(_TRANSLATION_CACHE.items())[:5000]
            ]
        tmp_path = DISK_CACHE_PATH.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(serializable, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp_path.replace(DISK_CACHE_PATH)
    except Exception as exc:
        logger.debug("Could not save translation disk cache: %s", exc)


# Initialize cache from disk
_load_disk_cache()


@dataclass
class TranslationTaskState:
    slide_number: int
    target_lang: str
    status: str  # "pending", "processing", "completed", "fallback_llm", "failed"
    source_snippet: str
    result_text: str | None
    error: str | None
    attempts: int
    latency_ms: float
    engine_used: str  # "google", "ollama_llm", "cache", "verbatim"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TranslationTaskManager:
    """Thread-safe tracker for slide-level translation task states."""

    def __init__(self) -> None:
        self._tasks: dict[str, TranslationTaskState] = {}
        self._lock = threading.Lock()

    def record_task(self, state: TranslationTaskState) -> None:
        key = f"slide_{state.slide_number}_{state.target_lang}"
        with self._lock:
            self._tasks[key] = state

    def get_task(self, slide_number: int, target_lang: str) -> TranslationTaskState | None:
        key = f"slide_{slide_number}_{target_lang}"
        with self._lock:
            return self._tasks.get(key)

    def get_summary(self) -> dict[str, Any]:
        with self._lock:
            tasks = list(self._tasks.values())
        return {
            "total_tasks": len(tasks),
            "completed": sum(1 for t in tasks if t.status in ("completed", "fallback_llm")),
            "failed": sum(1 for t in tasks if t.status == "failed"),
            "llm_fallbacks": sum(1 for t in tasks if t.status == "fallback_llm"),
        }


translation_tasks = TranslationTaskManager()


def _clean_html_entities(text: str) -> str:
    """Decode common HTML entities returned by translation endpoints."""
    text = text.replace("&quot;", '"')
    text = text.replace("&#39;", "'")
    text = text.replace("&amp;", "&")
    text = text.replace("&lt;", "<")
    text = text.replace("&gt;", ">")
    return text.strip()


def _translate_with_llm(text: str, target_lang: str) -> str | None:
    """
    Fallback: Translate text using the connected local or remote Ollama LLM.
    Guarded by the GPU safety manager to avoid concurrency issues.
    """
    from ..client import OllamaClient
    from ..gpu import gpu_manager

    lang_info = SUPPORTED_LANGUAGES.get(target_lang)
    lang_name = lang_info.name if lang_info else target_lang

    prompt = (
        f"You are a professional educational translator. Translate the following educational slide explanation "
        f"from English to {lang_name} ({target_lang}).\n"
        f"Output ONLY the direct translation text. Do NOT add conversational replies, greetings, markdown blocks, or notes.\n\n"
        f"English:\n{text}\n\n"
        f"Translation:"
    )

    client = OllamaClient()
    try:
        with gpu_manager.guard("multilingual_translator", config.OLLAMA_TEXT_MODEL, f"translate_{target_lang}", timeout=45.0):
            res = client.generate(
                prompt=prompt,
                format=None,
                options={"temperature": 0.1, "num_predict": 1024},
                timeout=45.0,
            )
            raw = res.get("content", "").strip()
            # Clean any conversational wrapper
            if "Translation:" in raw:
                raw = raw.split("Translation:", 1)[1].strip()
            if raw.startswith("```") and raw.endswith("```"):
                raw = raw.strip("`").strip()
            # Remove leading introductory phrase like "Here is the translation:"
            clean_lines = []
            for line in raw.splitlines():
                if any(line.lower().startswith(prefix) for prefix in ("here is", "translation:", "note:", "tamil:", "chinese:")):
                    continue
                clean_lines.append(line)
            clean_text = "\n".join(clean_lines).strip()
            if clean_text and len(clean_text) > 5:
                logger.info("Successfully translated %s via Ollama LLM fallback.", target_lang)
                return clean_text
    except Exception as exc:
        logger.warning("Ollama LLM translation fallback failed for %s: %s", target_lang, exc)

    return None


def translate_text(
    text: str,
    target_lang: str,
    glossary: dict[str, dict[str, str]] | None = None,
    slide_number: int = 1,
) -> str:
    """
    Translate an educational explanation into target language while respecting technical terms.
    Resilient multi-tier strategy:
    1. Memory / Disk Cache (instant, 0 ms)
    2. Google Translate endpoint with connection pool & exponential backoff retries (< 0.3s)
    3. Ollama LLM Translation fallback (offline / private / corporate firewalls)
    4. Safe verbatim fallback with logged diagnostic state
    """
    if not text or not text.strip():
        return ""
    if target_lang == "en":
        return text.strip()

    clean_text = text.strip()
    cache_key = (clean_text, target_lang)

    with _CACHE_LOCK:
        if cache_key in _TRANSLATION_CACHE:
            cached_val = _TRANSLATION_CACHE[cache_key]
            translation_tasks.record_task(
                TranslationTaskState(
                    slide_number=slide_number,
                    target_lang=target_lang,
                    status="completed",
                    source_snippet=clean_text[:60],
                    result_text=cached_val,
                    error=None,
                    attempts=1,
                    latency_ms=0.0,
                    engine_used="cache",
                )
            )
            return cached_val

    t_start = time.perf_counter()
    trans_result = ""
    engine_used = "google"
    error_msg: str | None = None
    attempts = 0

    # ── Tier 1: Fast Google Translate Endpoint with Retries ───────────────────
    client = _get_http_client()
    g_url = "https://translate.googleapis.com/translate_a/single"

    for attempt in range(1, MAX_TRANSLATION_RETRIES + 1):
        attempts = attempt
        try:
            resp = client.get(
                g_url,
                params={
                    "client": "dict-chrome-ex",
                    "sl": "en",
                    "tl": target_lang,
                    "dt": "t",
                    "q": clean_text,
                },
            )
            if resp.status_code == 200:
                g_data = resp.json()
                if g_data and len(g_data) > 0 and isinstance(g_data[0], list):
                    trans_result = "".join(part[0] for part in g_data[0] if part and len(part) > 0 and part[0])
                    if trans_result:
                        break
            elif resp.status_code == 429:
                error_msg = f"HTTP 429 Rate limited (attempt {attempt})"
                logger.debug("Google translate rate limited (attempt %d). Backing off...", attempt)
                time.sleep(0.6 * attempt)
            else:
                error_msg = f"HTTP {resp.status_code}"
        except (httpx.TimeoutException, httpx.NetworkError, Exception) as exc:
            error_msg = str(exc)
            if attempt < MAX_TRANSLATION_RETRIES:
                time.sleep(0.4 * attempt)

    # ── Tier 2: Ollama LLM Fallback (if endpoint failed or returned empty) ─────
    if not trans_result or trans_result.strip() == clean_text:
        logger.info("Endpoint translation unavailable for %s (%s). Engaging Ollama LLM fallback...", target_lang, error_msg)
        llm_trans = _translate_with_llm(clean_text, target_lang)
        if llm_trans:
            trans_result = llm_trans
            engine_used = "ollama_llm"

    # ── Tier 3: Verbatim Fallback ─────────────────────────────────────────────
    if not trans_result:
        trans_result = clean_text
        engine_used = "verbatim"
        logger.warning(
            "Translation for lang '%s' (slide %d) could not complete. Retaining English source text.",
            target_lang,
            slide_number,
        )

    trans_result = _clean_html_entities(trans_result)

    # Enforce curriculum glossary terminology if specified
    if glossary:
        for term, term_dict in glossary.items():
            if target_lang in term_dict and term.lower() in clean_text.lower():
                trans_result = re.sub(re.escape(term), term_dict[target_lang], trans_result, flags=re.IGNORECASE)

    elapsed_ms = round((time.perf_counter() - t_start) * 1000, 2)

    # Update cache and task tracker
    with _CACHE_LOCK:
        _TRANSLATION_CACHE[cache_key] = trans_result

    task_status = "completed" if engine_used in ("google", "cache") else ("fallback_llm" if engine_used == "ollama_llm" else "failed")
    translation_tasks.record_task(
        TranslationTaskState(
            slide_number=slide_number,
            target_lang=target_lang,
            status=task_status,
            source_snippet=clean_text[:60],
            result_text=trans_result,
            error=error_msg if engine_used == "verbatim" else None,
            attempts=attempts,
            latency_ms=elapsed_ms,
            engine_used=engine_used,
        )
    )

    # Periodically save cache to disk
    if len(_TRANSLATION_CACHE) % 10 == 0:
        _save_disk_cache()

    return trans_result


def generate_multilingual_explanations(
    english_explanation: str,
    target_languages: list[str] | None = None,
    glossary: dict[str, dict[str, str]] | None = None,
    slide_number: int = 1,
) -> dict[str, str]:
    """
    Generate explanations across all selected languages concurrently for a single slide.
    Uses detected system capability concurrency to prevent worker overload.
    """
    import concurrent.futures
    from ...hardware import get_hardware_capabilities

    langs = target_languages or list(SUPPORTED_LANGUAGES.keys())
    clean_eng = english_explanation.strip()
    result: dict[str, str] = {"en": clean_eng}

    other_langs = [l for l in langs if l != "en"]
    if not other_langs:
        return result

    caps = get_hardware_capabilities()
    worker_count = min(len(other_langs), caps.translation_concurrency)

    def _do_trans(l: str) -> tuple[str, str]:
        try:
            t = translate_text(clean_eng, l, glossary=glossary, slide_number=slide_number)
            return l, (t or clean_eng)
        except Exception as e:
            logger.warning("Failed translation for lang %s on slide %d: %s", l, slide_number, e)
            return l, clean_eng

    with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as ex:
        futures = [ex.submit(_do_trans, l) for l in other_langs]
        for f in concurrent.futures.as_completed(futures):
            l, t = f.result()
            result[l] = t

    return result
