from __future__ import annotations

from typing import Any

import pytest

from aiohttp_boilerplate.test_utils import UnitTestCase


class EmptyResponse:
    status = 200
    headers: dict[str, str] = {}

    async def text(self) -> str:
        return ""


class EmptyResponseClient:
    async def request(self, *args: Any, **kwargs: Any) -> EmptyResponse:
        return EmptyResponse()


class RequestHarness:
    client = EmptyResponseClient()


@pytest.mark.asyncio
async def test_request_helper_accepts_response_without_content_type() -> None:
    assert await UnitTestCase.request(RequestHarness(), "/resource", "OPTIONS") == (200, "")
