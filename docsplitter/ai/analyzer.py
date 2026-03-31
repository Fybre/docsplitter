"""
Page-by-page boundary detection using a sliding window of page images.

Strategy:
  - Page 0: classify type only (always a new document)
  - Pages 1..N-1: send (prev_page, current_page[, next_page]) for boundary decision
  - Post-process: group consecutive pages into DocumentBoundary objects
  - Return a SplitPlan with min_confidence across all boundaries
"""

from __future__ import annotations

import logging
from pathlib import Path

from docsplitter.ai.client import AIClient
from docsplitter.ai.prompts import (
    SYSTEM_PROMPT,
    build_boundary_prompt,
    build_first_page_prompt,
)
from docsplitter.models import DocumentBoundary, PageAnalysisResult, SplitPlan

logger = logging.getLogger(__name__)

# Sentinel used when a page analysis fails
_FAILED_CONFIDENCE = 0.0


class PageAnalyzer:
    """Analyses all pages of a document and returns a SplitPlan."""

    def __init__(self, client: AIClient) -> None:
        self._client = client

    async def analyse(
        self,
        source_file: str,
        page_images: list[Path],
        page_texts: list[str] | None = None,
        type_hints: list[str] | None = None,
        split_trigger_types: list[str] | None = None,
    ) -> SplitPlan:
        """
        Analyse all pages and return a SplitPlan with detected boundaries.

        page_images: list of rendered page image Paths, index == page number (0-based)
        page_texts:  extracted text per page (same length); empty string = none available
        """
        if not page_images:
            raise ValueError("No pages to analyse")

        hints = type_hints or []
        texts = page_texts or [""] * len(page_images)
        total = len(page_images)
        logger.info("Analysing %d pages from %s", total, source_file)

        results: list[PageAnalysisResult] = []

        # Page 0 — always a new document, classify type only
        result0 = await self._analyse_first_page(0, page_images[0], texts[0], hints)
        results.append(result0)
        logger.debug("Page 0: type=%s conf=%.2f", result0.document_type, result0.confidence)

        # Pages 1..N-1 — boundary detection with sliding window
        for i in range(1, total):
            prev_img = page_images[i - 1]
            curr_img = page_images[i]
            next_img = page_images[i + 1] if i + 1 < total else None
            prev_text = texts[i - 1]
            curr_text = texts[i]

            result = await self._analyse_boundary(
                i, prev_img, curr_img, next_img, prev_text, curr_text, hints
            )
            results.append(result)
            logger.debug(
                "Page %d: new=%s type=%s conf=%.2f — %s",
                i,
                result.is_new_document,
                result.document_type,
                result.confidence,
                result.reasoning[:60],
            )

        boundaries = _build_boundaries(results, split_trigger_types or [])
        plan = SplitPlan.from_boundaries(source_file, total, boundaries, self._client.model)

        logger.info(
            "Split plan: %d boundaries, min_confidence=%.2f",
            len(boundaries),
            plan.min_confidence,
        )
        return plan

    async def _analyse_first_page(
        self,
        page_index: int,
        image: Path,
        text: str,
        type_hints: list[str],
    ) -> PageAnalysisResult:
        prompt = build_first_page_prompt(type_hints, text)
        try:
            raw = await self._client.analyse_page(
                system_prompt=SYSTEM_PROMPT,
                user_text=prompt,
                images=[image],
                image_format=_image_format(image),
            )
            return PageAnalysisResult(
                page_index=page_index,
                is_new_document=True,   # always true for page 0
                document_type=raw.get("document_type"),
                confidence=float(raw.get("confidence", _FAILED_CONFIDENCE)),
                reasoning=raw.get("reasoning", ""),
                raw_response=raw,
            )
        except Exception as exc:
            logger.error("Failed to analyse page 0: %s", exc)
            return _failed_result(page_index, is_new_document=True)

    async def _analyse_boundary(
        self,
        page_index: int,
        prev_image: Path,
        curr_image: Path,
        next_image: Path | None,
        prev_text: str,
        curr_text: str,
        type_hints: list[str],
    ) -> PageAnalysisResult:
        images: list[Path] = [prev_image, curr_image]
        prompt_parts = [
            "PREVIOUS PAGE (context only — do not classify this page):\n[image 1]"
            + _text_block(prev_text),
            "\nCURRENT PAGE (classify this one):\n[image 2]"
            + _text_block(curr_text),
        ]

        if next_image is not None:
            images.append(next_image)
            prompt_parts.append("\nNEXT PAGE (context only — do not classify this page):\n[image 3]")

        prompt_parts.append("\n\n" + build_boundary_prompt(type_hints))
        prompt = "\n".join(prompt_parts)

        try:
            raw = await self._client.analyse_page(
                system_prompt=SYSTEM_PROMPT,
                user_text=prompt,
                images=images,
                image_format=_image_format(curr_image),
            )
            return PageAnalysisResult(
                page_index=page_index,
                is_new_document=bool(raw.get("is_new_document", False)),
                document_type=raw.get("document_type"),
                confidence=float(raw.get("confidence", _FAILED_CONFIDENCE)),
                reasoning=raw.get("reasoning", ""),
                raw_response=raw,
            )
        except Exception as exc:
            logger.error("Failed to analyse page %d: %s", page_index, exc)
            return _failed_result(page_index, is_new_document=False)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _image_format(path: Path) -> str:
    ext = path.suffix.lower().lstrip(".")
    return "png" if ext == "png" else "jpeg"


