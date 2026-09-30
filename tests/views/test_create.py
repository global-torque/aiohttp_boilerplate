from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from aiohttp_boilerplate.views.create import CreateView


class TransactionProbe:
    async def __aenter__(self) -> TransactionProbe:
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass


class SQLProbe:
    def transaction(self) -> TransactionProbe:
        return TransactionProbe()


class ReplayCreateView(CreateView):
    async def on_start(self) -> None:
        pass

    async def get_request_data(self, to_json: bool = False) -> dict[str, str]:
        return {"name": "requested"}

    async def validate(self, data: dict[str, Any]) -> dict[str, Any]:
        return data

    async def perform_create(self, data: dict[str, Any]) -> Any:
        if self.should_replay:
            self.replayed = True
        return SimpleNamespace(id=7)

    async def after_create_in_transaction(self, data: dict[str, Any]) -> dict[str, Any]:
        self.hooks.append("transaction")
        return {}

    async def after_create(self, data: dict[str, Any]) -> dict[str, Any]:
        self.hooks.append("after")
        return {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("replayed", "status", "hooks"),
    [(False, 201, ["transaction", "after"]), (True, 200, [])],
)
async def test_create_replay_status_and_side_effect_hooks(
    replayed: bool, status: int, hooks: list[str]
) -> None:
    view = object.__new__(ReplayCreateView)
    view._request = SimpleNamespace()
    view.schema = None
    view.data = {}
    view.obj = SimpleNamespace(sql=SQLProbe())
    view.replayed = False
    view.should_replay = replayed
    view.hooks = []

    response = await view._post()

    assert response.status == status
    assert json.loads(response.text) == {"id": 7}
    assert view.hooks == hooks
