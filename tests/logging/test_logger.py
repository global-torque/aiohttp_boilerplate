import json
import logging

from aiohttp_boilerplate.logging import get_logger


def test_error_log(capsys, monkeypatch):
    monkeypatch.delenv("SERVICE_NAME", raising=False)
    logger = get_logger("tests", logging.INFO, format="json", stack_info=False, stacklevel=0)

    logger.error(  # noqa: PLE1205 - the extra argument is structured log detail
        "Exception when render document",
        "Test error: variable XYZ not defined",
    )

    result_log = json.loads(capsys.readouterr().err)
    del result_log['time']

    expected_results = {
        "message": "Exception when render document",
        "component": "tests",
        "serviceContext": {"service_name": None},
        "error": "Test error: variable XYZ not defined",
        "level": "error",
        "severity": "ERROR"
    }

    assert result_log == expected_results

def test_info_log(capsys, monkeypatch):
    monkeypatch.delenv("SERVICE_NAME", raising=False)
    logger = get_logger("tests", logging.INFO, format="json", stack_info=False, stacklevel=0)

    logger.info("Hello world!")

    result_log = json.loads(capsys.readouterr().err)
    del result_log['time']

    expected_results = {
        "message": "Hello world!",
        "component": "tests",
        "serviceContext": {"service_name": None},
        "level": "info",
        "severity": "INFO"
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
        }
    )

    logger.info(  # noqa: PLE1205 - the extra argument is structured log detail
        "Upps, something is wrong",
        "Error: request timeout to stripe"
    )

    result_log = json.loads(capsys.readouterr().err)
    del result_log['time']

    expected_results = {
        "message": "Upps, something is wrong",
        "info": "Error: request timeout to stripe",
        "component": "tests",
        "serviceContext": {
            "user_id": "123456789",
            "service_name": None,
        },
        "level": "info",
        "severity": "INFO"
    }

    assert result_log == expected_results
