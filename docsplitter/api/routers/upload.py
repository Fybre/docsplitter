"""POST /api/v1/ingest/upload — accept a file for processing."""

from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, UploadFile

from docsplitter.api.deps import get_channel_store, get_pipeline
from docsplitter.api.schemas import UploadResponse
from docsplitter.channelstore import ChannelStore
from docsplitter.models import JobRecord
from docsplitter.pipeline import Pipeline

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ingest", tags=["ingestion"])


@router.post("/upload", response_model=UploadResponse, status_code=202)
async def upload_file(
    file: UploadFile,
    background_tasks: BackgroundTasks,
    channel: str = Query(default="general", description="Channel name from config"),
    pipeline: Pipeline = Depends(get_pipeline),
    store: ChannelStore = Depends(get_channel_store),
) -> UploadResponse:
    """
    Upload a PDF (or TIFF) for processing.
    Returns immediately with a job_id. Poll GET /api/v1/jobs/{job_id} for status.
    """
    record = await store.get(channel)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Channel '{channel}' not found")
    if not record.enabled:
        raise HTTPException(status_code=409, detail=f"Channel '{channel}' is disabled")
    if record.type != "api":
        raise HTTPException(
            status_code=400,
            detail=f"Channel '{channel}' is a watcher channel, not an API channel",
        )
    channel_cfg = record.to_channel_config()

    filename = file.filename or "upload.pdf"
    suffix = Path(filename).suffix.lower()
    if suffix not in (".pdf", ".tif", ".tiff"):
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type '{suffix}'. Supported: .pdf, .tif, .tiff",
        )

    # Save upload to a temp file (background task needs a persistent path)
    tmp = Path(tempfile.mkdtemp(prefix="docsplitter_upload_"))
    dest = tmp / filename
    try:
        with open(dest, "wb") as out:
            shutil.copyfileobj(file.file, out)
    finally:
        await file.close()

    # Pre-create the job record so we can return its ID immediately
    job = JobRecord(
        channel_name=channel_cfg.name,
        channel_type=channel_cfg.type,
        source_path=str(dest),
        original_filename=filename,
    )
    await pipeline.jobs.save(job)

    async def _process() -> None:
        try:
            await pipeline.process_file(
                dest, channel_cfg, original_filename=filename, existing_job=job
            )
        except Exception:
            logger.exception("Background processing failed for job %s", job.job_id[:8])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    background_tasks.add_task(_process)
    logger.info("Accepted upload: %s → job %s", filename, job.job_id[:8])

    return UploadResponse(
        job_id=job.job_id,
        channel_name=channel_cfg.name,
        original_filename=filename,
    )
