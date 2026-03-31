"""API endpoint tests."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health(client: AsyncClient):
    r = await client.get("/api/v1/health")
    assert r.status_code == 200
    data = r.json()
    assert data["model"] == "gpt-4o"
    assert "ai_reachable" in data


@pytest.mark.asyncio
async def test_channels(client: AsyncClient):
    r = await client.get("/api/v1/channels")
    assert r.status_code == 200
    channels = r.json()["channels"]
    names = [ch["name"] for ch in channels]
    assert "test_api" in names
    assert "test_watcher" in names


@pytest.mark.asyncio
async def test_list_jobs_empty(client: AsyncClient):
    r = await client.get("/api/v1/jobs")
    assert r.status_code == 200
    assert r.json()["items"] == []


@pytest.mark.asyncio
async def test_get_job_not_found(client: AsyncClient):
    r = await client.get("/api/v1/jobs/nonexistent-id")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_review_list_empty(client: AsyncClient):
    r = await client.get("/api/v1/review")
    assert r.status_code == 200
    assert r.json()["items"] == []


@pytest.mark.asyncio
async def test_review_stats(client: AsyncClient):
    r = await client.get("/api/v1/review/stats")
    assert r.status_code == 200
    assert "by_channel" in r.json()


@pytest.mark.asyncio
async def test_review_item_not_found(client: AsyncClient):
    r = await client.get("/api/v1/review/nonexistent")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_upload_wrong_channel_type(client: AsyncClient, sample_pdf):
    """Watcher channels should be rejected for API upload."""
    with open(sample_pdf, "rb") as f:
        r = await client.post(
            "/api/v1/ingest/upload?channel=test_watcher",
            files={"file": ("test.pdf", f, "application/pdf")},
        )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_upload_unknown_channel(client: AsyncClient, sample_pdf):
    with open(sample_pdf, "rb") as f:
        r = await client.post(
            "/api/v1/ingest/upload?channel=no_such_channel",
            files={"file": ("test.pdf", f, "application/pdf")},
        )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_upload_wrong_file_type(client: AsyncClient):
    r = await client.post(
        "/api/v1/ingest/upload?channel=test_api",
        files={"file": ("test.docx", b"fake content", "application/octet-stream")},
    )
    assert r.status_code == 415


@pytest.mark.asyncio
async def test_full_review_workflow(client: AsyncClient, db):
    """Test the review queue workflow directly via DB + API."""
    from docsplitter.config import get_config
    from docsplitter.db import init_db, create_tables
    from docsplitter.jobstore import JobStore
    from docsplitter.models import (
        DocumentBoundary, JobRecord, JobStatus, SplitPlan
    )
    from docsplitter.queue.manager import ReviewQueueManager

    cfg = get_config()

    # Create a job and put it in the review queue
    job = JobRecord(
        channel_name="test_api",
        channel_type="api",
        source_path="/tmp/fake.pdf",
        original_filename="fake.pdf",
    )
    store = JobStore()
    await store.save(job)

    boundary = DocumentBoundary(
        start_page=0, end_page=1,
        document_type="invoice",
        avg_confidence=0.6,
    )
    plan = SplitPlan(
        source_file="/tmp/fake.pdf",
        total_pages=2,
        boundaries=[boundary],
        min_confidence=0.6,
        model_used="test",
    )
    queue = ReviewQueueManager()
    item = await queue.enqueue(job, plan)

    # List pending
    r = await client.get("/api/v1/review?status=pending")
    assert r.status_code == 200
    items = r.json()["items"]
    assert any(i["review_id"] == item.review_id for i in items)

    # Get detail
    r = await client.get(f"/api/v1/review/{item.review_id}")
    assert r.status_code == 200
    detail = r.json()
    assert detail["status"] == "pending"
    assert len(detail["proposed_boundaries"]) == 1

    # Adjust boundaries
    new_boundaries = [
        {
            "start_page": 0,
            "end_page": 0,
            "document_type": "invoice",
            "avg_confidence": 0.6,
            "page_results": [],
        },
        {
            "start_page": 1,
            "end_page": 1,
            "document_type": "letter",
            "avg_confidence": 0.55,
            "page_results": [],
        },
    ]
    r = await client.put(
        f"/api/v1/review/{item.review_id}/boundaries",
        json={"boundaries": new_boundaries},
    )
    assert r.status_code == 200
    assert len(r.json()["adjusted_boundaries"]) == 2

    # Reject the item
    r = await client.post(
        f"/api/v1/review/{item.review_id}/reject",
        json={"notes": "test rejection"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "rejected"

    # Should not be in pending list anymore
    r = await client.get("/api/v1/review?status=pending")
    pending_ids = [i["review_id"] for i in r.json()["items"]]
    assert item.review_id not in pending_ids
