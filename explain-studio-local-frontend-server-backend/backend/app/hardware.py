"""Hardware & Execution Environment Capability Manager for Explain Studio.

Inspects system hardware, resources, and dependencies at startup:
- Operating system, architecture, Python version
- CPU cores (physical & logical) and real-time utilization
- RAM (total, available, usage percentage)
- GPU presence, model, total and free VRAM
- Storage capacity and free disk space
- Ollama connectivity, remote/local detection, and installed models
- TTS provider readiness (Edge TTS & offline pyttsx3)

Dynamically configures capability profiles and concurrency limits to ensure
rock-solid stability across varying systems (dev laptop, GPU server, or low-RAM host).
"""
from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import psutil

from . import config

logger = logging.getLogger("explain_studio.hardware")


@dataclass
class GPUInfo:
    name: str
    total_vram_mb: float
    free_vram_mb: float
    device_type: str  # 'nvidia', 'apple_silicon', 'directml', 'cpu'


@dataclass
class HardwareCapabilities:
    # Operating environment
    os_name: str
    os_release: str
    os_arch: str
    python_version: str

    # CPU & RAM
    cpu_physical_cores: int
    cpu_logical_cores: int
    cpu_usage_pct: float
    ram_total_gb: float
    ram_available_gb: float
    ram_used_pct: float

    # Storage
    disk_total_gb: float
    disk_free_gb: float

    # GPU
    has_gpu: bool
    gpus: list[dict[str, Any]]
    primary_gpu: str | None
    primary_vram_mb: float | None

    # AI & Services
    ollama_connected: bool
    ollama_base_url: str
    ollama_latency_ms: float | None
    is_remote_ollama: bool
    available_models: list[str]
    tts_providers: dict[str, bool]

    # Capability Profile
    profile: str  # 'remote_ollama' | 'gpu_high' | 'gpu_standard' | 'cpu_high_ram' | 'low_resource'

    # Dynamically adjusted runtime limits
    audio_concurrency: int
    slide_render_concurrency: int
    translation_concurrency: int
    enable_vision_analysis: bool
    inference_timeout_seconds: float
    recommended_text_model: str
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _detect_gpus() -> list[GPUInfo]:
    """Detect available GPUs cross-platform (NVIDIA, Apple Silicon, or DirectX)."""
    gpus: list[GPUInfo] = []

    # 1. Try PyTorch CUDA if available
    try:
        import torch  # type: ignore
        if torch.cuda.is_available():
            for i in range(torch.cuda.device_count()):
                props = torch.cuda.get_device_properties(i)
                total_mb = round(props.total_memory / (1024 * 1024), 1)
                free_mb = round(torch.cuda.mem_get_info(i)[0] / (1024 * 1024), 1)
                gpus.append(GPUInfo(
                    name=props.name,
                    total_vram_mb=total_mb,
                    free_vram_mb=free_mb,
                    device_type="nvidia",
                ))
            if gpus:
                return gpus
    except Exception:
        pass

    # 2. Try nvidia-smi CLI
    nvidia_smi = shutil.which("nvidia-smi")
    if nvidia_smi:
        try:
            cmd = [
                nvidia_smi,
                "--query-gpu=name,memory.total,memory.free",
                "--format=csv,noheader,nounits",
            ]
            out = subprocess.check_output(cmd, universal_newlines=True, timeout=3.0)
            for line in out.strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 3:
                    name = parts[0]
                    total_mb = float(parts[1]) if parts[1].replace(".", "", 1).isdigit() else 0.0
                    free_mb = float(parts[2]) if parts[2].replace(".", "", 1).isdigit() else 0.0
                    gpus.append(GPUInfo(
                        name=name,
                        total_vram_mb=total_mb,
                        free_vram_mb=free_mb,
                        device_type="nvidia",
                    ))
            if gpus:
                return gpus
        except Exception as e:
            logger.debug("nvidia-smi query failed: %s", e)

    # 3. macOS Apple Silicon MPS check
    if sys.platform == "darwin":
        try:
            import torch  # type: ignore
            if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                vm = psutil.virtual_memory()
                shared_mb = round(vm.total / (1024 * 1024) * 0.75, 1)  # unified memory
                gpus.append(GPUInfo(
                    name="Apple Silicon Unified GPU (MPS)",
                    total_vram_mb=shared_mb,
                    free_vram_mb=round(vm.available / (1024 * 1024) * 0.75, 1),
                    device_type="apple_silicon",
                ))
                return gpus
        except Exception:
            pass

    return gpus


