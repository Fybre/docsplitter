"""Review queue endpoints."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from docsplitter.api.deps import get_channel_store, get_pipeline
from docsplitter.api.schemas import (
    AdjustBoundariesRequest,
    ApproveRequest,
    ApproveResponse,
    RejectRequest,
    ReviewDetailResponse,
    ReviewListItem,
    ReviewListResponse,
    ReviewStatsResponse,
)
from docsplitter.channelstore import ChannelStore
from docsplitter.config import get_config
from docsplitter.models import ReviewItem, ReviewStatus
from docsplitter.pipeline import Pipeline

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/review", tags=["review"])


def _item_to_list_item(item: ReviewItem) -> ReviewListItem:
    confs = [b.avg_confidence for b in item.proposed_boundaries]
    min_conf = min(confs) if confs else 0.0
    return ReviewListItem(
        review_id=item.review_id,
        job_id=item.job_id,
        channel_name=item.channel_name,
        original_filename=item.original_filename,
        status=item.status,
        boundary_count=len(item.proposed_boundaries),
        min_confidence=min_conf,
        created_at=item.created_at,
        resolved_at=item.resolved_at,
    )


def _item_to_detail(item: ReviewItem) -> ReviewDetailResponse:
    return ReviewDetailResponse(
        review_id=item.review_id,
        job_id=item.job_id,
        channel_name=item.channel_name,
        original_filename=item.original_filename,
        source_path=item.source_path,
        status=item.status,
        proposed_boundaries=item.proposed_boundaries,
        adjusted_boundaries=item.adjusted_boundaries,
        active_boundaries=item.active_boundaries,
        notes=item.notes,
        created_at=item.created_at,
        resolved_at=item.resolved_at,
    )


@router.get("", response_model=ReviewListResponse)
async def list_review_items(
    status: str | None = Query(default="pending"),
    channel: str | None = Query(default=None),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    pipeline: Pipeline = Depends(get_pipeline),
) -> ReviewListResponse:
    items = await pipeline.queue.list_all(
        status=status, channel_name=channel, limit=limit, offset=offset
    )
    return ReviewListResponse(
        items=[_item_to_list_item(i) for i in items],
        total=len(items),
        limit=limit,
        offset=offset,
    )


@router.get("/stats", response_model=ReviewStatsResponse)
async def review_stats(pipeline: Pipeline = Depends(get_pipeline)) -> ReviewStatsResponse:
    stats = await pipeline.queue.stats()
    return ReviewStatsResponse(by_channel=stats)


@router.get("/{review_id}", response_model=ReviewDetailResponse)
async def get_review_item(
    review_id: str,
    pipeline: Pipeline = Depends(get_pipeline),
) -> ReviewDetailResponse:
    item = await pipeline.queue.get(review_id)
    if not item:
        raise HTTPException(status_code=404, detail=f"Review item '{review_id}' not found")
    return _item_to_detail(item)


@router.get("/{review_id}/pages/{page_index}/image")
async def get_page_image(
    review_id: str,
    page_index: int,
    pipeline: Pipeline = Depends(get_pipeline),
) -> Response:
    """Returns the rendered image for a specific page (for review UI)."""
    from pathlib import Path

    item = await pipeline.queue.get(review_id)
    if not item:
        raise HTTPException(status_code=404, detail=f"Review item '{review_id}' not found")

    source = Path(item.source_path)
    if not source.exists():
        raise HTTPException(status_code=410, detail="Source file no longer available")

    cfg = get_config()
    from docsplitter.pdf.renderer import PageRenderer

    renderer = PageRenderer(
        dpi=cfg.ai.render_dpi,
        image_format=cfg.ai.image_format,
        quality=cfg.ai.image_quality,
    )
    try:
        image_bytes = renderer.render_page(source, page_index)
    except (ValueError, IndexError) as e:
        raise HTTPException(status_code=400, detail=str(e))

    media_type = "image/jpeg" if cfg.ai.image_format == "jpeg" else "image/png"
    return Response(content=image_bytes, media_type=media_type)


@router.post("/{review_id}/approve", response_model=ApproveResponse)
async def approve_review(
    review_id: str,
    body: ApproveRequest,
    pipeline: Pipeline = Depends(get_pipeline),
    store: ChannelStore = Depends(get_channel_store),
) -> ApproveResponse:
    """Approve the proposed (or adjusted) split. Triggers output PDF writing."""
    item = await pipeline.queue.get(review_id)
    if not item:
        raise HTTPException(status_code=404, detail=f"Review item '{review_id}' not found")
    if item.status != ReviewStatus.PENDING:
        raise HTTPException(status_code=409, detail=f"Item is already {item.status.value}")

    record = await store.get(item.channel_name)
    if not record:
        raise HTTPException(
            status_code=500, detail=f"Channel '{item.channel_name}' no longer in database"
        )
    channel_cfg = record.to_channel_config()

    source_path = Path(item.source_path)
    output_paths = await pipeline.complete_review(review_id, channel_cfg)
    await pipeline.queue.approve(review_id, notes=body.notes)
    _cleanup_upload_tmp(source_path)

    return ApproveResponse(
        review_id=review_id,
        status=ReviewStatus.APPROVED,
        output_paths=[str(p) for p in output_paths],
        message=f"Split approved. {len(output_paths)} document(s) written.",
    )


@router.post("/{review_id}/reject")
async def reject_review(
    review_id: str,
    body: RejectRequest,
    pipeline: Pipeline = Depends(get_pipeline),
) -> dict:
    """Reject the item. No output is written."""
    item = await pipeline.queue.get(review_id)
    if not item:
        raise HTTPException(status_code=404, detail=f"Review item '{review_id}' not found")
    if item.status != ReviewStatus.PENDING:
        raise HTTPException(status_code=409, detail=f"Item is already {item.status.value}")

    _cleanup_upload_tmp(Path(item.source_path))
    await pipeline.queue.reject(review_id, notes=body.notes)
    from docsplitter.models import JobStatus
    await pipeline.jobs.update_status(item.job_id, JobStatus.REJECTED)
    return {"review_id": review_id, "status": "rejected"}


@router.put("/{review_id}/boundaries", response_model=ReviewDetailResponse)
async def adjust_boundaries(
    review_id: str,
    body: AdjustBoundariesRequest,
    pipeline: Pipeline = Depends(get_pipeline),
) -> ReviewDetailResponse:
    """
    Replace proposed boundaries with reviewer-adjusted ones.
    Does NOT trigger output — call POST /{id}/approve after adjusting.
    """
    item = await pipeline.queue.get(review_id)
    if not item:
        raise HTTPException(status_code=404, detail=f"Review item '{review_id}' not found")
    if item.status != ReviewStatus.PENDING:
        raise HTTPException(status_code=409, detail=f"Item is already {item.status.value}")

    if body.boundaries:
        total_pages = (
            item.proposed_boundaries[-1].end_page + 1 if item.proposed_boundaries else 0
        )
        for b in body.boundaries:
            if b.start_page < 0 or b.end_page >= total_pages or b.start_page > b.end_page:
                raise HTTPException(
                    status_code=422,
                    detail=f"Invalid boundary: pages {b.start_page}–{b.end_page} "
                    f"(document has {total_pages} pages, 0-indexed)",
                )

    updated = await pipeline.queue.adjust_boundaries(review_id, body.boundaries)
    if not updated:
        raise HTTPException(status_code=500, detail="Failed to update boundaries")
    return _item_to_detail(updated)


def _cleanup_upload_tmp(source_path: Path) -> None:
    """Delete the temp directory created by the upload router, if this is an upload job."""
    if "docsplitter_upload_" in str(source_path):
        shutil.rmtree(source_path.parent, ignore_errors=True)
