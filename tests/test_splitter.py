"""Tests for PDF renderer and splitter."""

from __future__ import annotations

from pathlib import Path

import pytest
import pypdfium2 as pdfium

from docsplitter.config import OutputConfig
from docsplitter.models import DocumentBoundary, JobRecord, SplitPlan
from docsplitter.pdf.renderer import PageRenderer
from docsplitter.pdf.splitter import PDFSplitter


def make_pdf(path: Path, num_pages: int) -> Path:
    """Create a minimal PDF with num_pages blank pages."""
    doc = pdfium.PdfDocument.new()
    for _ in range(num_pages):
        page = doc.new_page(595, 842)
        page.close()
    doc.save(str(path))
    doc.close()
    return path


class TestPageRenderer:
    def test_render_pdf(self, tmp_path: Path):
        pdf = make_pdf(tmp_path / "test.pdf", 3)
        renderer = PageRenderer(dpi=72, image_format="jpeg", quality=70)
        images = renderer.render_to_dir(pdf, tmp_path / "pages")
        assert len(images) == 3
        for img in images:
            assert img.exists()
            assert img.suffix == ".jpg"
            assert img.stat().st_size > 0

    def test_render_single_page(self, tmp_path: Path):
        pdf = make_pdf(tmp_path / "test.pdf", 2)
        renderer = PageRenderer(dpi=72)
        img_bytes = renderer.render_page(pdf, 0)
        assert len(img_bytes) > 0

    def test_render_to_tempdir(self, tmp_path: Path):
        pdf = make_pdf(tmp_path / "test.pdf", 2)
        renderer = PageRenderer(dpi=72)
        tmp_dir, pages = renderer.render_to_tempdir(pdf)
        assert len(pages) == 2
        assert tmp_dir.exists()
        import shutil
        shutil.rmtree(tmp_dir)

    def test_unsupported_format(self, tmp_path: Path):
        bad = tmp_path / "file.docx"
        bad.write_bytes(b"fake")
        renderer = PageRenderer()
        with pytest.raises(ValueError, match="Unsupported"):
            renderer.render_to_dir(bad, tmp_path)


class TestPDFSplitter:
    def test_split_into_two(self, tmp_path: Path):
        pdf = make_pdf(tmp_path / "source.pdf", 4)
        cfg = OutputConfig(base_dir=str(tmp_path / "output"))
        splitter = PDFSplitter(cfg)

        job = JobRecord(
            channel_name="test",
            channel_type="api",
            source_path=str(pdf),
            original_filename="source.pdf",
        )
        boundaries = [
            DocumentBoundary(start_page=0, end_page=1, document_type="invoice", avg_confidence=0.9),
            DocumentBoundary(start_page=2, end_page=3, document_type="letter", avg_confidence=0.85),
        ]
        plan = SplitPlan(
            source_file=str(pdf),
            total_pages=4,
            boundaries=boundaries,
            min_confidence=0.85,
            model_used="test",
        )

        output_paths = splitter.split(job, plan)
        assert len(output_paths) == 2

        for path in output_paths:
            assert path.exists()
            assert path.suffix == ".pdf"
            doc = pdfium.PdfDocument(str(path))
            assert len(doc) == 2
            doc.close()

    def test_separator_page_skipped(self, tmp_path: Path):
        pdf = make_pdf(tmp_path / "source.pdf", 3)
        cfg = OutputConfig(base_dir=str(tmp_path / "output"))
        splitter = PDFSplitter(cfg)

        job = JobRecord(
            channel_name="test",
            channel_type="api",
            source_path=str(pdf),
            original_filename="source.pdf",
        )
        boundaries = [
            DocumentBoundary(start_page=0, end_page=0, document_type="invoice", avg_confidence=0.9),
            DocumentBoundary(start_page=1, end_page=1, document_type="separator", avg_confidence=0.95),
            DocumentBoundary(start_page=2, end_page=2, document_type="letter", avg_confidence=0.88),
        ]
        plan = SplitPlan(
            source_file=str(pdf),
            total_pages=3,
            boundaries=boundaries,
            min_confidence=0.88,
            model_used="test",
        )

        output_paths = splitter.split(job, plan)
        # separator should be skipped
        assert len(output_paths) == 2

    def test_metadata_json_written(self, tmp_path: Path):
        pdf = make_pdf(tmp_path / "source.pdf", 2)
        cfg = OutputConfig(
            base_dir=str(tmp_path / "output"),
            write_metadata_json=True,
        )
        splitter = PDFSplitter(cfg)
        job = JobRecord(
            channel_name="test",
            channel_type="api",
            source_path=str(pdf),
            original_filename="source.pdf",
        )
        boundaries = [
            DocumentBoundary(start_page=0, end_page=1, document_type="invoice", avg_confidence=0.9),
        ]
        plan = SplitPlan(
            source_file=str(pdf),
            total_pages=2,
            boundaries=boundaries,
            min_confidence=0.9,
            model_used="test",
        )

        output_paths = splitter.split(job, plan)
        assert len(output_paths) == 1
        meta_path = output_paths[0].with_suffix(".json")
        assert meta_path.exists()

        import json
        meta = json.loads(meta_path.read_text())
        assert meta["document_type"] == "invoice"
        assert meta["page_range_1based"] == [1, 2]
        assert meta["avg_confidence"] == pytest.approx(0.9)