def detect_capabilities() -> HardwareCapabilities:
    """Execute thorough system discovery and determine optimal processing configuration."""
    warnings: list[str] = []

    # OS & Python
    os_name = platform.system()
    os_release = platform.release()
    os_arch = platform.machine()
    python_ver = platform.python_version()

    # CPU
    cpu_phys = psutil.cpu_count(logical=False) or 1
    cpu_log = psutil.cpu_count(logical=True) or cpu_phys
    cpu_usage = psutil.cpu_percent(interval=None)

    # RAM
    vmem = psutil.virtual_memory()
    ram_total_gb = round(vmem.total / (1024 ** 3), 2)
    ram_avail_gb = round(vmem.available / (1024 ** 3), 2)
    ram_used_pct = vmem.percent

    # Storage
    try:
        config.STORAGE_DIR.mkdir(parents=True, exist_ok=True)
        disk = shutil.disk_usage(config.STORAGE_DIR)
        disk_total_gb = round(disk.total / (1024 ** 3), 2)
        disk_free_gb = round(disk.free / (1024 ** 3), 2)
    except Exception as e:
        disk_total_gb = 0.0
        disk_free_gb = 0.0
        warnings.append(f"Storage check warning: {e}")

    # GPU
    gpu_list = _detect_gpus()
    has_gpu = len(gpu_list) > 0
    primary_gpu = gpu_list[0].name if has_gpu else None
    primary_vram_mb = gpu_list[0].total_vram_mb if has_gpu else None

    # TTS
    tts_providers: dict[str, bool] = {
        "edge_tts": False,
        "pyttsx3": False,
    }
    try:
        import edge_tts  # type: ignore
        tts_providers["edge_tts"] = True
    except ImportError:
        warnings.append("edge-tts is not installed. Speech generation will use offline voice.")

    try:
        import pyttsx3  # type: ignore
        tts_providers["pyttsx3"] = True
    except ImportError:
        pass

    # Ollama Connectivity & Models
    ollama_url = config.OLLAMA_BASE_URL
    is_remote = not (
        "localhost" in ollama_url
        or "127.0.0.1:11434" in ollama_url
    )  # Port 11435 is typically the forwarded SSH tunnel

    ollama_conn = False
    ollama_latency: float | None = None
    available_models: list[str] = []

    try:
        from .ai.client import OllamaClient
        client = OllamaClient()
        conn_res = client.check_connection()
        if conn_res.get("connected"):
            ollama_conn = True
            ollama_latency = conn_res.get("latency_ms")
            tags = client.list_models()
            if tags.get("connected"):
                available_models = [m["name"] for m in tags.get("models", [])]
    except Exception as e:
        warnings.append(f"Ollama discovery check: {e}")

    # Capability Profile Selection
    if is_remote and ollama_conn:
        profile = "remote_ollama"
        audio_concurrency = min(cpu_log, 8)
        slide_render_concurrency = min(cpu_phys, 4)
        translation_concurrency = 5
        enable_vision = "qwen2.5vl:7b" in available_models or any("vl" in m for m in available_models)
        inference_timeout = config.OLLAMA_INFERENCE_TIMEOUT_SECONDS
        recommended_text_model = config.OLLAMA_TEXT_MODEL
    elif has_gpu and primary_vram_mb and primary_vram_mb >= 11500:
        profile = "gpu_high"
        audio_concurrency = min(cpu_log, 6)
        slide_render_concurrency = min(cpu_phys, 4)
        translation_concurrency = 4
        enable_vision = True
        inference_timeout = 120.0
        recommended_text_model = "qwen2.5:14b"
    elif has_gpu and primary_vram_mb and primary_vram_mb >= 5500:
        profile = "gpu_standard"
        audio_concurrency = min(cpu_log, 4)
        slide_render_concurrency = min(cpu_phys, 2)
        translation_concurrency = 3
        # In standard 6GB GPU, vision 7B fits sequentially
        enable_vision = True
        inference_timeout = 180.0
        recommended_text_model = "qwen2.5:14b"
    elif ram_total_gb >= 15.0:
        profile = "cpu_high_ram"
        audio_concurrency = min(cpu_log, 4)
        slide_render_concurrency = 2
        translation_concurrency = 2
        enable_vision = False  # Avoid OOM on pure CPU
        inference_timeout = 300.0
        recommended_text_model = "qwen2.5:7b"
        warnings.append("System is running in CPU mode. Vision models will be bypassed to prevent slowdown.")
    else:
        profile = "low_resource"
        audio_concurrency = 2
        slide_render_concurrency = 1
        translation_concurrency = 1
        enable_vision = False
        inference_timeout = 360.0
        recommended_text_model = "qwen2.5:7b"
        warnings.append("Low resource system detected (<16GB RAM, no GPU). Using sequential lightweight execution.")

    return HardwareCapabilities(
        os_name=os_name,
        os_release=os_release,
        os_arch=os_arch,
        python_version=python_ver,
        cpu_physical_cores=cpu_phys,
        cpu_logical_cores=cpu_log,
        cpu_usage_pct=cpu_usage,
        ram_total_gb=ram_total_gb,
        ram_available_gb=ram_avail_gb,
        ram_used_pct=ram_used_pct,
        disk_total_gb=disk_total_gb,
        disk_free_gb=disk_free_gb,
        has_gpu=has_gpu,
        gpus=[asdict(g) for g in gpu_list],
        primary_gpu=primary_gpu,
        primary_vram_mb=primary_vram_mb,
        ollama_connected=ollama_conn,
        ollama_base_url=ollama_url,
        ollama_latency_ms=ollama_latency,
        is_remote_ollama=is_remote,
        available_models=available_models,
        tts_providers=tts_providers,
        profile=profile,
        audio_concurrency=audio_concurrency,
        slide_render_concurrency=slide_render_concurrency,
        translation_concurrency=translation_concurrency,
        enable_vision_analysis=enable_vision,
        inference_timeout_seconds=inference_timeout,
        recommended_text_model=recommended_text_model,
        warnings=warnings,
    )


# Cached singleton
_CACHED_CAPABILITIES: HardwareCapabilities | None = None


def get_hardware_capabilities(force_refresh: bool = False) -> HardwareCapabilities:
    """Return the cached hardware capabilities or execute fresh inspection."""
    global _CACHED_CAPABILITIES
    if _CACHED_CAPABILITIES is None or force_refresh:
        _CACHED_CAPABILITIES = detect_capabilities()
        logger.info(
            "Hardware profile detected: '%s' (GPU: %s, RAM: %.1fGB, Ollama: %s)",
            _CACHED_CAPABILITIES.profile,
            _CACHED_CAPABILITIES.primary_gpu or "None",
            _CACHED_CAPABILITIES.ram_total_gb,
            "connected" if _CACHED_CAPABILITIES.ollama_connected else "disconnected",
        )
    return _CACHED_CAPABILITIES
