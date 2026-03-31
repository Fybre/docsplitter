"""
Render PDF pages to image files using pypdfium2.
Also handles TIFF and other multi-page image inputs via Pillow.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image

logger = logging.getLogger(__name__)


class PageRenderer:
    """Renders all pages of a document to JPEG/PNG image files."""

    def __init__(self, dpi: int = 150, image_format: str = "jpeg", quality: int = 85) -> None:
        self._dpi = dpi
        self._format = image_format.lower()
        self._quality = quality
        self._ext = "jpg" if self._format == "jpeg" else "png"

    def render_to_dir(self, source: Path, output_dir: Path) -> list[Path]:
        """
        Render all pages of source into output_dir.
        Returns list of image paths ordered by page number.
        """
        suffix = source.suffix.lower()
        if suffix == ".pdf":
            return self._render_pdf(source, output_dir)
        elif suffix in (".tif", ".tiff"):
            return self._render_tiff(source, output_dir)
        else:
            raise ValueError(f"Unsupported file type: {suffix}")

    def render_to_tempdir(self, source: Path) -> tuple[Path, list[Path]]:
        """
        Render to a managed temp directory.
        Caller is responsible for cleaning up the temp dir.
        Returns (temp_dir, [page_paths]).
        """
        tmp = Path(tempfile.mkdtemp(prefix="docsplitter_"))
        pages = self.render_to_dir(source, tmp)
        return tmp, pages

    def render_page(self, source: Path, page_index: int) -> bytes:
        """Render a single page to bytes (for on-demand review API)."""
        if source.suffix.lower() == ".pdf":
            doc = pdfium.PdfDocument(str(source))
            try:
                return self._render_pdf_page_bytes(doc, page_index)
            finally:
                doc.close()
        raise ValueError(f"Unsupported format for single-page render: {source.suffix}")

    # ── Internal ──────────────────────────────────────────────────────────────

    def _render_pdf(self, source: Path, output_dir: Path) -> list[Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        doc = pdfium.PdfDocument(str(source))
        paths: list[Path] = []
        try:
            for i in range(len(doc)):
                out_path = output_dir / f"page_{i:04d}.{self._ext}"
                raw = self._render_pdf_page_bytes(doc, i)
                out_path.write_bytes(raw)
                paths.append(out_path)
                logger.debug("Rendered PDF page %d → %s", i, out_path.name)
        finally:
            doc.close()
        return paths

    def _render_pdf_page_bytes(self, doc: pdfium.PdfDocument, page_index: int) -> bytes:
        page = doc[page_index]
        scale = self._dpi / 72.0   # pypdfium2 default is 72 dpi
        bitmap = page.render(scale=scale, rotation=0)
        pil_image = bitmap.to_pil()
        page.close()

        import io
        buf = io.BytesIO()
        if self._format == "jpeg":
            pil_image = pil_image.convert("RGB")
            pil_image.save(buf, format="JPEG", quality=self._quality, optimize=True)
        else:
            pil_image.save(buf, format="PNG", optimize=True)
        return buf.getvalue()

    def _render_tiff(self, source: Path, output_dir: Path) -> list[Path]:
        """Render each frame of a multi-page TIFF."""
        import io
        img = Image.open(source)
        paths: list[Path] = []
        frame = 0
        while True:
            out_path = output_dir / f"page_{frame:04d}.{self._ext}"
            buf = io.BytesIO()
            if self._format == "jpeg":
                img.convert("RGB").save(buf, format="JPEG", quality=self._quality)
            else:
                img.save(buf, format="PNG")
            out_path.write_bytes(buf.getvalue())
            paths.append(out_path)
            logger.debug("Rendered TIFF frame %d → %s", frame, out_path.name)
            frame += 1
            try:
                img.seek(frame)
            except EOFError:
                break
        return paths
