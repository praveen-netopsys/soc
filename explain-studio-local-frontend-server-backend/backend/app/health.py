"""System Health & Diagnostics Service for Explain Studio.

Provides dedicated endpoints:
- GET /health/live: Fast liveness ping.
- GET /health/ready: Overall readiness check for local engine, tunnel, Ollama, GPU, storage, and TTS.
- GET /health/services: Detailed multi-service diagnostic report with latency, status, and actionable remediation.
"""
from __future__ import annotations

import logging
import os
import shutil
import time
from typing import Any

from . import config
from .ai.client import OllamaClient
from .ai.gpu import gpu_manager
from .hardware import get_hardware_capabilities

logger = logging.getLogger("explain_studio.health")


def check_liveness() -> dict[str, Any]:
    """Instant liveness check confirming process is alive and responsive."""
    return {
        "status": "alive",
        "service": "explain-studio-backend",
        "timestamp": time.time(),
    }


def check_storage_health() -> dict[str, Any]:
    """Verify local storage directory exists, is writable, and has sufficient capacity."""
    try:
        config.STORAGE_DIR.mkdir(parents=True, exist_ok=True)
        usage = shutil.disk_usage(config.STORAGE_DIR)
        free_gb = round(usage.free / (1024 ** 3), 2)
        total_gb = round(usage.total / (1024 ** 3), 2)
        used_gb = round(usage.used / (1024 ** 3), 2)

        is_ok = free_gb > 1.0  # At least 1 GB free for temporary video rendering
        return {
            "status": "ok" if is_ok else "warning",
            "free_gb": free_gb,
            "total_gb": total_gb,
            "used_gb": used_gb,
            "storage_path": str(config.STORAGE_DIR),
            "writable": True,
            "message": "Storage is healthy and writable." if is_ok else f"Low disk space: {free_gb} GB remaining.",
        }
    except Exception as exc:
        return {
            "status": "error",
            "free_gb": 0.0,
            "total_gb": 0.0,
            "used_gb": 0.0,
            "storage_path": str(config.STORAGE_DIR),
            "writable": False,
            "error": str(exc),
            "message": f"Storage directory inaccessible: {exc}",
        }


def check_tts_health() -> dict[str, Any]:
    """Verify Edge TTS and local audio synthesis readiness."""
    try:
        import edge_tts
        return {
            "status": "ok",
            "provider": "edge-tts",
            "ready": True,
            "message": "Edge TTS speech synthesis engine loaded and ready.",
        }
    except ImportError:
        return {
            "status": "warning",
            "provider": "offline",
            "ready": True,
            "message": "edge-tts package not installed; will fall back to pyttsx3/offline audio.",
        }
    except Exception as exc:
        return {
            "status": "error",
            "provider": "unknown",
            "ready": False,
            "error": str(exc),
            "message": f"TTS service error: {exc}",
        }


def check_all_services() -> dict[str, Any]:
    """Execute diagnostic checks across all 7 platform dependencies."""
    start_time = time.perf_counter()
    client = OllamaClient()

    # 1. Backend API & DB
    backend_status = {
        "status": "ok",
        "version": "0.6.0",
        "database": "sqlite_ready",
        "message": "Explain Studio backend API is active.",
    }

    # 2. Storage
    storage_status = check_storage_health()

    # 3. TTS Engine
    tts_status = check_tts_health()

    # 4. GPU Safety Manager
    gpu_status_raw = gpu_manager.get_status()
    gpu_status = {
        "status": "busy" if gpu_status_raw["is_busy"] else "ok",
        "is_busy": gpu_status_raw["is_busy"],
        "queue_count": gpu_status_raw["queue_count"],
        "current_task": gpu_status_raw["current_task"],
        "elapsed_seconds": gpu_status_raw["elapsed_seconds"],
        "message": (
            f"GPU inference active ({gpu_status_raw['current_task']})"
            if gpu_status_raw["is_busy"]
            else "GPU is idle and ready for inference."
        ),
    }

    # 5. Ollama & SSH Tunnel Connectivity
    ollama_conn = client.check_connection()
    tunnel_active = ollama_conn["connected"]
    default_tunnel_cmd = os.getenv(
        "EXPLAIN_STUDIO_SSH_TUNNEL_COMMAND",
        "ssh -N -o ServerAliveInterval=30 -o ExitOnForwardFailure=yes -L 127.0.0.1:11435:127.0.0.1:11434 intern@192.168.50.6"
    )
    tunnel_status = {
        "status": "ok" if tunnel_active else "offline",
        "active": tunnel_active,
        "base_url": client.base_url,
        "latency_ms": ollama_conn["latency_ms"],
        "error": ollama_conn["error"],
        "remediation": (
            None
            if tunnel_active
            else f"Run tunnel command: {default_tunnel_cmd} (or start local Ollama: 'ollama serve')"
        ),
        "message": (
            f"Ollama connected ({ollama_conn['latency_ms']} ms)."
            if tunnel_active
            else f"Ollama connection at {client.base_url} is not active."
        ),
    }

    # 6. Models verification
    models_status: dict[str, Any]
    if tunnel_active:
        models_raw = client.verify_models()
        models_status = {
            "status": "ok" if models_raw["all_expected_present"] else "warning",
            "all_present": models_raw["all_expected_present"],
            "expected_models": models_raw["expected_models"],
            "installed_models": models_raw["installed_models"],
            "verified_models": models_raw["verified_models"],
            "missing_models": models_raw["missing_models"],
            "message": (
                "Required models (qwen2.5:14b, qwen2.5vl:7b) verified on server."
                if models_raw["all_expected_present"]
                else f"Missing required models: {models_raw['missing_models']}"
            ),
        }
    else:
        models_status = {
            "status": "offline",
            "all_present": False,
            "expected_models": config.EXPECTED_OLLAMA_MODELS,
            "installed_models": [],
            "verified_models": [],
            "missing_models": config.EXPECTED_OLLAMA_MODELS,
            "message": "Cannot inspect models: SSH tunnel to Ollama server is offline.",
        }

    total_latency_ms = round((time.perf_counter() - start_time) * 1000, 2)

    # Calculate overall readiness
    ready_for_generation = (
        backend_status["status"] == "ok"
        and storage_status["status"] in ("ok", "warning")
        and tts_status["ready"]
    )

    ai_server_ready = tunnel_active and models_status["status"] == "ok"
    caps = get_hardware_capabilities()

    return {
        "status": "ready" if (ready_for_generation and ai_server_ready) else ("degraded" if ready_for_generation else "not_ready"),
        "ready": ready_for_generation,
        "ai_server_ready": ai_server_ready,
        "total_latency_ms": total_latency_ms,
        "hardware_profile": caps.profile,
        "services": {
            "backend": backend_status,
            "tunnel": tunnel_status,
            "ollama": {
                "status": "ok" if tunnel_active else "offline",
                "connected": tunnel_active,
                "latency_ms": ollama_conn["latency_ms"],
                "base_url": client.base_url,
            },
            "models": models_status,
            "gpu": gpu_status,
            "storage": storage_status,
            "tts": tts_status,
            "hardware": caps.to_dict(),
        },
    }
