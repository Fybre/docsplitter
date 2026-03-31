"""SQLite database setup via SQLAlchemy async."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

_engine = None
_session_factory = None


def init_db(url: str) -> None:
    global _engine, _session_factory
    _engine = create_async_engine(url, echo=False)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    if _session_factory is None:
        raise RuntimeError("Database not initialised — call init_db() first")
    async with _session_factory() as session:
        yield session


async def create_tables() -> None:
    """Create all tables if they don't exist."""
    if _engine is None:
        raise RuntimeError("Database not initialised")
    async with _engine.begin() as conn:
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS review_items (
                review_id       TEXT PRIMARY KEY,
                job_id          TEXT NOT NULL,
                channel_name    TEXT NOT NULL,
                source_path     TEXT NOT NULL,
                original_filename TEXT NOT NULL,
                proposed_json   TEXT NOT NULL,
                adjusted_json   TEXT,
                status          TEXT NOT NULL DEFAULT 'pending',
                notes           TEXT,
                created_at      TEXT NOT NULL,
                resolved_at     TEXT
            )
        """))
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS jobs (
                job_id          TEXT PRIMARY KEY,
                channel_name    TEXT NOT NULL,
                channel_type    TEXT NOT NULL,
                source_path     TEXT NOT NULL,
                original_filename TEXT NOT NULL,
                status          TEXT NOT NULL DEFAULT 'pending',
                split_plan_json TEXT,
                output_paths_json TEXT NOT NULL DEFAULT '[]',
                error           TEXT,
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL
            )
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_review_status
            ON review_items (status, channel_name)
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_jobs_status
            ON jobs (status, channel_name)
        """))
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS channels (
                name                  TEXT PRIMARY KEY,
                type                  TEXT NOT NULL CHECK(type IN ('watcher', 'api')),
                enabled               INTEGER NOT NULL DEFAULT 1,
                output_subdir         TEXT NOT NULL DEFAULT 'default',
                confidence_threshold  REAL NOT NULL DEFAULT 0.80,
                type_hints_json       TEXT NOT NULL DEFAULT '[]',
                path                  TEXT,
                stable_seconds        REAL NOT NULL DEFAULT 2.0,
                include_patterns_json TEXT NOT NULL DEFAULT '["*.pdf","*.PDF"]',
                dirty                 INTEGER NOT NULL DEFAULT 0,
                created_at            TEXT NOT NULL,
                updated_at            TEXT NOT NULL
            )
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_channels_type
            ON channels (type, enabled)
        """))
        # Migrations — ADD COLUMN is idempotent via try/except (SQLite has no IF NOT EXISTS)
        try:
            await conn.execute(text(
                "ALTER TABLE channels ADD COLUMN split_trigger_types_json TEXT NOT NULL DEFAULT '[]'"
            ))
        except Exception:
            pass  # column already exists
        try:
            await conn.execute(text(
                "ALTER TABLE channels ADD COLUMN description TEXT"
            ))
        except Exception:
            pass  # column already exists
