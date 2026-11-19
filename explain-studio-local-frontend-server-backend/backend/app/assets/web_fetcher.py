"""Internet and Local Image Retrieval Service for Explain Studio.

Enables the AI Director and Render Engines to fetch high-resolution, relevant
photographs, diagrams, and educational imagery from open internet repositories
(Wikimedia Commons CC0/Public Domain) and local presentation extractions.
Caches all downloaded assets persistently in storage/web_assets/ for offline reuse.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .. import config, storage

logger = logging.getLogger("explain_studio.assets.web_fetcher")

WEB_ASSETS_DIR = config.STORAGE_DIR / "web_assets"
WEB_ASSETS_DIR.mkdir(parents=True, exist_ok=True)

USER_AGENT = "ExplainStudioBot/1.0 (Corporate Training Production; contact@explainstudio.ai)"


def clean_search_term(raw: str) -> str:
    """Normalize raw title or bullet into a clean image search query."""
    # Strip prefixes like Lesson 1:, Step 2:, Module 3:, bullets, numbers
    q = re.sub(r'^(lesson|module|step|part)\s*\d+[:\-]?\s*', '', raw, flags=re.IGNORECASE)
    q = re.sub(r'^[•\-\*\d\.\s]+', '', q)
    # Strip markdown / punctuation
    q = re.sub(r'[^\w\s]', ' ', q)
    # Reduce whitespace
    tokens = [t for t in q.strip().split() if len(t) > 2 and t.lower() not in {"and", "the", "for", "with", "that", "this", "from", "into"}]
    return " ".join(tokens[:5])


def find_extracted_slide_asset(job_id: str, slide_numbers: list[int] | None = None) -> Path | None:
    """Check if the extracted presentation already has an image on the relevant slides."""
    if not job_id:
        return None
    assets_dir = storage.extracted_dir(job_id) / "assets"
    if not assets_dir.exists():
        return None

    # Check manifest blocks for visual asset references on these slides
    manifest_file = storage.extracted_dir(job_id) / "manifest.json"
    if manifest_file.exists() and slide_numbers:
        try:
            mdata = json.loads(manifest_file.read_text(encoding="utf-8"))
            for b in mdata.get("blocks", []):
                s_num = b.get("slide_number")
                if s_num in slide_numbers and b.get("asset_reference"):
                    ref = b.get("asset_reference")
                    # asset_reference might be assets/slide_1_img_1.png
                    candidate = storage.extracted_dir(job_id) / ref
                    if candidate.exists() and candidate.stat().st_size > 2000:
                        return candidate
        except Exception as e:
            logger.debug("Error checking manifest assets: %s", e)

    # Fallback to any extracted image in assets_dir
    images = sorted(assets_dir.glob("*.png")) + sorted(assets_dir.glob("*.jpg"))
    for img in images:
        if img.stat().st_size > 2000:
            return img
    return None


def fetch_internet_image(query: str, timeout_seconds: float = 6.0) -> Path | None:
    """Search Wikimedia Commons for a CC0/public domain image and cache locally."""
    clean_q = clean_search_term(query)
    if not clean_q or len(clean_q) < 3:
        return None

    q_hash = hashlib.md5(clean_q.lower().encode("utf-8")).hexdigest()
    cached_path = WEB_ASSETS_DIR / f"{q_hash}.jpg"
    if cached_path.exists() and cached_path.stat().st_size > 2000:
        logger.debug("Using cached internet image for '%s': %s", clean_q, cached_path.name)
        return cached_path

    # Construct Wikimedia Commons Search API request
    api_url = (
        "https://commons.wikimedia.org/w/api.php?action=query&format=json"
        f"&generator=search&gsrsearch={urllib.parse.quote(clean_q)}"
        "&gsrnamespace=6&gsrlimit=3&prop=imageinfo&iiprop=url|thumburl|mime|size&iiurlwidth=960"
    )

    try:
        req = urllib.request.Request(api_url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        pages = data.get("query", {}).get("pages", {})
        for pid, p in pages.items():
            imageinfo = p.get("imageinfo", [{}])[0]
            mime = imageinfo.get("mime", "")
            thumburl = imageinfo.get("thumburl")

            # Prioritize standard raster formats
            if mime in ("image/jpeg", "image/png", "image/webp") and thumburl:
                treq = urllib.request.Request(thumburl, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(treq, timeout=timeout_seconds) as tresp:
                    img_bytes = tresp.read()

                if len(img_bytes) > 2000:
                    cached_path.write_bytes(img_bytes)
                    logger.info("Successfully fetched internet image for '%s' -> %s (%d bytes)", clean_q, cached_path.name, len(img_bytes))
                    return cached_path
    except Exception as exc:
        logger.debug("Internet image fetch failed for '%s' (query: %s): %s", query, clean_q, exc)

    return None


def resolve_scene_image(
    scene_title: str,
    key_points: list[str] | None = None,
    job_id: str = "",
    slide_numbers: list[int] | None = None,
) -> Path | None:
    """Resolve the best matching image for a scene (extracted slide asset or internet image)."""
    # 1. Check extracted presentation slides first
    local_asset = find_extracted_slide_asset(job_id, slide_numbers)
    if local_asset:
        logger.info("Using extracted presentation asset for scene '%s': %s", scene_title, local_asset.name)
        return local_asset

    # 2. Try specific queries from title and bullets
    candidates = [scene_title]
    if key_points:
        candidates.extend(key_points[:2])

    for q in candidates:
        if q:
            img = fetch_internet_image(q)
            if img:
                return img

    return None
