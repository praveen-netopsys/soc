"""Reusable Ollama API client for remote AI foundation.

Read-only inspection client for connectivity and model discovery.
Strictly adheres to safety constraints:
- Never pulls or downloads models automatically.
- Never performs inference automatically.
- Configurable base URL and timeout.
- Graceful error handling for missing SSH tunnels or closed ports.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any

import httpx

from .. import config

logger = logging.getLogger("explain_studio.ai")


# ── Custom Exceptions ─────────────────────────────────────────────────────────

class OllamaError(Exception):
    """Base exception for Ollama errors."""


class OllamaConnectionError(OllamaError):
    """Raised when Ollama is unreachable (e.g., SSH tunnel is down)."""


class OllamaTimeoutError(OllamaError):
    """Raised when an inference call times out."""


class OllamaInferenceError(OllamaError):
    """Raised when Ollama returns a non-200 or unexpected response."""


class ModelNotFoundError(OllamaError):
    """Raised when the requested model is not installed on the remote instance."""


def _normalize_name(name: str) -> str:
    """Normalize model names for comparison (e.g. 'qwen2.5-vl:7b' vs 'qwen2.5vl:7b')."""
    return re.sub(r"[-_]", "", name.lower())


def _model_matches(expected: str, available_name: str) -> bool:
    """
    Check if an installed model name satisfies an expected model requirement.
    Matches exact names or normalized versions (e.g. qwen2.5vl:7b matches qwen2.5-vl:7b).
    """
    if expected.lower() == available_name.lower():
        return True

    norm_exp = _normalize_name(expected)
    norm_avail = _normalize_name(available_name)

    if norm_exp == norm_avail:
        return True

    if ":" in expected and ":" in available_name:
        exp_repo, exp_tag = expected.split(":", 1)
        avail_repo, avail_tag = available_name.split(":", 1)
        if _normalize_name(exp_repo) == _normalize_name(avail_repo):
            if _normalize_name(avail_tag).startswith(_normalize_name(exp_tag)):
                return True

    return False


class OllamaClient:
    """
    Safe, read-only client for inspecting a local or SSH-tunneled Ollama instance.
    """

    def __init__(
        self,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
        api_key: str | None = None,
    ) -> None:
        self.base_url = (base_url or config.OLLAMA_BASE_URL).rstrip("/")
        self.timeout = timeout_seconds or config.OLLAMA_TIMEOUT_SECONDS
        self.api_key = (api_key or config.QWEN_API_KEY).strip()

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
            headers["X-API-Key"] = self.api_key
        return headers

    def _get(self, path: str) -> httpx.Response:
        """Issue an HTTP GET request to the Ollama/Qwen gateway server."""
        url = f"{self.base_url}{path}"
        with httpx.Client(timeout=self.timeout) as client:
            return client.get(url, headers=self._headers())

    def _post(
        self,
        path: str,
        json_data: dict[str, Any],
        timeout: float | None = None,
    ) -> httpx.Response:
        """Issue an HTTP POST request to the Ollama/Qwen gateway server."""
        url = f"{self.base_url}{path}"
        effective_timeout = timeout if timeout is not None else self.timeout
        with httpx.Client(timeout=effective_timeout) as client:
            return client.post(url, json=json_data, headers=self._headers())

    def check_connection(self) -> dict[str, Any]:
        """
        Probe Ollama / Qwen Gateway instance connectivity and measure response latency.
        """
        start_time = time.perf_counter()
        try:
            # Probe root, health, or tags
            resp = None
            for probe_path in ["/", "/health", "/api/tags"]:
                try:
                    r = self._get(probe_path)
                    if r.status_code < 400:
                        resp = r
                        break
                except Exception:
                    continue

            if resp is None:
                resp = self._get("/")

            latency_ms = round((time.perf_counter() - start_time) * 1000, 2)

            if resp.status_code == 200 or resp.status_code < 400:
                return {
                    "connected": True,
                    "base_url": self.base_url,
                    "latency_ms": latency_ms,
                    "status": "connected",
                    "error": None,
                }
            else:
                return {
                    "connected": False,
                    "base_url": self.base_url,
                    "latency_ms": latency_ms,
                    "status": "unexpected_response",
                    "error": f"Server responded with HTTP {resp.status_code}: {resp.text[:100]}",
                }

        except httpx.ConnectError:
            return {
                "connected": False,
                "base_url": self.base_url,
                "latency_ms": None,
                "status": "unreachable",
                "error": (
                    f"Cannot reach Ollama at {self.base_url}. Ensure the SSH tunnel is active: "
                    f"ssh -N -L 11434:127.0.0.1:11434 user@server"
                ),
            }
        except httpx.TimeoutException:
            return {
                "connected": False,
                "base_url": self.base_url,
                "latency_ms": None,
                "status": "timeout",
                "error": f"Connection to {self.base_url} timed out after {self.timeout}s.",
            }
        except Exception as exc:
            return {
                "connected": False,
                "base_url": self.base_url,
                "latency_ms": None,
                "status": "error",
                "error": f"Unexpected connection error: {exc}",
            }

    def list_models(self) -> dict[str, Any]:
        """
        Retrieve list of installed models via Ollama's GET /api/tags endpoint.
        """
        try:
            resp = self._get("/api/tags")
            if resp.status_code != 200:
                return {
                    "connected": False,
                    "models": [],
                    "error": f"Ollama returned HTTP {resp.status_code}",
                }

            data = resp.json()
            raw_models = data.get("models", [])

            parsed_models = []
            for m in raw_models:
                details = m.get("details", {})
                parsed_models.append({
                    "name": m.get("name", ""),
                    "model": m.get("model", ""),
                    "size_bytes": m.get("size", 0),
                    "digest": m.get("digest", "")[:12],
                    "modified_at": m.get("modified_at", ""),
                    "parameter_size": details.get("parameter_size", "unknown"),
                    "quantization": details.get("quantization_level", "unknown"),
                    "format": details.get("format", "unknown"),
                    "family": details.get("family", "unknown"),
                })

            return {
                "connected": True,
                "models": parsed_models,
                "error": None,
            }

        except httpx.ConnectError:
            return {
                "connected": False,
                "models": [],
                "error": (
                    f"Cannot reach Ollama at {self.base_url}. Ensure SSH tunnel is open: "
                    f"ssh -N -L 11434:127.0.0.1:11434 user@server"
                ),
            }
        except Exception as exc:
            return {
                "connected": False,
                "models": [],
                "error": f"Failed to query Ollama tags: {exc}",
            }

    def verify_models(self, expected_models: list[str] | None = None) -> dict[str, Any]:
        """
        Check if the required AI models are installed on the server.
        Does NOT download or pull missing models.
        """
        expected = expected_models or config.EXPECTED_OLLAMA_MODELS
        tags_result = self.list_models()

        if not tags_result["connected"]:
            return {
                "connected": False,
                "base_url": self.base_url,
                "all_expected_present": False,
                "expected_models": expected,
                "installed_models": [],
                "missing_models": expected,
                "verified_models": [],
                "models": [],
                "error": tags_result["error"],
            }

        installed_names = [m["name"] for m in tags_result["models"]]
        verified: list[str] = []
        missing: list[str] = []

        for exp in expected:
            match = next((name for name in installed_names if _model_matches(exp, name)), None)
            if match:
                verified.append(match)
            else:
                missing.append(exp)

        return {
            "connected": True,
            "base_url": self.base_url,
            "all_expected_present": len(missing) == 0,
            "expected_models": expected,
            "installed_models": installed_names,
            "verified_models": verified,
            "missing_models": missing,
            "models": tags_result["models"],
            "error": None,
        }

    # ── Inference Methods ─────────────────────────────────────────────────────

    def get_running_vram(self) -> dict[str, Any]:
        """Query /api/ps to retrieve active model memory and VRAM usage on the remote GPU."""
        try:
            resp = self._get("/api/ps")
            if resp.status_code == 200:
                data = resp.json()
                models = data.get("models", [])
                total_vram_bytes = sum(m.get("size_vram", 0) for m in models)
                return {
                    "connected": True,
                    "models": [
                        {
                            "name": m.get("name"),
                            "size_bytes": m.get("size", 0),
                            "size_vram_bytes": m.get("size_vram", 0),
                            "size_vram_mb": round(m.get("size_vram", 0) / (1024 * 1024), 2),
                        }
                        for m in models
                    ],
                    "total_vram_bytes": total_vram_bytes,
                    "total_vram_mb": round(total_vram_bytes / (1024 * 1024), 2),
                    "total_vram_gb": round(total_vram_bytes / (1024 * 1024 * 1024), 2),
                }
        except Exception as exc:
            logger.debug("Failed to query /api/ps for VRAM: %s", exc)
        return {
            "connected": False,
            "models": [],
            "total_vram_bytes": 0,
            "total_vram_mb": 0.0,
            "total_vram_gb": 0.0,
        }

    def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        format: str | None = "json",
        options: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """
        Execute chat/completion inference against remote Ollama.
        Supports structured JSON output and image arrays for multimodal models.
        """
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
        }
        if format:
            payload["format"] = format
        if options is not None:
            payload["options"] = options
        else:
            payload["options"] = {
                "num_ctx": config.OLLAMA_NUM_CTX,
                "num_predict": config.OLLAMA_NUM_PREDICT,
                "temperature": config.OLLAMA_TEMPERATURE,
            }

        effective_timeout = timeout or config.OLLAMA_INFERENCE_TIMEOUT_SECONDS
        resp = None
        last_error = None

        endpoints_to_try = ["/api/v1/chat", "/api/chat", "/v1/chat/completions", "/chat", "/recruitment/chat"]
        for ep in endpoints_to_try:
            try:
                if ep == "/api/v1/chat":
                    sys_msg = next((m.get("content", "") for m in messages if m.get("role") == "system"), "")
                    user_msg = "\n\n".join(str(m.get("content", "")) for m in messages if m.get("role") != "system")
                    r = self._post(ep, {"message": user_msg, "system_prompt": sys_msg}, timeout=effective_timeout)
                elif ep == "/v1/chat/completions":
                    openai_payload: dict[str, Any] = {
                        "model": model,
                        "messages": messages,
                        "temperature": config.OLLAMA_TEMPERATURE,
                    }
                    if format == "json":
                        openai_payload["response_format"] = {"type": "json_object"}
                    r = self._post(ep, openai_payload, timeout=effective_timeout)
                elif ep in ("/chat", "/recruitment/chat"):
                    last_msg = messages[-1]["content"] if messages else ""
                    r = self._post(ep, {"message": str(last_msg)}, timeout=effective_timeout)
                else:
                    r = self._post(ep, payload, timeout=effective_timeout)

                if r.status_code == 200:
                    resp = r
                    break
                elif r.status_code != 404:
                    resp = r
                    break
            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                last_error = exc
                continue
            except Exception as exc:
                last_error = exc
                continue

        if resp is None:
            if isinstance(last_error, httpx.ConnectError):
                raise OllamaConnectionError(
                    f"Cannot connect to Qwen gateway at {self.base_url}."
                ) from last_error
            elif isinstance(last_error, httpx.TimeoutException):
                raise OllamaTimeoutError(
                    f"Qwen inference timed out after {effective_timeout}s for model '{model}'."
                ) from last_error
            raise OllamaInferenceError(f"Qwen inference request failed: {last_error}")

        if resp.status_code != 200:
            raise OllamaInferenceError(
                f"Qwen gateway returned HTTP {resp.status_code}: {resp.text[:300]}"
            )

        try:
            data = resp.json()
        except Exception as exc:
            raise OllamaInferenceError(f"Failed to parse Qwen JSON response: {exc}") from exc

        # Extract content from Ollama, OpenAI, or FastAPI format
        content = ""
        if "message" in data and isinstance(data["message"], dict):
            content = data["message"].get("content", "")
        elif "choices" in data and isinstance(data["choices"], list) and len(data["choices"]) > 0:
            content = data["choices"][0].get("message", {}).get("content", "")
        elif "response" in data:
            content = data.get("response", "")
        elif "reply" in data:
            content = data.get("reply", "")
        elif "text" in data:
            content = data.get("text", "")

        return {
            "model": data.get("model", model),
            "content": content,
            "total_duration_ns": data.get("total_duration"),
            "eval_count": data.get("eval_count"),
            "prompt_eval_count": data.get("prompt_eval_count"),
        }

    def generate(
        self,
        *,
        prompt: str,
        model: str | None = None,
        format: str | None = "json",
        options: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Convenience method wrapping chat with a single user message."""
        target_model = model or config.OLLAMA_TEXT_MODEL
        return self.chat(
            model=target_model,
            messages=[{"role": "user", "content": prompt}],
            format=format,
            options=options,
            timeout=timeout,
        )

    # ── Safety Protections ────────────────────────────────────────────────────

    def pull_model(self, *args: Any, **kwargs: Any) -> None:
        """Explicit safety guard: automatic model pulls are strictly forbidden."""
        raise NotImplementedError(
            "Automatic model downloads (ollama pull) are strictly disabled by policy. "
            "Company server models must be managed by the administrator."
        )
