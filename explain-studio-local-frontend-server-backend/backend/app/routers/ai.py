"""AI diagnostic and model discovery endpoints.

GET /v1/ai/health — check Ollama connectivity and latency through the SSH tunnel
GET /v1/ai/models — inspect installed models and verify required models (qwen2.5:14b, qwen2.5vl:7b)
"""
from __future__ import annotations

from typing import Any
from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..ai.client import OllamaClient
from .. import config

router = APIRouter(prefix="/v1/ai", tags=["ai"])


class AIHealthResponse(BaseModel):
    status: str
    connected: bool
    base_url: str
    latency_ms: float | None = None
    tunnel_active: bool
    error: str | None = None
    note: str = "Checks connectivity to the company's Ollama instance over the private SSH tunnel."


class AIModelsResponse(BaseModel):
    connected: bool
    base_url: str
    all_expected_present: bool
    expected_models: list[str]
    installed_models: list[str]
    verified_models: list[str]
    missing_models: list[str]
    models: list[dict[str, Any]]
    error: str | None = None
    note: str = "Read-only inspection. Automatic model downloading and inference are disabled."


@router.get("/health", response_model=AIHealthResponse)
def get_ai_health() -> AIHealthResponse:
    """Check connectivity to Ollama via loopback / SSH tunnel."""
    client = OllamaClient()
    result = client.check_connection()
    return AIHealthResponse(
        status=result["status"],
        connected=result["connected"],
        base_url=result["base_url"],
        latency_ms=result["latency_ms"],
        tunnel_active=result["connected"],
        error=result["error"],
    )


@router.get("/models", response_model=AIModelsResponse)
def get_ai_models() -> AIModelsResponse:
    """Inspect installed Ollama models and verify the presence of qwen2.5:14b and qwen2.5vl:7b."""
    client = OllamaClient()
    result = client.verify_models()
    return AIModelsResponse(
        connected=result["connected"],
        base_url=result["base_url"],
        all_expected_present=result["all_expected_present"],
        expected_models=result["expected_models"],
        installed_models=result["installed_models"],
        verified_models=result["verified_models"],
        missing_models=result["missing_models"],
        models=result["models"],
        error=result["error"],
    )
