"""GET /api/v1/jobs — job status and listing."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse

from docsplitter.api.deps import get_pipeline
from docsplitter.api.schemas import JobListResponse, JobResponse
from docsplitter.models import JobRecord
from docsplitter.pipeline import Pipeline

router = APIRouter(prefix="/jobs", tags=["jobs"])


def _job_to_response(job: JobRecord) -> JobResponse:
    return JobResponse(
        job_id=job.job_id,
        channel_name=job.channel_name,
        original_filename=job.original_filename,
        status=job.status,
        output_paths=job.output_paths,
        error=job.error,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


@router.delete("", status_code=200)
async def clear_jobs(
    pipeline: Pipeline = Depends(get_pipeline),
) -> dict:
    """Delete all terminal jobs (auto_split, approved, rejected, failed)."""
    count = await pipeline.jobs.delete_terminal()
    return {"deleted": count}


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: str,
    pipeline: Pipeline = Depends(get_pipeline),
) -> JobResponse:
    job = await pipeline.jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    return _job_to_response(job)


@router.get("", response_model=JobListResponse)
async def list_jobs(
    status: str | None = Query(default=None),
    channel: str | None = Query(default=None),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    pipeline: Pipeline = Depends(get_pipeline),
) -> JobListResponse:
    jobs = await pipeline.jobs.list_jobs(
        status=status, channel_name=channel, limit=limit, offset=offset
    )
    return JobListResponse(
        items=[_job_to_response(j) for j in jobs],
        total=len(jobs),
        limit=limit,
        offset=offset,
    )


@router.get("/{job_id}/outputs/{index}")
async def download_output(
    job_id: str,
    index: int,
    pipeline: Pipeline = Depends(get_pipeline),
) -> FileResponse:
    """Download a single split output file by index."""
    job = await pipeline.jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    if index < 0 or index >= len(job.output_paths):
        raise HTTPException(status_code=404, detail=f"Output index {index} not found")
    path = Path(job.output_paths[index])
    if not path.exists():
        raise HTTPException(status_code=410, detail="Output file no longer available")
    return FileResponse(
        path=path,
        filename=path.name,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{path.name}"'},
    )


@router.get("/{job_id}/download-zip")
async def download_zip(
    job_id: str,
    pipeline: Pipeline = Depends(get_pipeline),
) -> StreamingResponse:
    """Download all output files for a job as a ZIP archive."""
    job = await pipeline.jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    if not job.output_paths:
        raise HTTPException(status_code=404, detail="No outputs available for this job")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in job.output_paths:
            file_path = Path(p)
            if file_path.exists():
                zf.write(file_path, file_path.name)
    buf.seek(0)

    stem = Path(job.original_filename).stem
    zip_name = f"{stem}_split.zip"
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{zip_name}"'},
    )


@router.get("/{job_id}/review")
async def get_job_review(
    job_id: str,
    pipeline: Pipeline = Depends(get_pipeline),
) -> dict:
    """Return the review item for a job, if one exists."""
    item = await pipeline.queue.get_by_job_id(job_id)
    if not item:
        raise HTTPException(status_code=404, detail=f"No review item found for job '{job_id}'")
    return {
        "review_id": item.review_id,
        "status": item.status.value,
        "boundary_count": len(item.proposed_boundaries),
        "min_confidence": min((b.avg_confidence for b in item.proposed_boundaries), default=0.0),
    }
