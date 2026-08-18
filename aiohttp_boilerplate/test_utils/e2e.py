import logging
from typing import Any, cast

from aiohttp import web

from aiohttp_boilerplate import config
from aiohttp_boilerplate import logging as blogging
from aiohttp_boilerplate.bootstrap import start_web_app
from aiohttp_boilerplate.dbpool import pg as db

from .load_fixtures import LoadFixture
from .unit import UnitTestCase


class E2ETestCase(UnitTestCase):
    """
    E2E make real database connection
    Auto fixtures loading
    """

    loaded_fixtures: dict[str, list[dict[str, Any]]] = {}
    fixtures: dict[str, str] = {}

    async def get_application(self) -> web.Application:
        """Override the get_app method to return your application."""
        # it's important to use the loop passed here.
        conf = await config.load_config()
        db_pool = await db.create_pool(
            conf=conf["postgres"],
            loop=self.loop,
        )
        blogging.setup_global_logger(conf["log"]["format"], conf["log"]["level"])

        app = start_web_app(
            conf=conf,
            db_pool=db_pool,
            loop=self.loop,
        )
        return app

    async def setUpAsync(self) -> None:
        await super().setUpAsync()
        if len(self.fixtures.keys()) > 0:
            db_pool = cast(Any, self.app).db_pool
            con = await db_pool.acquire()
            # Truncate all the tables first
            for name, path in self.fixtures.items():
                await self.truncate_table(path, con, name)
            for name, path in self.fixtures.items():
                self.loaded_fixtures[name] = await self.load_fixture(path, con, name)
                # print("Loaded: {}: {}".format(path, len(self.loaded_fixtures[name])))

            await db_pool.release(con)

    async def truncate_table(self, path: str, con: Any, name: str) -> None:

        directory, _file = path.rsplit("/", 1)
        fixture = LoadFixture(_file, directory, name)

        try:
            async with con.transaction():
                # print('Loading {}'.format(path))
                await fixture.truncate(con)
        except Exception as err:
            logging.error("fixture truncate failed", extra={"fixture_path": path})
            raise RuntimeError(f"cannot truncate fixture {path}") from err

    async def load_fixture(self, path: str, con: Any, name: str) -> list[dict[str, Any]]:

        directory, _file = path.rsplit("/", 1)
        fixture = LoadFixture(_file, directory, name)

        try:
            async with con.transaction():
                # print('Loading {}'.format(path))
                await fixture.file2db(con)
        except Exception as err:
            logging.error("fixture load failed", extra={"fixture_path": path})
            raise RuntimeError(f"cannot upload fixture {path}") from err

        return fixture.data
