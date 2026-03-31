"""Shared test fixtures."""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from docsplitter.config import AppConfig, AIConfig, OutputConfig, DatabaseConfig, ServerConfig, ChannelConfig, set_config
from docsplitter.db import init_db, create_tables


@pytest.fixture(scope="session")
def event_loop_policy():
    return asyncio.DefaultEventLoopPolicy()


@pytest.fixture
def tmp_output(tmp_path: Path) -> Path:
    out = tmp_path / "output"
    out.mkdir()
    return out


@pytest.fixture
def test_config(tmp_path: Path, tmp_output: Path) -> AppConfig:
    db_path = tmp_path / "test.db"
    cfg = AppConfig(
        ai=AIConfig(
            base_url="https://api.openai.com/v1",
            api_key="test-key",
            model="gpt-4o",
            render_dpi=72,   # low DPI for fast tests
        ),
        output=OutputConfig(base_dir=str(tmp_output)),
        database=DatabaseConfig(url=f"sqlite+aiosqlite:///{db_path}"),
        server=ServerConfig(port=8001),
        channels=[
            ChannelConfig(
                name="test_api",
                type="api",
                confidence_threshold=0.80,
                type_hints=["invoice", "letter"],
            ),
            ChannelConfig(
                name="test_watcher",
                type="watcher",
                path=str(tmp_path / "watch"),
                confidence_threshold=0.80,
            ),
        ],
    )
    set_config(cfg)
    return cfg


@pytest_asyncio.fixture
async def db(test_config: AppConfig):
    init_db(test_config.database.url)
    await create_tables()
    yield


@pytest_asyncio.fixture
async def client(test_config: AppConfig, db) -> AsyncGenerator[AsyncClient, None]:
    from asgi_lifespan import LifespanManager
    from docsplitter.api.app import create_app
    app = create_app(test_config)
    async with LifespanManager(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            yield c


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    """Create a minimal 3-page PDF for testing."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument.new()
    for i in range(3):
        page = doc.new_page(595, 842)  # A4
        # We can't easily add text without a font, so leave pages blank
        # Real integration tests should use actual fixture PDFs
        page.close()
    out = tmp_path / "sample.pdf"
    doc.save(str(out))
    doc.close()
    return out
