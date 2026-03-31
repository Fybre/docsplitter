"""
DocSplitter entrypoint.
Starts the FastAPI server and folder watchers together.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

import uvicorn

from docsplitter.config import load_config, set_config
from docsplitter.db import create_tables, init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)


def main() -> None:
    cfg = load_config()
    set_config(cfg)

    init_db(cfg.database.url)

    # Start uvicorn in-process (single worker for SQLite compatibility)
    from docsplitter.api.app import create_app
    from docsplitter.channels.watcher import FolderWatcher
    from docsplitter.pipeline import Pipeline

    app = create_app(cfg)

    # Build a pipeline instance for the watcher (separate from the API's instance,
    # but they share the same SQLite database so jobs are visible across both)
    pipeline = Pipeline(ai_cfg=cfg.ai, output_cfg=cfg.output)
    watcher = FolderWatcher(pipeline)

    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=cfg.server.host,
            port=cfg.server.port,
            log_level="info",
        )
    )

    async def run() -> None:
        await create_tables()

        # Seed channels from YAML to DB (no-op after first run)
        from docsplitter.channelstore import ChannelStore
        channel_store = ChannelStore()
        seeded = await channel_store.seed_from_yaml(cfg.channels)
        if seeded:
            logger.info("Seeded %d channels from YAML", seeded)

        # Load watcher channels from DB
        watcher_channels = await channel_store.list_enabled(type_filter="watcher")
        for record in watcher_channels:
            watcher.add_channel(record.to_channel_config())
            await channel_store.clear_dirty(record.name)

        loop = asyncio.get_running_loop()

        if watcher_channels:
            watcher.start(loop)

        # Graceful shutdown on SIGINT/SIGTERM
        stop_event = asyncio.Event()

        def _signal_handler(*_) -> None:  # type: ignore[misc]
            stop_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _signal_handler)

        logger.info(
            "DocSplitter starting on http://%s:%d", cfg.server.host, cfg.server.port
        )
        logger.info("Watching %d folder channel(s)", len(watcher_channels))

        server_task = asyncio.create_task(server.serve())
        stop_task = asyncio.create_task(stop_event.wait())

        done, pending = await asyncio.wait(
            [server_task, stop_task], return_when=asyncio.FIRST_COMPLETED
        )

        if stop_task in done:
            logger.info("Shutdown signal received")
            server.should_exit = True
            await server_task

        watcher.stop()
        logger.info("DocSplitter stopped")

    asyncio.run(run())


if __name__ == "__main__":
    main()
