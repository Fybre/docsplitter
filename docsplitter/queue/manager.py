"""Review queue: persist and manage items awaiting human review."""

from __future__ import annotations

import json
import logging
from datetime import datetime

from sqlalchemy import text

from docsplitter.db import get_session
from docsplitter.models import DocumentBoundary, JobRecord, ReviewItem, ReviewStatus, SplitPlan

logger = logging.getLogger(__name__)


class ReviewQueueManager:
    async def enqueue(self, job: JobRecord, plan: SplitPlan) -> ReviewItem:
        """Add a job to the review queue. Returns the created ReviewItem."""
        item = ReviewItem(
            job_id=job.job_id,
            channel_name=job.channel_name,
            source_path=job.source_path,
            original_filename=job.original_filename,
            proposed_boundaries=plan.boundaries,
        )
        async with get_session() as session:
            await session.execute(
                text("""
                    INSERT INTO review_items
                        (review_id, job_id, channel_name, source_path, original_filename,
                         proposed_json, adjusted_json, status, notes, created_at, resolved_at)
                    VALUES
                        (:review_id, :job_id, :channel_name, :source_path, :original_filename,
                         :proposed_json, NULL, 'pending', NULL, :created_at, NULL)
                """),
                {
                    "review_id": item.review_id,
                    "job_id": item.job_id,
                    "channel_name": item.channel_name,
                    "source_path": item.source_path,
                    "original_filename": item.original_filename,
                    "proposed_json": _serialise_boundaries(item.proposed_boundaries),
                    "created_at": item.created_at.isoformat(),
                },
            )
            await session.commit()
        logger.info("Enqueued review item %s for job %s", item.review_id, job.job_id)
        return item

    async def get_by_job_id(self, job_id: str) -> ReviewItem | None:
        """Return the most recent review item for a given job (if any)."""
        async with get_session() as session:
            row = (
                await session.execute(
                    text("SELECT * FROM review_items WHERE job_id = :job_id ORDER BY created_at DESC LIMIT 1"),
                    {"job_id": job_id},
                )
            ).mappings().first()
        return _row_to_item(row) if row else None

    async def get(self, review_id: str) -> ReviewItem | None:
        async with get_session() as session:
            row = (
                await session.execute(
                    text("SELECT * FROM review_items WHERE review_id = :id"),
                    {"id": review_id},
                )
            ).mappings().first()
        return _row_to_item(row) if row else None

    async def list_pending(
        self,
        channel_name: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ReviewItem]:
        query = "SELECT * FROM review_items WHERE status = 'pending'"
        params: dict = {"limit": limit, "offset": offset}
        if channel_name:
            query += " AND channel_name = :channel_name"
            params["channel_name"] = channel_name
        query += " ORDER BY created_at ASC LIMIT :limit OFFSET :offset"

        async with get_session() as session:
            rows = (await session.execute(text(query), params)).mappings().all()
        return [_row_to_item(r) for r in rows]

    async def list_all(
        self,
        status: str | None = None,
        channel_name: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ReviewItem]:
        query = "SELECT * FROM review_items WHERE 1=1"
        params: dict = {"limit": limit, "offset": offset}
        if status:
            query += " AND status = :status"
            params["status"] = status
        if channel_name:
            query += " AND channel_name = :channel_name"
            params["channel_name"] = channel_name
        query += " ORDER BY created_at DESC LIMIT :limit OFFSET :offset"

        async with get_session() as session:
            rows = (await session.execute(text(query), params)).mappings().all()
        return [_row_to_item(r) for r in rows]

    async def approve(self, review_id: str, notes: str | None = None) -> ReviewItem | None:
        """Mark item as approved (using adjusted boundaries if set, else proposed)."""
        now = datetime.utcnow().isoformat()
        async with get_session() as session:
            await session.execute(
                text("""
                    UPDATE review_items
                    SET status = 'approved', notes = :notes, resolved_at = :now
                    WHERE review_id = :id AND status = 'pending'
                """),
                {"id": review_id, "notes": notes, "now": now},
            )
            await session.commit()
        return await self.get(review_id)

    async def reject(self, review_id: str, notes: str | None = None) -> ReviewItem | None:
        """Mark item as rejected — no output will be written."""
        now = datetime.utcnow().isoformat()
        async with get_session() as session:
            await session.execute(
                text("""
                    UPDATE review_items
                    SET status = 'rejected', notes = :notes, resolved_at = :now
                    WHERE review_id = :id AND status = 'pending'
                """),
                {"id": review_id, "notes": notes, "now": now},
            )
            await session.commit()
        return await self.get(review_id)

    async def adjust_boundaries(
        self, review_id: str, boundaries: list[DocumentBoundary]
    ) -> ReviewItem | None:
        """Replace proposed boundaries with reviewer-adjusted ones."""
        async with get_session() as session:
            await session.execute(
                text("""
                    UPDATE review_items
                    SET adjusted_json = :adjusted
                    WHERE review_id = :id AND status = 'pending'
                """),
                {
                    "id": review_id,
                    "adjusted": _serialise_boundaries(boundaries),
                },
            )
            await session.commit()
        return await self.get(review_id)

    async def stats(self) -> dict:
        async with get_session() as session:
            rows = (
                await session.execute(
                    text("""
                        SELECT channel_name, status, COUNT(*) as cnt
                        FROM review_items
                        GROUP BY channel_name, status
                    """)
                )
            ).mappings().all()
        result: dict = {}
        for row in rows:
            ch = row["channel_name"]
            st = row["status"]
            result.setdefault(ch, {})[st] = row["cnt"]
        return result


# ── Helpers ───────────────────────────────────────────────────────────────────


def _serialise_boundaries(boundaries: list[DocumentBoundary]) -> str:
    return json.dumps([b.model_dump(mode="json") for b in boundaries])


def _deserialise_boundaries(s: str | None) -> list[DocumentBoundary] | None:
    if not s:
        return None
    return [DocumentBoundary.model_validate(d) for d in json.loads(s)]


def _row_to_item(row: dict) -> ReviewItem:  # type: ignore[type-arg]
    return ReviewItem(
        review_id=row["review_id"],
        job_id=row["job_id"],
        channel_name=row["channel_name"],
        source_path=row["source_path"],
        original_filename=row["original_filename"],
        proposed_boundaries=_deserialise_boundaries(row["proposed_json"]) or [],
        adjusted_boundaries=_deserialise_boundaries(row["adjusted_json"]),
        status=ReviewStatus(row["status"]),
        notes=row["notes"],
        created_at=datetime.fromisoformat(row["created_at"]),
        resolved_at=(
            datetime.fromisoformat(row["resolved_at"]) if row["resolved_at"] else None
        ),
    )
