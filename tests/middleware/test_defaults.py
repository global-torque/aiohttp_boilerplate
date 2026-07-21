import asyncio
import json
from types import SimpleNamespace

import pytest
from aiohttp import web

from aiohttp_boilerplate.middleware.defaults import cross_origin_rules


ORIGIN = 'https://app.example.com'


def make_request():
    return SimpleNamespace(
        app=SimpleNamespace(conf={'domain': 'example.com'}),
        headers={'origin': ORIGIN},
    )


def run_middleware(handler):
    return asyncio.run(cross_origin_rules(make_request(), handler))


def assert_cors_headers(headers):
    assert headers['Access-Control-Allow-Origin'] == ORIGIN
    assert headers['Access-Control-Allow-Credentials'] == 'true'
    assert 'GET' in headers['Access-Control-Allow-Methods']
    assert 'Content-Type' in headers['Access-Control-Allow-Headers']


def test_adds_cors_headers_to_success_response():
    async def handler(request):
        return web.json_response({'data': 'ok'})

    response = run_middleware(handler)

    assert response.status == 200
    assert json.loads(response.text) == {'data': 'ok'}
    assert_cors_headers(response.headers)


def test_preserves_http_exception_and_adds_cors_headers():
    async def handler(request):
        raise web.HTTPForbidden(text='forbidden')

    with pytest.raises(web.HTTPForbidden) as raised:
        run_middleware(handler)

    assert raised.value.status == 403
    assert raised.value.text == 'forbidden'
    assert_cors_headers(raised.value.headers)


def test_returns_sanitized_cors_json_for_unhandled_exception(caplog):
    async def handler(request):
        raise RuntimeError('sensitive database details')

    with caplog.at_level('ERROR'):
        response = run_middleware(handler)

    assert response.status == 500
    assert response.content_type == 'application/json'
    assert json.loads(response.text) == {
        '__error__': ['HTTP Internal Server Error'],
    }
    assert 'sensitive database details' not in response.text
    assert 'Unhandled exception while processing request' in caplog.text
    assert_cors_headers(response.headers)
