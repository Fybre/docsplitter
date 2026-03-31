"""API-layer request/response schemas (separate from internal models)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from docsplitter.models import DocumentBoundary, JobStatus, ReviewStatus


# ── Jobs ──────────────────────────────────────────────────────────────────────


class JobResponse(BaseModel):
    job_id: str
    channel_name: str
    original_filename: str
    status: JobStatus
    output_paths: list[str]
    error: str | None
    created_at: datetime
    updated_at: datetime


class JobListResponse(BaseModel):
    items: list[JobResponse]
    total: int
    limit: int
    offset: int


# ── Upload ────────────────────────────────────────────────────────────────────


class UploadResponse(BaseModel):
    job_id: str
    channel_name: str
    original_filename: str
    message: str = "File accepted for processing"


# ── Review Queue ──────────────────────────────────────────────────────────────


class BoundarySummary(BaseModel):
    start_page: int
    end_page: int
    document_type: str | None
    avg_confidence: float
    page_count: int


class ReviewListItem(BaseModel):
    review_id: str
    job_id: str
    channel_name: str
    original_filename: str
    status: ReviewStatus
    boundary_count: int
    min_confidence: float
    created_at: datetime
    resolved_at: datetime | None


class ReviewDetailResponse(BaseModel):
    review_id: str
    job_id: str
    channel_name: str
    original_filename: str
    source_path: str
    status: ReviewStatus
    proposed_boundaries: list[DocumentBoundary]
    adjusted_boundaries: list[DocumentBoundary] | None
    active_boundaries: list[DocumentBoundary]
    notes: str | None
    created_at: datetime
    resolved_at: datetime | None


class ReviewListResponse(BaseModel):
    items: list[ReviewListItem]
    total: int
    limit: int
    offset: int


class ApproveRequest(BaseModel):
    notes: str | None = None


class RejectRequest(BaseModel):
    notes: str | None = None


class AdjustBoundariesRequest(BaseModel):
    boundaries: list[DocumentBoundary] = Field(
        description="Replacement boundary list. Order matters — pages must be contiguous."
    )


class ApproveResponse(BaseModel):
    review_id: str
    status: ReviewStatus
    output_paths: list[str]
    message: str


class ReviewStatsResponse(BaseModel):
    by_channel: dict[str, dict[str, int]]


# ── Health ────────────────────────────────────────────────────────────────────


class HealthResponse(BaseModel):
    status: str
    ai_reachable: bool
    model: str
    database: str


class ChannelInfo(BaseModel):
    name: str
    type: str
    confidence_threshold: float
    type_hints: list[str]
    path: str | None


class ChannelsResponse(BaseModel):
    channels: list[ChannelInfo]
