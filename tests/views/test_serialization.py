import asyncio
import json
from decimal import Decimal
from types import SimpleNamespace

from marshmallow import Schema, fields, post_dump

from aiohttp_boilerplate.views import fixed_dump
from aiohttp_boilerplate.views.create import CreateView
from aiohttp_boilerplate.views.list import ListView
from aiohttp_boilerplate.views.options import ObjectView


class NestedSchema(Schema):
    amount = fields.Decimal(as_string=True)


class ResponseSchema(Schema):
    amount = fields.Decimal(as_string=True)
    internal = fields.String(load_only=True)
    label = fields.String(data_key="display_label")
    nested = fields.Nested(NestedSchema)

    @post_dump
    def add_serialized_marker(self, data, **kwargs):
        data["serialized"] = True
        return data


class ResponseListView(ListView):
    def get_schema(self):
        return ResponseSchema


def make_view(view_class=ObjectView):
    view = object.__new__(view_class)
    view.schema = ResponseSchema
    view._request = SimpleNamespace(
        log=SimpleNamespace(debug=lambda *args, **kwargs: None),
    )
    return view


def make_object(amount="9007199254740993.123456789012345678"):
    data = {
        "amount": Decimal(amount),
        "internal": "do-not-return",
        "label": "Investment",
        "nested": {"amount": Decimal("0.000000000000000001")},
    }
    return SimpleNamespace(data=data, **data)


def test_object_view_dumps_model_data_through_schema():
    result = asyncio.run(ObjectView.get_data(make_view(), make_object()))

    assert result == {
        "amount": "9007199254740993.123456789012345678",
        "display_label": "Investment",
        "nested": {"amount": "0.000000000000000001"},
        "serialized": True,
    }


def test_list_view_dumps_each_model_through_schema():
    view = make_view(ResponseListView)

    result = asyncio.run(view.get_data([make_object("1.25"), make_object("2.5")]))

    assert [item["amount"] for item in result] == ["1.25", "2.5"]
    assert all(item["serialized"] is True for item in result)


def test_decimal_json_fallback_is_lossless_string():
    payload = json.loads(
        fixed_dump(
            {
                "large": Decimal("9007199254740993.123456789012345678"),
                "small": Decimal("1E-18"),
            }
        )
    )

    assert payload == {
        "large": "9007199254740993.123456789012345678",
        "small": "0.000000000000000001",
    }


def test_create_view_keeps_id_only_default_response():
    result = asyncio.run(CreateView.get_data(object(), SimpleNamespace(id=42)))

    assert result == {"id": 42}
