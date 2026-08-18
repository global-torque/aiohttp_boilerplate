"""Small asyncpg-like resources for downstream application tests."""

from __future__ import annotations

from typing import Any


class Transaction:
    async def __aenter__(self) -> Transaction:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        return None


class Connection:
    def __init__(self) -> None:
        self.return_value: Any = None

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        return self.return_value

    fetch = fetchval = fetchrow = reset = execute

    async def set_type_codec(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def prepare(self, *args: Any, **kwargs: Any) -> Connection:
        return self

    def is_closed(self) -> bool:
        return False

    async def add_listener(self, channel: str, callback: Any) -> Any:
        return self.return_value

    def transaction(self) -> Transaction:
        return Transaction()


class DBPool:
    _max_queries = 100

    def __init__(self) -> None:
        self.connection = Connection()
        self.closed = False

    async def acquire(self) -> Connection:
        return self.connection

    async def release(self, conn: Connection) -> None:
        return None

    async def close(self) -> None:
        self.closed = True

    async def __aenter__(self) -> DBPool:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()


async def create_pool(conf: Any = None, loop: Any = None) -> DBPool:
    return DBPool()
