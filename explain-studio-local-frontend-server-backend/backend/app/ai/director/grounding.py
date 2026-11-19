"""Source Identifier Mapping and Provenance Grounding for Explain Studio.

Resolves, verifies, and normalizes source citations between:
- source_id: original uploaded file or input document identifier.
- content_block_id: specific extracted content block within that source.

Key capabilities:
1. Verifies that content_block_id exists in the ExtractionManifest.
2. Resolves actual parent source_id when content_block_id is provided.
3. Automatically detects and resolves citations where the LLM placed a valid
   content_block_id in the source_id field, avoiding false hallucination flags.
4. Verifies that both identifiers belong together (detecting cross-file mismatches).
5. Preserves and backfills slide_number and asset_reference from the canonical block.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

from ...extractor.models import ContentBlock, ExtractionManifest, SourceMetadata
from ..models import SourceReference

logger = logging.getLogger("explain_studio.ai.director.grounding")


class CitationStatus(str, Enum):
    VALID = "valid"
    RESOLVED_FROM_BLOCK = "resolved_from_block"
    MISMATCHED = "mismatched"
    UNKNOWN_BLOCK = "unknown_block"
    UNKNOWN_SOURCE = "unknown_source"


@dataclass
class CitationResolution:
    status: CitationStatus
    is_valid: bool
    resolved_reference: SourceReference
    error_message: str | None = None
    canonical_source_id: str | None = None
    canonical_block_id: str | None = None


class CitationResolver:
    """Validates and maps citations against an authentic ExtractionManifest."""

    def __init__(self, manifest: ExtractionManifest) -> None:
        self.manifest = manifest
        self.blocks_by_id: dict[str, ContentBlock] = {}
        # 1. Authoritative primary index
        for b in manifest.blocks:
            self.blocks_by_id[b.id] = b
            # Index original ID if present
            orig_id = b.extraction_metadata.get("original_id")
            if orig_id:
                self.blocks_by_id[orig_id] = b

        # 2. Canonical alias indexing (block_1 <-> blk_1, blk-1, etc.)
        for idx, b in enumerate(manifest.blocks):
            canonical_aliases = [
                f"block_{idx + 1}",
                f"blk_{idx + 1}",
                f"block-{idx + 1}",
                f"blk-{idx + 1}",
                f"b_{idx + 1}",
                f"b{idx + 1}",
            ]
            for alias in canonical_aliases:
                if alias not in self.blocks_by_id:
                    self.blocks_by_id[alias] = b

        self.sources_by_id: dict[str, SourceMetadata] = {s.source_id: s for s in manifest.sources}
        self.sources_by_filename: dict[str, SourceMetadata] = {
            s.original_filename: s for s in manifest.sources
        }

        # Valid source tokens include source UUIDs, original filenames, and pasted_text aliases
        self.valid_source_keys: set[str] = (
            set(self.sources_by_id.keys())
            | set(self.sources_by_filename.keys())
            | {
                "pasted_text", "source_pasted_text", "raw_text",
                # DEF-02: hyphenated form used by extractor
                "pasted-text", "pasted_text.txt",
            }
        )

        # DEF-02: synthetic aliases the LLM commonly produces for pasted-text blocks
        self._PASTED_TEXT_ALIASES: frozenset[str] = frozenset({
            "block_pasted_text", "block_0", "pasted_block",
            "text_block", "pasted_text_block", "pasted-text",
        })

    def resolve_reference(
        self,
        ref: SourceReference | dict[str, Any],
        context_name: str = "citation",
    ) -> CitationResolution:
        """
        Verify and resolve a single source reference.
        Distinguishes clearly between source_id (parent document) and content_block_id (extracted block).
        """
        if isinstance(ref, dict):
            raw_source_id = str(ref.get("source_id", "") or "").strip()
            raw_filename = str(ref.get("original_filename", "") or "").strip()
            raw_block_id = str(ref.get("content_block_id", "") or "").strip() or None
            raw_slide = ref.get("slide_number")
            raw_asset = ref.get("asset_reference")
        else:
            raw_source_id = str(ref.source_id or "").strip()
            raw_filename = str(ref.original_filename or "").strip()
            raw_block_id = str(ref.content_block_id or "").strip() or None
            raw_slide = ref.slide_number
            raw_asset = ref.asset_reference

        # ── Case 0 (DEF-02): Synthetic alias resolution for pasted-text sources ──
        is_pasted_alias = (
            (raw_block_id and (raw_block_id in self._PASTED_TEXT_ALIASES or "pasted" in raw_block_id.lower()))
            or (raw_source_id and (raw_source_id in self._PASTED_TEXT_ALIASES or "pasted" in raw_source_id.lower()))
        )
        if is_pasted_alias:
            pasted_blocks = [
                b for b in self.manifest.blocks
                if b.source_id in {"pasted-text", "pasted_text"} or b.original_filename in {"pasted_text.txt", "pasted-text"}
            ]
            if pasted_blocks:
                block = pasted_blocks[0]
                resolved = SourceReference(
                    source_id=block.source_id,
                    original_filename=block.original_filename,
                    slide_number=raw_slide,
                    content_block_id=block.id,
                    asset_reference=raw_asset,
                )
                return CitationResolution(
                    status=CitationStatus.RESOLVED_FROM_BLOCK,
                    is_valid=True,
                    resolved_reference=resolved,
                    canonical_source_id=block.source_id,
                    canonical_block_id=block.id,
                )

        # ── Case 1: content_block_id is explicitly provided and exists in manifest ──
        if raw_block_id and raw_block_id in self.blocks_by_id:
            block = self.blocks_by_id[raw_block_id]
            canonical_source_id = block.source_id
            canonical_filename = block.original_filename
            canonical_slide = raw_slide if raw_slide is not None else block.slide_number
            canonical_asset = raw_asset or block.asset_reference

            # Verify whether provided source_id belongs together with this block
            if raw_source_id:
                # Check for direct match with parent source_id or filename
                is_parent_match = (
                    raw_source_id == canonical_source_id
                    or raw_source_id == canonical_filename
                    or raw_source_id == block.id
                    or raw_source_id in {"pasted_text", "source_pasted_text", "pasted-text", "pasted_text.txt"}
                )

                if is_parent_match:
                    resolved = SourceReference(
                        source_id=canonical_source_id,
                        original_filename=canonical_filename,
                        slide_number=canonical_slide,
                        content_block_id=block.id,
                        asset_reference=canonical_asset,
                    )
                    return CitationResolution(
                        status=CitationStatus.VALID,
                        is_valid=True,
                        resolved_reference=resolved,
                        canonical_source_id=canonical_source_id,
                        canonical_block_id=block.id,
                    )

                # Check if raw_source_id is a different valid source from the manifest
                if raw_source_id in self.valid_source_keys:
                    err = (
                        f"Mismatched citation in {context_name}: content block '{block.id}' belongs to "
                        f"source '{canonical_filename}' ({canonical_source_id}), but was cited under "
                        f"different source '{raw_source_id}'."
                    )
                    resolved = SourceReference(
                        source_id=canonical_source_id,
                        original_filename=canonical_filename,
                        slide_number=canonical_slide,
                        content_block_id=block.id,
                        asset_reference=canonical_asset,
                    )
                    return CitationResolution(
                        status=CitationStatus.MISMATCHED,
                        is_valid=False,
                        resolved_reference=resolved,
                        error_message=err,
                        canonical_source_id=canonical_source_id,
                        canonical_block_id=block.id,
                    )

            # If source_id was omitted or a generic string, resolve to authentic parent
            resolved = SourceReference(
                source_id=canonical_source_id,
                original_filename=canonical_filename,
                slide_number=canonical_slide,
                content_block_id=block.id,
                asset_reference=canonical_asset,
            )
            return CitationResolution(
                status=CitationStatus.VALID,
                is_valid=True,
                resolved_reference=resolved,
                canonical_source_id=canonical_source_id,
                canonical_block_id=block.id,
            )

        # ── Case 2: raw_source_id is actually a valid content_block_id ────────────
        # (This handles the verified Qwen behavior where block ID was returned in source_id)
        if raw_source_id and raw_source_id in self.blocks_by_id:
            block = self.blocks_by_id[raw_source_id]
            canonical_source_id = block.source_id
            canonical_filename = block.original_filename
            canonical_slide = raw_slide if raw_slide is not None else block.slide_number
            canonical_asset = raw_asset or block.asset_reference

            resolved = SourceReference(
                source_id=canonical_source_id,
                original_filename=canonical_filename,
                slide_number=canonical_slide,
                content_block_id=block.id,
                asset_reference=canonical_asset,
            )
            logger.debug(
                "Resolved content_block_id '%s' passed in source_id field to authentic parent '%s'",
                block.id,
                canonical_source_id,
            )
            return CitationResolution(
                status=CitationStatus.RESOLVED_FROM_BLOCK,
                is_valid=True,
                resolved_reference=resolved,
                canonical_source_id=canonical_source_id,
                canonical_block_id=block.id,
            )

        # ── Case 3: raw_source_id is a valid document source identifier ──────────
        if raw_source_id and raw_source_id in self.valid_source_keys:
            # If a block ID was supplied but was not found in Case 1:
            if raw_block_id:
                err = (
                    f"Unknown content_block_id '{raw_block_id}' cited in {context_name}: "
                    f"does not exist in extraction manifest for source '{raw_source_id}'."
                )
                resolved = SourceReference(
                    source_id=raw_source_id,
                    original_filename=raw_filename or raw_source_id,
                    slide_number=raw_slide,
                    content_block_id=raw_block_id,
                    asset_reference=raw_asset,
                )
                return CitationResolution(
                    status=CitationStatus.UNKNOWN_BLOCK,
                    is_valid=False,
                    resolved_reference=resolved,
                    error_message=err,
                    canonical_source_id=raw_source_id,
                    canonical_block_id=raw_block_id,
                )

            # Valid document-level citation
            src_meta = self.sources_by_id.get(raw_source_id) or self.sources_by_filename.get(raw_source_id)
            canonical_source_id = src_meta.source_id if src_meta else raw_source_id
            canonical_filename = src_meta.original_filename if src_meta else (raw_filename or raw_source_id)

            resolved = SourceReference(
                source_id=canonical_source_id,
                original_filename=canonical_filename,
                slide_number=raw_slide,
                content_block_id=None,
                asset_reference=raw_asset,
            )
            return CitationResolution(
                status=CitationStatus.VALID,
                is_valid=True,
                resolved_reference=resolved,
                canonical_source_id=canonical_source_id,
                canonical_block_id=None,
            )

        # ── Case 4: content_block_id was provided but not found anywhere ───────────
        if raw_block_id:
            err = (
                f"Unknown content_block_id '{raw_block_id}' cited in {context_name}: "
                f"does not exist in extraction manifest."
            )
            resolved = SourceReference(
                source_id=raw_source_id or "unknown_source",
                original_filename=raw_filename or "Unknown",
                slide_number=raw_slide,
                content_block_id=raw_block_id,
                asset_reference=raw_asset,
            )
            return CitationResolution(
                status=CitationStatus.UNKNOWN_BLOCK,
                is_valid=False,
                resolved_reference=resolved,
                error_message=err,
                canonical_block_id=raw_block_id,
            )

        # ── Case 5: Neither source_id nor content_block_id matched any record ─────
        err = (
            f"Unknown source reference '{raw_source_id or 'empty'}' in {context_name}: "
            f"matches neither an authentic source file nor an extracted content block."
        )
        resolved = SourceReference(
            source_id=raw_source_id or "unknown_source",
            original_filename=raw_filename or "Unknown",
            slide_number=raw_slide,
            content_block_id=None,
            asset_reference=raw_asset,
        )
        return CitationResolution(
            status=CitationStatus.UNKNOWN_SOURCE,
            is_valid=False,
            resolved_reference=resolved,
            error_message=err,
        )

    def normalize_citation_list(
        self,
        refs: list[SourceReference | dict[str, Any]],
        context_name: str = "citations",
    ) -> tuple[list[SourceReference], list[str], list[str]]:
        """
        Normalize a list of citations:
        Returns:
            (normalized_references, invalid_errors, mismatched_errors)
        """
        normalized: list[SourceReference] = []
        invalid_errors: list[str] = []
        mismatched_errors: list[str] = []

        for idx, r in enumerate(refs):
            res = self.resolve_reference(r, context_name=f"{context_name}[{idx}]")
            normalized.append(res.resolved_reference)
            if res.status in {CitationStatus.UNKNOWN_BLOCK, CitationStatus.UNKNOWN_SOURCE}:
                if res.error_message:
                    invalid_errors.append(res.error_message)
            elif res.status == CitationStatus.MISMATCHED:
                if res.error_message:
                    mismatched_errors.append(res.error_message)

        return normalized, invalid_errors, mismatched_errors
