from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import signal
import sys
from threading import Event

from aiohttp import web

from seller_agent.control_plane.api import create_control_app
from seller_agent.control_plane.bot import run_control_bot_loop
from seller_agent.control_plane.config import ControlPlaneConfig
from seller_agent.core.job_service import JobService
from seller_agent.core.job_store import DEFAULT_RUNTIME_DB, JobStore
from seller_agent.tasks.registry import default_task_registry


CONTROL_BIND_HOST = "127.0.0.1"
CONTROL_BIND_PORT = 8092


def build_control_application(
    *,
    config: ControlPlaneConfig,
    runtime_db: Path = DEFAULT_RUNTIME_DB,
    data_dir: Path = Path("data"),
) -> web.Application:
    store = JobStore(runtime_db)
    store.initialize()
    registry = default_task_registry()
    service = JobService(
        store=store,
        registry=registry,
        data_dir=data_dir,
        runtime_db=runtime_db,
    )
    return create_control_app(config=config, store=store, service=service)


async def serve_control_plane(
    *,
    config: ControlPlaneConfig,
    runtime_db: Path,
    data_dir: Path,
) -> None:
    app = build_control_application(
        config=config,
        runtime_db=runtime_db,
        data_dir=data_dir,
    )
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, host=CONTROL_BIND_HOST, port=CONTROL_BIND_PORT)
    await site.start()

    shutdown = asyncio.Event()
    thread_stop = Event()
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signal_name, shutdown.set)
        except NotImplementedError:
            pass
    bot_task = asyncio.create_task(
        asyncio.to_thread(
            run_control_bot_loop,
            token=config.bot_token,
            allowed_owner_ids=config.allowed_owner_ids,
            public_app_url=config.public_app_url,
            state_file=config.state_file,
            lock_file=config.lock_file,
            timeout_seconds=20,
            limit=20,
            stop_event=thread_stop,
        )
    )
    stop_task = asyncio.create_task(shutdown.wait())
    try:
        done, _ = await asyncio.wait(
            {bot_task, stop_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if bot_task in done and not shutdown.is_set():
            try:
                bot_task.result()
            except Exception as exc:
                raise RuntimeError("control_bot_polling_failed") from exc
            raise RuntimeError("control_bot_polling_stopped")
    finally:
        thread_stop.set()
        stop_task.cancel()
        if not bot_task.done():
            try:
                await asyncio.wait_for(bot_task, timeout=35)
            except (TimeoutError, asyncio.CancelledError):
                pass
        await runner.cleanup()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Vital Shevron owner control plane")
    parser.add_argument("--runtime-db", default=str(DEFAULT_RUNTIME_DB))
    parser.add_argument("--data-dir", default="data")
    args = parser.parse_args(argv)
    try:
        config = ControlPlaneConfig.from_environment()
        asyncio.run(
            serve_control_plane(
                config=config,
                runtime_db=Path(args.runtime_db),
                data_dir=Path(args.data_dir),
            )
        )
    except (OSError, PermissionError, RuntimeError, ValueError):
        print("control_plane_start_failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
