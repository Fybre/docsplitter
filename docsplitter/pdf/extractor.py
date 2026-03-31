"""
Per-page text extraction for PDFs and TIFFs.

Strategy (per page):
  1. For PDFs: pull the embedded text layer via pypdfium2.
  2. If the text layer is sparse (< MIN_CHARS), fall back to pytesseract OCR
     on the already-rendered page image.
  3. For TIFFs (no text layer): always OCR.

The returned text is passed to the AI prompt so the model can read invoice
numbers, dates, and totals even if its vision resolution is insufficient.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pypdfium2 as pdfium

logger = logging.getLogger(__name__)

# If a page's text layer has fewer than this many printable characters we treat
# it as a scanned page and run OCR instead.
MIN_CHARS = 80


class PageTextExtractor:
    """Extracts text from each page of a document, OCR-ing where needed."""

    def extract_all(self, source: Path, page_images: list[Path]) -> list[str]:
        """
        Return one text string per page (same length as page_images).
        Empty string means no usable text could be extracted.
        """
        suffix = source.suffix.lower()
        if suffix == ".pdf":
            return self._extract_pdf(source, page_images)
        else:
            # TIFF / other formats — no text layer, always OCR
            return [self._ocr(img) for img in page_images]

    # ── Internal ──────────────────────────────────────────────────────────────

    def _extract_pdf(self, source: Path, page_images: list[Path]) -> list[str]:
        texts: list[str] = []
        doc = pdfium.PdfDocument(str(source))
        try:
            for i, img_path in enumerate(page_images):
                try:
                    page = doc[i]
                    textpage = page.get_textpage()
                    raw = textpage.get_text_range()
                    textpage.close()
                    page.close()
                    cleaned = _clean(raw)
                    if len(cleaned) >= MIN_CHARS:
                        logger.debug("Page %d: text layer (%d chars)", i, len(cleaned))
                        texts.append(cleaned)
                        continue
                except Exception as exc:
                    logger.debug("Page %d: text layer failed (%s), trying OCR", i, exc)

                # Sparse or failed — fall back to OCR
                ocr_text = self._ocr(img_path)
                logger.debug("Page %d: OCR (%d chars)", i, len(ocr_text))
                texts.append(ocr_text)
        finally:
            doc.close()
        return texts

    def _ocr(self, image_path: Path) -> str:
        try:
            import pytesseract
            from PIL import Image
            text = pytesseract.image_to_string(Image.open(image_path), timeout=30)
            return _clean(text)
        except ImportError:
            logger.warning("pytesseract not installed — OCR unavailable")
            return ""
        except Exception as exc:
            logger.warning("OCR failed for %s: %s", image_path.name, exc)
            return ""


# ── Helpers ───────────────────────────────────────────────────────────────────


def _clean(text: str) -> str:
    """Collapse whitespace and strip non-printable characters."""
    text = re.sub(r"[^\x20-\x7E\n]", " ", text)   # keep printable ASCII + newlines
    text = re.sub(r"[ \t]{2,}", " ", text)           # collapse multiple spaces/tabs
    text = re.sub(r"\n{3,}", "\n\n", text)            # collapse excessive blank lines
    return text.strip()
