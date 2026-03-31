"""Shared data models for the docsplitter pipeline."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


# ── Enums ─────────────────────────────────────────────────────────────────────


class JobStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    AUTO_SPLIT = "auto_split"   # confidence met; output written automatically
    REVIEW = "review"           # below threshold; queued for human review
    APPROVED = "approved"       # reviewer accepted the split
    REJECTED = "rejected"       # reviewer discarded (no output written)
    FAILED = "failed"


class ReviewStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


# ── AI Analysis ───────────────────────────────────────────────────────────────


class PageAnalysisResult(BaseModel):
    """AI verdict for a single page boundary decision."""

    page_index: int                  # 0-based
    is_new_document: bool
    document_type: str | None        # e.g. "invoice", "transcript", None if unknown
    confidence: float                # 0.0–1.0
    reasoning: str                   # brief explanation (for audit trail / UI)
    raw_response: dict[str, Any] = Field(default_factory=dict)


class DocumentBoundary(BaseModel):
    """A contiguous page range belonging to one logical document."""

    start_page: int                  # 0-based inclusive
    end_page: int                    # 0-based inclusive
    document_type: str | None
    avg_confidence: float
    page_results: list[PageAnalysisResult] = Field(default_factory=list)

    @property
    def page_count(self) -> int:
        return self.end_page - self.start_page + 1


class SplitPlan(BaseModel):
    """Complete boundary plan for one input file."""

    source_file: str
    total_pages: int
    boundaries: list[DocumentBoundary]
    min_confidence: float            # lowest confidence across all boundaries
    model_used: str
    created_at: datetime = Field(default_factory=datetime.utcnow)

    @classmethod
    def from_boundaries(
        cls, source_file: str, total_pages: int, boundaries: list[DocumentBoundary], model: str
    ) -> "SplitPlan":
        min_conf = min((b.avg_confidence for b in boundaries), default=0.0)
        return cls(
            source_file=source_file,
            total_pages=total_pages,
            boundaries=boundaries,
            min_confidence=min_conf,
            model_used=model,
        )


# ── Jobs ──────────────────────────────────────────────────────────────────────


class JobRecord(BaseModel):
    """Tracks the lifecycle of one submitted file through the pipeline."""

    job_id: str = Field(default_factory=lambda: str(uuid4()))
    channel_name: str
    channel_type: str                # "watcher" | "api"
    source_path: str
    original_filename: str
    status: JobStatus = JobStatus.PENDING
    split_plan: SplitPlan | None = None
    output_paths: list[str] = Field(default_factory=list)
    error: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    def touch(self) -> None:
        self.updated_at = datetime.utcnow()


# ── Review Queue ──────────────────────────────────────────────────────────────


class ReviewItem(BaseModel):
    """A job awaiting human review due to low confidence."""

    review_id: str = Field(default_factory=lambda: str(uuid4()))
    job_id: str
    channel_name: str
    source_path: str
    original_filename: str
    proposed_boundaries: list[DocumentBoundary]
    adjusted_boundaries: list[DocumentBoundary] | None = None
    status: ReviewStatus = ReviewStatus.PENDING
    notes: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    resolved_at: datetime | None = None

    @property
    def active_boundaries(self) -> list[DocumentBoundary]:
        """Return adjusted boundaries if set, otherwise proposed."""
        return self.adjusted_boundaries or self.proposed_boundaries


# ── Output Artifacts ──────────────────────────────────────────────────────────


class OutputMetadata(BaseModel):
    """Sidecar JSON written alongside each split PDF."""

    source_file: str
    source_sha256: str
    output_file: str
    document_type: str | None
    page_range_1based: tuple[int, int]   # human-readable 1-based
    avg_confidence: float
    channel_name: str
    job_id: str
    model_used: str
    processed_at: datetime = Field(default_factory=datetime.utcnow)
