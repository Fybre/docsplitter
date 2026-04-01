"""Database-backed channel configuration store."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import text

from docsplitter.config import ChannelConfig
from docsplitter.db import get_session

logger = logging.getLogger(__name__)


# ── Models ────────────────────────────────────────────────────────────────────


class ChannelRecord(BaseModel):
    """A channel as stored in the database."""

    name: str
    type: Literal["watcher", "api"]
    description: str | None
    display_name: str | None
    show_on_upload: bool
    enabled: bool
    output_subdir: str
    confidence_threshold: float
    type_hints: list[str]
    split_trigger_types: list[str]
    path: str | None
    stable_seconds: float
    include_patterns: list[str]
    dirty: bool               # True = watcher changed since last startup
    created_at: datetime
    updated_at: datetime

    def to_channel_config(self) -> ChannelConfig:
        return ChannelConfig(
            name=self.name,
            type=self.type,
            output_subdir=self.output_subdir,
            confidence_threshold=self.confidence_threshold,
            type_hints=self.type_hints,
            split_trigger_types=self.split_trigger_types,
            path=self.path,
            stable_seconds=self.stable_seconds,
            include_patterns=self.include_patterns,
        )


class ChannelCreate(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9_-]+$",
                      description="Lowercase slug, e.g. 'invoices' or 'student-records'")
    type: Literal["watcher", "api"]
    description: str | None = None
    display_name: str | None = None
    show_on_upload: bool = True
    enabled: bool = True
    output_subdir: str = "default"
    confidence_threshold: float = Field(default=0.80, ge=0.0, le=1.0)
    type_hints: list[str] = Field(default_factory=list)
    split_trigger_types: list[str] = Field(default_factory=list)
    path: str | None = None
    stable_seconds: float = Field(default=2.0, ge=0.1)
    include_patterns: list[str] = Field(default_factory=lambda: ["*.pdf", "*.PDF"])

    @model_validator(mode="after")
    def watcher_requires_path(self) -> "ChannelCreate":
        if self.type == "watcher" and not self.path:
            raise ValueError("Watcher channels require a 'path'")
        return self


class ChannelUpdate(BaseModel):
    description: str | None = None
    display_name: str | None = None
    show_on_upload: bool | None = None
    enabled: bool | None = None
    output_subdir: str | None = None
    confidence_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    type_hints: list[str] | None = None
    split_trigger_types: list[str] | None = None
    path: str | None = None
    stable_seconds: float | None = Field(default=None, ge=0.1)
    include_patterns: list[str] | None = None


# ── Store ─────────────────────────────────────────────────────────────────────


class ChannelStore:
    async def seed_from_yaml(self, channels: list[ChannelConfig]) -> int:
        """Insert channels from YAML only if the table is empty. Returns count seeded."""
        async with get_session() as session:
            count = (
                await session.execute(text("SELECT COUNT(*) FROM channels"))
            ).scalar()
            if count and count > 0:
                return 0
            now = datetime.utcnow().isoformat()
            for ch in channels:
                await session.execute(
                    text("""
                        INSERT INTO channels
                            (name, type, description, display_name, show_on_upload,
                             enabled, output_subdir, confidence_threshold,
                             type_hints_json, split_trigger_types_json,
                             path, stable_seconds, include_patterns_json,
                             dirty, created_at, updated_at)
                        VALUES
                            (:name, :type, :description, :display_name, 1,
                             1, :output_subdir, :confidence_threshold,
                             :type_hints_json, :split_trigger_types_json,
                             :path, :stable_seconds, :include_patterns_json,
                             0, :now, :now)
                    """),
                    {
                        "name": ch.name,
                        "type": ch.type,
                        "description": None,
                        "display_name": None,
                        "output_subdir": ch.output_subdir,
                        "confidence_threshold": ch.confidence_threshold,
                        "type_hints_json": json.dumps(ch.type_hints),
                        "split_trigger_types_json": json.dumps(ch.split_trigger_types),
                        "path": ch.path,
                        "stable_seconds": ch.stable_seconds,
                        "include_patterns_json": json.dumps(ch.include_patterns),
                        "now": now,
                    },
                )
            await session.commit()
            logger.info("Seeded %d channels from YAML config", len(channels))
            return len(channels)

    async def list_all(self) -> list[ChannelRecord]:
        async with get_session() as session:
            rows = (
                await session.execute(text("SELECT * FROM channels ORDER BY name"))
            ).mappings().all()
        return [_row_to_record(r) for r in rows]

    async def list_enabled(self, type_filter: str | None = None) -> list[ChannelRecord]:
        query = "SELECT * FROM channels WHERE enabled = 1"
        params: dict = {}
        if type_filter:
            query += " AND type = :type"
            params["type"] = type_filter
        query += " ORDER BY name"
        async with get_session() as session:
            rows = (await session.execute(text(query), params)).mappings().all()
        return [_row_to_record(r) for r in rows]

    async def get(self, name: str) -> ChannelRecord | None:
        async with get_session() as session:
            row = (
                await session.execute(
                    text("SELECT * FROM channels WHERE name = :name"), {"name": name}
                )
            ).mappings().first()
        return _row_to_record(row) if row else None

    async def create(self, data: ChannelCreate) -> ChannelRecord:
        now = datetime.utcnow().isoformat()
        async with get_session() as session:
            try:
                await session.execute(
                    text("""
                        INSERT INTO channels
                            (name, type, description, display_name, show_on_upload,
                             enabled, output_subdir, confidence_threshold,
                             type_hints_json, split_trigger_types_json,
                             path, stable_seconds, include_patterns_json,
                             dirty, created_at, updated_at)
                        VALUES
                            (:name, :type, :description, :display_name, :show_on_upload,
                             :enabled, :output_subdir, :confidence_threshold,
                             :type_hints_json, :split_trigger_types_json,
                             :path, :stable_seconds, :include_patterns_json,
                             0, :now, :now)
                    """),
                    {
                        "name": data.name,
                        "type": data.type,
                        "description": data.description,
                        "display_name": data.display_name,
                        "show_on_upload": int(data.show_on_upload),
                        "enabled": int(data.enabled),
                        "output_subdir": data.output_subdir,
                        "confidence_threshold": data.confidence_threshold,
                        "type_hints_json": json.dumps(data.type_hints),
                        "split_trigger_types_json": json.dumps(data.split_trigger_types),
                        "path": data.path,
                        "stable_seconds": data.stable_seconds,
                        "include_patterns_json": json.dumps(data.include_patterns),
                        "now": now,
                    },
                )
                await session.commit()
            except Exception as e:
                if "UNIQUE constraint failed" in str(e):
                    raise ValueError(f"Channel '{data.name}' already exists")
                raise
        record = await self.get(data.name)
        assert record is not None
        return record

    async def update(self, name: str, data: ChannelUpdate) -> ChannelRecord | None:
        existing = await self.get(name)
        if existing is None:
            return None

        # Build SET clause from non-None fields only
        fields: dict = {}
        if data.description is not None:
            fields["description"] = data.description
        if data.display_name is not None:
            fields["display_name"] = data.display_name
        if data.show_on_upload is not None:
            fields["show_on_upload"] = int(data.show_on_upload)
        if data.enabled is not None:
            fields["enabled"] = int(data.enabled)
        if data.output_subdir is not None:
            fields["output_subdir"] = data.output_subdir
        if data.confidence_threshold is not None:
            fields["confidence_threshold"] = data.confidence_threshold
        if data.type_hints is not None:
            fields["type_hints_json"] = json.dumps(data.type_hints)
        if data.split_trigger_types is not None:
            fields["split_trigger_types_json"] = json.dumps(data.split_trigger_types)
        if data.path is not None:
            fields["path"] = data.path
        if data.stable_seconds is not None:
            fields["stable_seconds"] = data.stable_seconds
        if data.include_patterns is not None:
            fields["include_patterns_json"] = json.dumps(data.include_patterns)

        if not fields:
            return existing

        # Watcher channels need a restart for any change to take effect
        if existing.type == "watcher":
            fields["dirty"] = 1

        now = datetime.utcnow().isoformat()
        fields["updated_at"] = now
        set_clause = ", ".join(f"{k} = :{k}" for k in fields)
        fields["name"] = name

        async with get_session() as session:
            await session.execute(
                text(f"UPDATE channels SET {set_clause} WHERE name = :name"), fields
            )
            await session.commit()

        return await self.get(name)

    async def delete(self, name: str) -> bool:
        async with get_session() as session:
            result = await session.execute(
                text("DELETE FROM channels WHERE name = :name"), {"name": name}
            )
            await session.commit()
        return result.rowcount > 0

    async def clear_dirty(self, name: str) -> None:
        """Reset dirty flag after watcher has loaded the channel at startup."""
        async with get_session() as session:
            await session.execute(
                text("UPDATE channels SET dirty = 0 WHERE name = :name"), {"name": name}
            )
            await session.commit()


# ── Helpers ───────────────────────────────────────────────────────────────────


def _row_to_record(row: dict) -> ChannelRecord:  # type: ignore[type-arg]
    return ChannelRecord(
        name=row["name"],
        type=row["type"],
        description=row["description"] if "description" in row.keys() else None,
        display_name=row["display_name"] if "display_name" in row.keys() else None,
        show_on_upload=bool(row["show_on_upload"]) if "show_on_upload" in row.keys() else True,
        enabled=bool(row["enabled"]),
        output_subdir=row["output_subdir"],
        confidence_threshold=row["confidence_threshold"],
        type_hints=json.loads(row["type_hints_json"] or "[]"),
        split_trigger_types=json.loads(row["split_trigger_types_json"] or "[]"),
        path=row["path"],
        stable_seconds=row["stable_seconds"],
        include_patterns=json.loads(row["include_patterns_json"] or '["*.pdf","*.PDF"]'),
        dirty=bool(row["dirty"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )
