"""Deprecated compatibility exports for request IDs."""

from aiohttp_boilerplate.middleware.logger_to_request import (
    REQUEST_ID_HEADER as REQUEST_ID_HEADER_SETTING,
)
from aiohttp_boilerplate.middleware.logger_to_request import (
    get_request_id,
)
from aiohttp_boilerplate.middleware.logger_to_request import (
    logger_to_request as x_request_id,
)

__all__ = (
    "GENERATE_REQUEST_ID",
    "REQUEST_ID_HEADER_SETTING",
    "generate_id",
    "get_request_id",
    "x_request_id",
)

GENERATE_REQUEST_ID = True


def generate_id() -> str:
    """Generate a request ID for legacy callers."""
    import uuid

    return uuid.uuid4().hex
