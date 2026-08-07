"""PostgreSQL resource creation and JSONB codecs."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

import asyncpg

from aiohttp_boilerplate.views import fixed_dump


def _encoder(value: Any) -> bytes:
    return b"\x01" + fixed_dump(value).encode("utf-8")


def _decoder(value: bytes) -> Any:
    return json.loads(re.sub(rb"(\n|\t|\x01)", b"", value).decode("utf-8"))


async def setup_connection(conn: asyncpg.Connection) -> None:
    await conn.set_type_codec(
        "jsonb",
        encoder=_encoder,
        decoder=_decoder,
        schema="pg_catalog",
        format="binary",
    )


async def create_connection(conf: Mapping[str, Any], loop: Any = None) -> asyncpg.Connection:
    """Create one asyncpg connection; loop is ignored for 0.9 compatibility."""
    return await asyncpg.connect(**dict(conf))


async def create_pool(conf: Mapping[str, Any], loop: Any = None) -> asyncpg.Pool:
    """Create an asyncpg pool; loop is ignored for 0.9 compatibility."""
    return await asyncpg.create_pool(**dict(conf), setup=setup_connection)
