"""
Folder watcher channel.
Uses watchdog to monitor directories for new files, then submits them to the pipeline.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from watchdog.events import FileCreatedEvent, FileMovedEvent, FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from docsplitter.config import ChannelConfig
from docsplitter.pipeline import Pipeline

logger = logging.getLogger(__name__)


class _Handler(FileSystemEventHandler):
    """Watchdog handler that enqueues new files into the asyncio event loop."""

    def __init__(
        self,
        channel: ChannelConfig,
        pipeline: Pipeline,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._channel = channel
        self._pipeline = pipeline
        self._loop = loop
        # Track files we're already waiting on to avoid duplicate submissions
        self._pending: set[str] = set()

    def on_created(self, event: FileSystemEvent) -> None:
        if not isinstance(event, FileCreatedEvent):
            return
        self._maybe_submit(Path(event.src_path))

    def on_moved(self, event: FileSystemEvent) -> None:
        # Handles atomic moves (rename into watched dir)
        if not isinstance(event, FileMovedEvent):
            return
        self._maybe_submit(Path(event.dest_path))

    def _maybe_submit(self, path: Path) -> None:
        if path.is_dir():
            return
        if not self._matches_pattern(path):
            return
        if str(path) in self._pending:
            return

        self._pending.add(str(path))
        asyncio.run_coroutine_threadsafe(
            self._wait_and_process(path), self._loop
        )

    def _matches_pattern(self, path: Path) -> bool:
        from fnmatch import fnmatch
        return any(fnmatch(path.name, pat) for pat in self._channel.include_patterns)

    async def _wait_and_process(self, path: Path) -> None:
        """Wait for the file to be fully written, then process it."""
        try:
            await _wait_stable(path, self._channel.stable_seconds)
            if not path.exists():
                logger.warning("File disappeared before processing: %s", path)
                return
            logger.info(
                "Watcher [%s]: picked up %s", self._channel.name, path.name
            )
            await self._pipeline.process_file(
                path, self._channel, original_filename=path.name
            )
        except Exception:
            logger.exception("Error processing %s", path)
        finally:
            self._pending.discard(str(path))


async def _wait_stable(path: Path, stable_seconds: float) -> None:
    """Poll until the file size is unchanged for stable_seconds."""
    prev_size = -1
    stable_since: float | None = None

    while True:
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            return

        if size == prev_size:
            if stable_since is None:
                stable_since = time.monotonic()
            elif time.monotonic() - stable_since >= stable_seconds:
                return
        else:
            prev_size = size
            stable_since = None

        await asyncio.sleep(0.5)


class FolderWatcher:
    """Manages watchdog observers for all watcher-type channels."""

    def __init__(self, pipeline: Pipeline) -> None:
        self._pipeline = pipeline
        self._observer: Observer | None = None
        self._channels: list[ChannelConfig] = []

    def add_channel(self, channel: ChannelConfig) -> None:
        assert channel.type == "watcher"
        assert channel.path is not None
        watch_path = Path(channel.path)
        watch_path.mkdir(parents=True, exist_ok=True)
        self._channels.append(channel)
        logger.info("Registered watcher channel '%s' → %s", channel.name, watch_path)

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        self._observer = Observer()
        for channel in self._channels:
            handler = _Handler(channel, self._pipeline, loop)
            self._observer.schedule(handler, str(channel.path), recursive=False)
            logger.info(
                "Watching '%s' (threshold=%.0f%%)",
                channel.path,
                channel.confidence_threshold * 100,
            )
        self._observer.start()
        logger.info("Folder watcher started (%d channels)", len(self._channels))

    def stop(self) -> None:
        if self._observer:
            self._observer.stop()
            self._observer.join()
            logger.info("Folder watcher stopped")
