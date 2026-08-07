"""Explicit bootstrap entry points."""

from __future__ import annotations

import asyncio
import signal

from aiohttp import web

from aiohttp_boilerplate import config
from aiohttp_boilerplate import logging as blogging
from aiohttp_boilerplate.dbpool import pg as db
from aiohttp_boilerplate.logging import access_log, gcp_logger

from .console_app import ConsoleApp, start_console_app
from .web_app import SHUTDOWN_TIMEOUT_KEY, create_app, start_web_app

__all__ = ("console_app", "create_app", "get_loop", "start_web_app", "web_app")


def get_loop() -> asyncio.AbstractEventLoop:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop


def console_app(loop: asyncio.AbstractEventLoop | None = None) -> ConsoleApp:
    loop = loop or get_loop()
    conf = loop.run_until_complete(config.load_config())
    db_pool = loop.run_until_complete(db.create_pool(conf.postgres))
    return start_console_app(conf, db_pool, loop)


def web_app() -> None:
    loop = get_loop()
    conf = loop.run_until_complete(config.load_config())
    blogging.setup_global_logger(
        str(conf.log.get("format", "json")), str(conf.log.get("level", "INFO"))
    )
    app = create_app(conf)
    log = gcp_logger.GCPLogger("web_app")
    runner = web.AppRunner(
        app,
        logger=log,
        access_log_class=access_log.AccessLoggerRequestResponse,
        shutdown_timeout=app[SHUTDOWN_TIMEOUT_KEY],
    )
    installed_signals: list[signal.Signals] = []
    try:
        loop.run_until_complete(runner.setup())
        site = web.TCPSite(
            runner,
            host=str(conf.web_run["host"]),
            port=int(conf.web_run["port"]),
        )
        loop.run_until_complete(site.start())
        log.info(
            "starting server",
            extra={"host": conf.web_run["host"], "port": conf.web_run["port"]},
        )
        for stop_signal in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(stop_signal, loop.stop)
            except (NotImplementedError, RuntimeError):
                continue
            installed_signals.append(stop_signal)
        loop.run_forever()
    finally:
        for stop_signal in installed_signals:
            loop.remove_signal_handler(stop_signal)
        loop.run_until_complete(runner.cleanup())
