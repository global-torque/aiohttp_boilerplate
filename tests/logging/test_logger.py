import json
import logging
from types import SimpleNamespace

import pytest
from pythonjsonlogger.json import JsonFormatter

from aiohttp_boilerplate.logging import get_logger, setup_global_logger
from aiohttp_boilerplate.logging.access_log import AccessLoggerRequestResponse
from aiohttp_boilerplate.logging.formatters import ColoredFormatter, TxtFormatter
from aiohttp_boilerplate.logging.gcp_logger import GCPLogger


def test_error_log(capsys, monkeypatch):
    monkeypatch.delenv("SERVICE_NAME", raising=False)
    logger = get_logger("tests", logging.INFO, format="json", stack_info=False, stacklevel=0)

    logger.error(
        "Exception when render document: %s",
        "Test error: variable XYZ not defined",
    )

    result_log = json.loads(capsys.readouterr().err)
    del result_log["time"]

    expected_results = {
        "message": "Exception when render document: Test error: variable XYZ not defined",
        "component": "tests",
        "serviceContext": {"service_name": None},
        "level": "error",
        "severity": "ERROR",
    }

    assert result_log == expected_results


def test_info_log(capsys, monkeypatch):
    monkeypatch.delenv("SERVICE_NAME", raising=False)
    logger = get_logger("tests", logging.INFO, format="json", stack_info=False, stacklevel=0)

    logger.info("Hello world!")

    result_log = json.loads(capsys.readouterr().err)
    del result_log["time"]

    expected_results = {
        "message": "Hello world!",
        "component": "tests",
        "serviceContext": {"service_name": None},
        "level": "info",
        "severity": "INFO",
    }

    assert result_log == expected_results


def test_info_log_with_extra_info(capsys, monkeypatch):
    monkeypatch.delenv("SERVICE_NAME", raising=False)
    logger = get_logger(
        "tests",
        logging.INFO,
        format="json",
        stack_info=False,
        stacklevel=0,
        extra_labels={
            "user_id": "123456789",
        },
    )

    logger.info("Upps, something is wrong: %s", "Error: request timeout to stripe")

    result_log = json.loads(capsys.readouterr().err)
    del result_log["time"]

    expected_results = {
        "message": "Upps, something is wrong: Error: request timeout to stripe",
        "component": "tests",
        "serviceContext": {
            "user_id": "123456789",
            "service_name": None,
        },
        "level": "info",
        "severity": "INFO",
    }

    assert result_log == expected_results


def test_access_log_excludes_credentials_and_query_values() -> None:
    events = []

    class Recorder:
        def addFilter(self, value):
            return None

        def log(self, level, message, *, extra):
            events.append((level, message, extra))

    class Request(dict):
        method = "GET"
        path = "/widgets"
        path_qs = "/widgets?token=top-secret"
        headers = {
            "Authorization": "Bearer top-secret",
            "Cookie": "session=top-secret",
            "Referer": "https://example.test/?access_token=top-secret",
            "User-Agent": "pytest",
        }
        remote = "127.0.0.1"
        scheme = "https"

    access_logger = AccessLoggerRequestResponse(Recorder(), "%r")
    access_logger.log(Request(), SimpleNamespace(status=200), 0.01)

    serialized = json.dumps(events)
    assert "top-secret" not in serialized
    assert events[0][2]["serviceContext"]["httpRequest"]["url"] == "/widgets"


@pytest.mark.parametrize(
    ("mode", "formatter_type"),
    [
        ("json", JsonFormatter),
        ("txt", TxtFormatter),
        ("colored", ColoredFormatter),
    ],
)
def test_global_format_and_component_logger_inheritance(mode, formatter_type) -> None:
    original_class = logging.getLoggerClass()
    original_format = GCPLogger.default_format
    try:
        setup_global_logger(mode, "WARNING")
        parent = logging.getLogger(f"tests.global.{mode}")
        child = parent.new_component_logger(f"tests.child.{mode}")

        assert isinstance(parent.handlers[0].formatter, formatter_type)
        assert isinstance(child.handlers[0].formatter, formatter_type)
        assert parent.getEffectiveLevel() == logging.WARNING
    finally:
        logging.setLoggerClass(original_class)
        GCPLogger.default_format = original_format
