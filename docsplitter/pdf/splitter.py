"""
Split a source PDF into multiple output PDFs based on a SplitPlan.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path

import pypdfium2 as pdfium

from docsplitter.config import OutputConfig
from docsplitter.models import DocumentBoundary, JobRecord, OutputMetadata, SplitPlan

logger = logging.getLogger(__name__)


class PDFSplitter:
    def __init__(self, cfg: OutputConfig) -> None:
        self._cfg = cfg

    def split(self, job: JobRecord, plan: SplitPlan) -> list[Path]:
        """
        Write one PDF per boundary in the plan.
        Returns list of output file paths.
        """
        source = Path(plan.source_file)
        if not source.exists():
            raise FileNotFoundError(f"Source file not found: {source}")

        output_dir = self._output_dir(job)
        output_dir.mkdir(parents=True, exist_ok=True)

        source_sha256 = _sha256(source)
        doc = pdfium.PdfDocument(str(source))
        output_paths: list[Path] = []

        try:
            for idx, boundary in enumerate(plan.boundaries):
                # Skip single-page separator documents
                if boundary.document_type == "separator" and boundary.page_count == 1:
                    logger.debug("Skipping separator page at index %d", boundary.start_page)
                    continue

                out_path = self._make_output_path(job, boundary, idx)
                self._write_segment(doc, boundary, out_path)
                output_paths.append(out_path)

                if self._cfg.write_metadata_json:
                    meta = OutputMetadata(
                        source_file=str(source),
                        source_sha256=source_sha256,
                        output_file=str(out_path),
                        document_type=boundary.document_type,
                        page_range_1based=(
                            boundary.start_page + 1,
                            boundary.end_page + 1,
                        ),
                        avg_confidence=boundary.avg_confidence,
                        channel_name=job.channel_name,
                        job_id=job.job_id,
                        model_used=plan.model_used,
                        processed_at=datetime.utcnow(),
                    )
                    meta_path = out_path.with_suffix(".json")
                    meta_path.write_text(
                        json.dumps(meta.model_dump(mode="json"), indent=2)
                    )
                    logger.debug("Wrote metadata: %s", meta_path.name)

                logger.info(
                    "Split %s → %s (pages %d–%d, type=%s, conf=%.2f)",
                    source.name,
                    out_path.name,
                    boundary.start_page + 1,
                    boundary.end_page + 1,
                    boundary.document_type,
                    boundary.avg_confidence,
                )
        finally:
            doc.close()

        return output_paths

    def _write_segment(
        self,
        source_doc: pdfium.PdfDocument,
        boundary: DocumentBoundary,
        out_path: Path,
    ) -> None:
        """Extract a page range from source_doc into a new PDF file."""
        new_doc = pdfium.PdfDocument.new()
        try:
            page_indices = list(range(boundary.start_page, boundary.end_page + 1))
            new_doc.import_pages(source_doc, pages=page_indices)
            new_doc.save(str(out_path))
        finally:
            new_doc.close()

    def _output_dir(self, job: JobRecord) -> Path:
        base = Path(self._cfg.base_dir)
        return base / job.channel_name

    def _make_output_path(
        self,
        job: JobRecord,
        boundary: DocumentBoundary,
        doc_index: int,
    ) -> Path:
        doc_type = (boundary.document_type or "unknown").replace(" ", "_")
        date_str = datetime.utcnow().strftime("%Y%m%d")

        try:
            filename = self._cfg.filename_template.format(
                channel=job.channel_name,
                date=date_str,
                doc_type=doc_type,
                doc_index=doc_index,
                job_id=job.job_id[:8],
            )
        except (KeyError, ValueError):
            filename = f"{date_str}_{doc_type}_{doc_index:03d}.pdf"

        output_dir = self._output_dir(job)
        # Avoid collisions
        path = output_dir / filename
        stem = path.stem
        suffix = path.suffix
        counter = 1
        while path.exists():
            path = output_dir / f"{stem}_{counter}{suffix}"
            counter += 1

        return path


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
