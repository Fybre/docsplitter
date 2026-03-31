"""
Core processing pipeline.
All ingestion channels (watcher, API) delegate here.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path

from docsplitter.ai.analyzer import PageAnalyzer
from docsplitter.ai.client import AIClient
from docsplitter.config import AIConfig, ChannelConfig, OutputConfig
from docsplitter.jobstore import JobStore
from docsplitter.models import JobRecord, JobStatus, SplitPlan
from docsplitter.pdf.extractor import PageTextExtractor
from docsplitter.pdf.renderer import PageRenderer
from docsplitter.pdf.splitter import PDFSplitter
from docsplitter.queue.manager import ReviewQueueManager

logger = logging.getLogger(__name__)


class Pipeline:
    """
    Orchestrates the full render → analyse → split-or-queue flow.
    One Pipeline instance is shared across all channels.
    """

    def __init__(
        self,
        ai_cfg: AIConfig,
        output_cfg: OutputConfig,
    ) -> None:
        self._renderer = PageRenderer(
            dpi=ai_cfg.render_dpi,
            image_format=ai_cfg.image_format,
            quality=ai_cfg.image_quality,
        )
        self._extractor = PageTextExtractor()
        self._analyzer = PageAnalyzer(AIClient(ai_cfg))
        self._splitter = PDFSplitter(output_cfg)
        self._queue = ReviewQueueManager()
        self._jobs = JobStore()

    async def process_file(
        self,
        source_path: Path,
        channel: ChannelConfig,
        original_filename: str | None = None,
        existing_job: JobRecord | None = None,
    ) -> JobRecord:
        """
        Process a single file through the pipeline.
        Pass existing_job if the record was already persisted (e.g. API upload).
        Returns the completed JobRecord.
        """
        if existing_job is not None:
            job = existing_job
        else:
            job = JobRecord(
                channel_name=channel.name,
                channel_type=channel.type,
                source_path=str(source_path),
                original_filename=original_filename or source_path.name,
            )
            await self._jobs.save(job)
        logger.info("Job %s started: %s", job.job_id[:8], job.original_filename)

        tmp_dir: Path | None = None
        try:
            # Mark as processing
            job.status = JobStatus.PROCESSING
            job.touch()
            await self._jobs.update_status(job.job_id, JobStatus.PROCESSING)

            # Step 1: Render pages to images
            tmp_dir = Path(tempfile.mkdtemp(prefix="docsplitter_"))
            page_images = self._renderer.render_to_dir(source_path, tmp_dir)
            logger.info("Rendered %d pages for job %s", len(page_images), job.job_id[:8])

            # Step 2: Extract text (PDF layer or OCR fallback)
            page_texts = self._extractor.extract_all(source_path, page_images)
            logger.info(
                "Extracted text from %d/%d pages for job %s",
                sum(1 for t in page_texts if t),
                len(page_images),
                job.job_id[:8],
            )

            # Step 3: Analyse boundaries
            plan: SplitPlan = await self._analyzer.analyse(
                source_file=str(source_path),
                page_images=page_images,
                page_texts=page_texts,
                type_hints=channel.type_hints,
                split_trigger_types=channel.split_trigger_types,
            )

            logger.info(
                "Job %s: %d boundaries, min_conf=%.2f, threshold=%.2f",
                job.job_id[:8],
                len(plan.boundaries),
                plan.min_confidence,
                channel.confidence_threshold,
            )

            # Step 4: Auto-split or queue for review
            if plan.min_confidence >= channel.confidence_threshold:
                output_paths = self._splitter.split(job, plan)
                job.status = JobStatus.AUTO_SPLIT
                job.split_plan = plan
                job.output_paths = [str(p) for p in output_paths]
                job.touch()
                await self._jobs.update_status(
                    job.job_id,
                    JobStatus.AUTO_SPLIT,
                    split_plan=plan,
                    output_paths=job.output_paths,
                )
                logger.info(
                    "Job %s auto-split into %d documents", job.job_id[:8], len(output_paths)
                )
            else:
                review_item = await self._queue.enqueue(job, plan)
                job.status = JobStatus.REVIEW
                job.split_plan = plan
                job.touch()
                await self._jobs.update_status(
                    job.job_id, JobStatus.REVIEW, split_plan=plan
                )
                logger.info(
                    "Job %s queued for review (review_id=%s, min_conf=%.2f < %.2f)",
                    job.job_id[:8],
                    review_item.review_id[:8],
                    plan.min_confidence,
                    channel.confidence_threshold,
                )

        except Exception as exc:
            logger.exception("Job %s failed: %s", job.job_id[:8], exc)
            job.status = JobStatus.FAILED
            job.error = str(exc)
            job.touch()
            await self._jobs.update_status(job.job_id, JobStatus.FAILED, error=str(exc))
        finally:
            if tmp_dir and tmp_dir.exists():
                shutil.rmtree(tmp_dir, ignore_errors=True)

        return job

    async def complete_review(
        self,
        review_id: str,
        channel: ChannelConfig,
    ) -> list[Path]:
        """
        Called after a review item is approved.
        Fetches the (possibly adjusted) boundaries and writes output PDFs.
        Returns output paths.
        """
        item = await self._queue.get(review_id)
        if not item:
            raise ValueError(f"Review item not found: {review_id}")

        job = await self._jobs.get(item.job_id)
        if not job or not job.split_plan:
            raise ValueError(f"Job or split plan not found for review {review_id}")

        # Build a plan using the active (possibly adjusted) boundaries
        active_plan = job.split_plan.model_copy(
            update={"boundaries": item.active_boundaries}
        )
        output_paths = self._splitter.split(job, active_plan)

        await self._jobs.update_status(
            job.job_id,
            JobStatus.APPROVED,
            output_paths=[str(p) for p in output_paths],
        )
        logger.info(
            "Review %s approved: %d documents written", review_id[:8], len(output_paths)
        )
        return output_paths

    @property
    def renderer(self) -> PageRenderer:
        return self._renderer

    @property
    def jobs(self) -> JobStore:
        return self._jobs

    @property
    def queue(self) -> ReviewQueueManager:
        return self._queue
