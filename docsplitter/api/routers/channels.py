"""Channel configuration CRUD endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Literal

from docsplitter.api.deps import get_channel_store
from docsplitter.channelstore import ChannelCreate, ChannelRecord, ChannelStore, ChannelUpdate

router = APIRouter(prefix="/channels", tags=["channels"])


class ChannelListResponse(BaseModel):
    channels: list[ChannelRecord]
    total: int


@router.get("", response_model=ChannelListResponse)
async def list_channels(
    store: ChannelStore = Depends(get_channel_store),
) -> ChannelListResponse:
    channels = await store.list_all()
    return ChannelListResponse(channels=channels, total=len(channels))


@router.get("/{name}", response_model=ChannelRecord)
async def get_channel(
    name: str,
    store: ChannelStore = Depends(get_channel_store),
) -> ChannelRecord:
    record = await store.get(name)
    if not record:
        raise HTTPException(status_code=404, detail=f"Channel '{name}' not found")
    return record


@router.post("", response_model=ChannelRecord, status_code=201)
async def create_channel(
    data: ChannelCreate,
    store: ChannelStore = Depends(get_channel_store),
) -> ChannelRecord:
    try:
        return await store.create(data)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.put("/{name}", response_model=ChannelRecord)
async def update_channel(
    name: str,
    data: ChannelUpdate,
    store: ChannelStore = Depends(get_channel_store),
) -> ChannelRecord:
    record = await store.update(name, data)
    if not record:
        raise HTTPException(status_code=404, detail=f"Channel '{name}' not found")
    return record


@router.delete("/{name}", status_code=204)
async def delete_channel(
    name: str,
    store: ChannelStore = Depends(get_channel_store),
) -> None:
    deleted = await store.delete(name)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Channel '{name}' not found")
