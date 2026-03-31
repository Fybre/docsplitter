"""FastAPI dependencies shared across routers."""

from __future__ import annotations

from fastapi import Request

from docsplitter.channelstore import ChannelStore
from docsplitter.pipeline import Pipeline


def get_pipeline(request: Request) -> Pipeline:
    """Dependency: returns the pipeline from app.state (set during lifespan)."""
    return request.app.state.pipeline


def get_channel_store(request: Request) -> ChannelStore:
    """Dependency: returns the shared ChannelStore from app.state."""
    return request.app.state.channel_store
