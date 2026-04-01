"""
In-process job store backed by SQLite.
Tracks all jobs through their lifecycle.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from sqlalchemy import text

from docsplitter.db import get_session
from docsplitter.models import JobRecord, JobStatus, SplitPlan

logger = logging.getLogger(__name__)


class JobStore:
    async def save(self, job: JobRecord) -> None:
        async with get_session() as session:
            await session.execute(
                text("""
                    INSERT OR REPLACE INTO jobs
                        (job_id, channel_name, channel_type, source_path, original_filename,
                         status, split_plan_json, output_paths_json, error,
                         created_at, updated_at)
                    VALUES
                        (:job_id, :channel_name, :channel_type, :source_path, :original_filename,
                         :status, :split_plan_json, :output_paths_json, :error,
                         :created_at, :updated_at)
                """),
                {
                    "job_id": job.job_id,
                    "channel_name": job.channel_name,
                    "channel_type": job.channel_type,
                    "source_path": job.source_path,
                    "original_filename": job.original_filename,
                    "status": job.status.value,
                    "split_plan_json": (
                        job.split_plan.model_dump_json() if job.split_plan else None
                    ),
                    "output_paths_json": json.dumps(job.output_paths),
                    "error": job.error,
                    "created_at": job.created_at.isoformat(),
                    "updated_at": job.updated_at.isoformat(),
                },
            )
            await session.commit()

    async def update_status(
        self,
        job_id: str,
        status: JobStatus,
        *,
        error: str | None = None,
        output_paths: list[str] | None = None,
        split_plan: SplitPlan | None = None,
    ) -> None:
        now = datetime.utcnow().isoformat()
        async with get_session() as session:
            await session.execute(
                text("""
                    UPDATE jobs SET
                        status = :status,
                        error = :error,
                        output_paths_json = COALESCE(:output_paths_json, output_paths_json),
                        split_plan_json = COALESCE(:split_plan_json, split_plan_json),
                        updated_at = :now
                    WHERE job_id = :job_id
                """),
                {
                    "job_id": job_id,
                    "status": status.value,
                    "error": error,
                    "output_paths_json": (
                        json.dumps(output_paths) if output_paths is not None else None
                    ),
                    "split_plan_json": (
                        split_plan.model_dump_json() if split_plan else None
                    ),
                    "now": now,
                },
            )
            await session.commit()

    async def get(self, job_id: str) -> JobRecord | None:
        async with get_session() as session:
            row = (
                await session.execute(
                    text("SELECT * FROM jobs WHERE job_id = :id"), {"id": job_id}
                )
            ).mappings().first()
        return _row_to_job(row) if row else None

    async def delete_terminal(self) -> int:
        """Delete all jobs in a terminal or review state. Returns count deleted."""
        clearable = ("auto_split", "approved", "rejected", "failed", "review")
        placeholders = ", ".join(f"'{s}'" for s in clearable)
        async with get_session() as session:
            # Clean up associated review items first
            await session.execute(
                text(f"DELETE FROM review_items WHERE job_id IN (SELECT job_id FROM jobs WHERE status IN ({placeholders}))")
            )
            result = await session.execute(
                text(f"DELETE FROM jobs WHERE status IN ({placeholders})")
            )
            await session.commit()
        return result.rowcount

    async def list_jobs(
        self,
        status: str | None = None,
        channel_name: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[JobRecord]:
        query = "SELECT * FROM jobs WHERE 1=1"
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
        return [_row_to_job(r) for r in rows]


def _row_to_job(row: dict) -> JobRecord:  # type: ignore[type-arg]
    return JobRecord(
        job_id=row["job_id"],
        channel_name=row["channel_name"],
        channel_type=row["channel_type"],
        source_path=row["source_path"],
        original_filename=row["original_filename"],
        status=JobStatus(row["status"]),
        split_plan=(
            SplitPlan.model_validate_json(row["split_plan_json"])
            if row["split_plan_json"]
            else None
        ),
        output_paths=json.loads(row["output_paths_json"] or "[]"),
        error=row["error"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )
