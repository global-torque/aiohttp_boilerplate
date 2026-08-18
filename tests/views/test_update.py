from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from aiohttp_boilerplate.views.exceptions import JSONHTTPError
from aiohttp_boilerplate.views.update import UpdateView


class NoOpUpdateView(UpdateView):
    async def on_start(self) -> None:
        pass

    async def get_request_data(self, to_json: bool = False) -> dict[str, str]:
        return {"name": "unchanged"}

    async def validate(self, data: dict[str, Any]) -> dict[str, Any]:
        return data

    async def before_update(self, data: dict[str, Any]) -> dict[str, Any]:
        self.obj = SimpleNamespace(data={"id": 7, "name": data["name"]})
        return {}

    async def perform_update(
        self,
        where: str,
        params: dict[str, Any],
        data: dict[str, Any],
    ) -> int:
        raise AssertionError("perform_update must not run for a no-op")

    async def after_update_in_transaction(self, data: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("transaction hooks must not run for a no-op")

    async def after_update(self, data: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("after_update must not run for a no-op")

    async def get_data(self, obj: Any) -> dict[str, Any]:
        return dict(obj.data)


@pytest.mark.asyncio
async def test_empty_prepared_update_is_successful_no_op() -> None:
    view = object.__new__(NoOpUpdateView)
    view._request = SimpleNamespace()
    view.schema = None
    view.data = {}
    view.where = ""
    view.params = {}

    response = await view._patch()

    assert response.status == 200
    assert json.loads(response.text) == {"id": 7, "name": "unchanged"}


class TransactionProbe:
    def __init__(self) -> None:
        self.entered = False
        self.exited = False

    async def __aenter__(self) -> TransactionProbe:
        self.entered = True
        return self

    async def __aexit__(self, *args: Any) -> None:
        self.exited = True


class SQLProbe:
    def __init__(self) -> None:
        self.transaction_probe = TransactionProbe()

    def transaction(self) -> TransactionProbe:
        return self.transaction_probe


class NormalUpdateView(NoOpUpdateView):
    updated_rows = 1

    async def before_update(self, data: dict[str, Any]) -> dict[str, Any]:
        return data

    async def perform_update(
        self,
        where: str,
        params: dict[str, Any],
        data: dict[str, Any],
    ) -> int:
        self.obj.data.update(data)
        return self.updated_rows

    async def after_update_in_transaction(self, data: dict[str, Any]) -> dict[str, Any]:
        return {"transaction_hook": True}

    async def after_update(self, data: dict[str, Any]) -> dict[str, Any]:
        return {"after_hook": True}


def normal_update_view(updated_rows: int = 1) -> NormalUpdateView:
    view = object.__new__(NormalUpdateView)
    view._request = SimpleNamespace()
    view.schema = None
    view.data = {}
    view.where = "id={id}"
    view.params = {"id": 7}
    view.obj = SimpleNamespace(data={"id": 7}, sql=SQLProbe())
    view.updated_rows = updated_rows
    return view


@pytest.mark.asyncio
async def test_non_empty_prepared_update_keeps_transaction_and_hooks() -> None:
    view = normal_update_view()

    response = await view._patch()

    assert response.status == 200
    assert json.loads(response.text) == {"id": 7, "name": "unchanged"}
    assert view.data == {
        "name": "unchanged",
        "transaction_hook": True,
        "after_hook": True,
    }
    assert view.obj.sql.transaction_probe.entered is True
    assert view.obj.sql.transaction_probe.exited is True


@pytest.mark.asyncio
async def test_zero_row_update_still_returns_not_found() -> None:
    view = normal_update_view(updated_rows=0)

    with pytest.raises(JSONHTTPError) as error:
        await view._patch()

    assert error.value.status_code == 404
    assert view.obj.sql.transaction_probe.entered is True
    assert view.obj.sql.transaction_probe.exited is True
    assert "transaction_hook" not in view.data
    assert "after_hook" not in view.data
