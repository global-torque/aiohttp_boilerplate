import json
from collections.abc import Mapping
from typing import Any

from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase

from aiohttp_boilerplate import config
from aiohttp_boilerplate.bootstrap import start_web_app
from aiohttp_boilerplate.dbpool import dummy


class UnitTestCase(AioHTTPTestCase):
    """
    UnitTest does not have real connection with database
    We will create dummy connection just so we can check sql calls
    """

    url = "/"

    async def get_application(self) -> web.Application:
        """Override the get_app method to return your application."""
        # it's important to use the loop passed here.
        conf = await config.load_config()

        db_pool = await dummy.create_pool(
            conf=conf["postgres"],
            loop=self.loop,
        )

        app = start_web_app(
            conf=conf,
            db_pool=db_pool,
            loop=self.loop,
        )
        return app

    async def request(
        self,
        url: str,
        method: str,
        data: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> tuple[int, Any]:
        data = data or {}
        headers = headers or {}

        # Do Not share headers and data between tests
        _headers = dict(headers)
        _headers["content-type"] = headers.get("content-type", "application/json")

        _data = json.dumps(data)
        # ToDo
        # Check if we can use moke_mocked_request here
        resp = await self.client.request(
            method,
            url,
            headers=_headers,
            data=_data,
        )

        if "json" in resp.headers.get("content-type", ""):
            data = await resp.json()
            return resp.status, data or {}  # , resp.headers
        else:
            return resp.status, await resp.text()  # , resp.headers
