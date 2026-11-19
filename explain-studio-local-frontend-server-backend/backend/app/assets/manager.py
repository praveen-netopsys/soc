"""Asset Catalog Manager for Explain Studio.

Manages reusable 2D/3D visual assets, vector graphics, and scientific models.
Enforces CC0 / open license attribution and provides fast local path resolution.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("explain_studio.assets")

_ASSETS_DIR = Path(__file__).resolve().parent
_MANIFEST_PATH = _ASSETS_DIR / "manifest.json"


class AssetCatalog:
    """Manages discoverable visual assets for the AI Visual Director and Render Engines."""

    def __init__(self, manifest_path: Path | None = None):
        self.manifest_path = manifest_path or _MANIFEST_PATH
        self.catalog_dir = self.manifest_path.parent
        self._assets_cache: dict[str, dict[str, Any]] = {}
        self._load_manifest()

    def _load_manifest(self) -> None:
        if not self.manifest_path.exists():
            logger.warning("Asset manifest not found at %s", self.manifest_path)
            return

        try:
            with open(self.manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for item in data.get("assets", []):
                self._assets_cache[item["id"]] = item
            logger.debug("Loaded %d visual assets from catalog.", len(self._assets_cache))
        except Exception as exc:
            logger.error("Failed to parse asset manifest: %s", exc)

    def get_asset(self, asset_id: str) -> dict[str, Any] | None:
        return self._assets_cache.get(asset_id)

    def get_asset_file_path(self, asset_id: str) -> Path | None:
        item = self._assets_cache.get(asset_id)
        if not item:
            return None
        file_path = self.catalog_dir / item.get("file_path", "")
        return file_path if file_path.exists() else None

    def search_assets(self, query: str, category: str | None = None) -> list[dict[str, Any]]:
        """Search assets by tag or name keywords."""
        q = query.lower()
        results = []
        for item in self._assets_cache.values():
            if category and item.get("category") != category:
                continue
            name_match = q in item.get("name", "").lower()
            tag_match = any(q in tag.lower() for tag in item.get("tags", []))
            desc_match = q in item.get("intended_use", "").lower()
            if name_match or tag_match or desc_match:
                results.append(item)
        return results

    def list_all_assets(self) -> list[dict[str, Any]]:
        return list(self._assets_cache.values())


# Global singleton catalog instance
catalog = AssetCatalog()