def _text_block(text: str, head: int = 500, tail: int = 300) -> str:
    """
    Format extracted page text for inclusion in the prompt.
    Takes the first `head` and last `tail` characters so that both the document
    header (invoice number, date, supplier) and footer (total, page X of Y) are
    always included regardless of how long the middle body is.
    """
    if not text:
        return ""
    total = head + tail
    if len(text) <= total:
        excerpt = text
    else:
        excerpt = text[:head] + "\n…\n" + text[-tail:]
    return f"\nExtracted text:\n{excerpt}"


def _failed_result(page_index: int, *, is_new_document: bool) -> PageAnalysisResult:
    return PageAnalysisResult(
        page_index=page_index,
        is_new_document=is_new_document,
        document_type=None,
        confidence=_FAILED_CONFIDENCE,
        reasoning="analysis_failed",
    )


def _build_boundaries(
    results: list[PageAnalysisResult],
    split_trigger_types: list[str],
) -> list[DocumentBoundary]:
    """
    Group page results into DocumentBoundary objects.

    When split_trigger_types is empty (default): a new boundary starts on every
    page where is_new_document=True.

    When split_trigger_types is non-empty: a new boundary only starts when
    is_new_document=True AND document_type is in split_trigger_types. Pages that
    the AI flags as new documents but whose type is not a trigger are appended to
    the current boundary instead — this handles the "invoice + supporting docs"
    pattern where you only want to split on the invoice, not the attachments.
    """
    if not results:
        return []

    trigger_set = {t.lower() for t in split_trigger_types}

    boundaries: list[DocumentBoundary] = []
    current_pages: list[PageAnalysisResult] = [results[0]]

    for result in results[1:]:
        is_trigger = (
            result.is_new_document
            and (
                not trigger_set
                or (result.document_type or "").lower() in trigger_set
            )
        )
        if is_trigger:
            boundaries.append(_close_boundary(current_pages))
            current_pages = [result]
        else:
            # Inherit document type from boundary start if current page has none
            if result.document_type is None and current_pages:
                result = result.model_copy(
                    update={"document_type": current_pages[0].document_type}
                )
            current_pages.append(result)

    # Close the final boundary
    boundaries.append(_close_boundary(current_pages))

    return boundaries


def _close_boundary(pages: list[PageAnalysisResult]) -> DocumentBoundary:
    avg_conf = sum(p.confidence for p in pages) / len(pages)
    doc_type = pages[0].document_type  # type is set by the first (header) page
    return DocumentBoundary(
        start_page=pages[0].page_index,
        end_page=pages[-1].page_index,
        document_type=doc_type,
        avg_confidence=avg_conf,
        page_results=pages,
    )
