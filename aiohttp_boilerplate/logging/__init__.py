"""Explicit structured logging configuration."""

from __future__ import annotations

import logging
import sys
import threading
from collections.abc import Mapping
from typing import Any

from .gcp_logger import GCPLogger


def setup_global_logger(format: str, level: str) -> None:
    """Configure future named loggers without reading application config."""
    GCPLogger.default_format = format
    logging.setLoggerClass(GCPLogger)
    logging.getLogger().setLevel(level.upper())


def get_logger(
    name: str,
    level: str | int = logging.INFO,
    format: str = "json",
    stack_info: bool = False,
    stacklevel: int = 3,
    extra_labels: Mapping[str, Any] | None = None,
) -> GCPLogger:
    """Create an independently configured structured logger."""
    numeric_level = (
        logging._nameToLevel.get(level.upper(), logging.INFO) if isinstance(level, str) else level
    )
    logger = GCPLogger(
        name,
        format=format,
        stack_info=stack_info,
        stacklevel=stacklevel,
        extra_labels=extra_labels,
    )
    logger.setLevel(numeric_level)
    return logger


def install_exception_hooks() -> None:
    """Opt in to process-wide uncaught-exception logging hooks."""

    def except_logging(exc_type: type[BaseException], exc_value: BaseException, tb: Any) -> None:
        logging.error("Uncaught exception", exc_info=(exc_type, exc_value, tb))

    def unraisable_logging(args: Any) -> None:
        logging.error(
            args.err_msg or "Unraisable exception",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    def threading_except_logging(args: threading.ExceptHookArgs) -> None:
        exc_info = (
            (args.exc_type, args.exc_value, args.exc_traceback)
            if args.exc_value is not None
            else (None, None, None)
        )
        logging.error(
            "Uncaught threading exception",
            exc_info=exc_info,
        )

    sys.excepthook = except_logging
    sys.unraisablehook = unraisable_logging
    threading.excepthook = threading_except_logging


__all__ = ("GCPLogger", "get_logger", "install_exception_hooks", "setup_global_logger")
