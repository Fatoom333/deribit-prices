"""DeribitClient tests against a local fake exchange (no real network)."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from decimal import Decimal

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from app.clients.deribit import DeribitClient
from app.domain import PriceSourceError
from app.tickers import Ticker

Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]
ClientFactory = Callable[[Handler, float], Awaitable[DeribitClient]]


@pytest.fixture
async def make_client() -> AsyncIterator[ClientFactory]:
    servers: list[TestServer] = []
    sessions: list[aiohttp.ClientSession] = []

    async def factory(handler: Handler, timeout_seconds: float = 2.0) -> DeribitClient:
        app = web.Application()
        app.router.add_get("/api/v2/public/get_index_price", handler)
        server = TestServer(app)
        await server.start_server()
        servers.append(server)
        session = aiohttp.ClientSession()
        sessions.append(session)
        base_url = f"http://{server.host}:{server.port}/api/v2"
        return DeribitClient(session, base_url, timeout_seconds)

    yield factory

    for session in sessions:
        await session.close()
    for server in servers:
        await server.close()


def raw_json(body: str, status: int = 200) -> Handler:
    async def handler(request: web.Request) -> web.StreamResponse:
        return web.Response(text=body, status=status, content_type="application/json")

    return handler


async def test_returns_exact_decimal(make_client: ClientFactory) -> None:
    body = '{"jsonrpc":"2.0","result":{"estimated_delivery_price":82190.06,"index_price":82190.06}}'
    client = await make_client(raw_json(body))

    price = await client.get_index_price(Ticker.BTC_USD)

    assert price == Decimal("82190.06")
    assert isinstance(price, Decimal)


async def test_integer_price_is_accepted(make_client: ClientFactory) -> None:
    client = await make_client(raw_json('{"result":{"index_price":3000}}'))

    assert await client.get_index_price(Ticker.ETH_USD) == Decimal("3000")


async def test_sends_index_name_query_param(make_client: ClientFactory) -> None:
    seen: list[str | None] = []

    async def handler(request: web.Request) -> web.StreamResponse:
        seen.append(request.query.get("index_name"))
        return web.json_response({"result": {"index_price": 1}})

    client = await make_client(handler)
    await client.get_index_price(Ticker.BTC_USD)
    await client.get_index_price(Ticker.ETH_USD)

    assert seen == ["btc_usd", "eth_usd"]


async def test_jsonrpc_error_object_raises(make_client: ClientFactory) -> None:
    body = '{"error":{"code":-32602,"message":"Invalid params"}}'
    client = await make_client(raw_json(body))

    with pytest.raises(PriceSourceError, match="Invalid params"):
        await client.get_index_price(Ticker.BTC_USD)


async def test_jsonrpc_error_with_http_400_raises(make_client: ClientFactory) -> None:
    body = '{"error":{"code":-32602,"message":"Invalid params"}}'
    client = await make_client(raw_json(body, status=400))

    with pytest.raises(PriceSourceError, match="Invalid params"):
        await client.get_index_price(Ticker.BTC_USD)


async def test_http_500_raises(make_client: ClientFactory) -> None:
    client = await make_client(raw_json("oops", status=500))

    with pytest.raises(PriceSourceError):
        await client.get_index_price(Ticker.BTC_USD)


async def test_http_error_status_with_valid_body_raises(make_client: ClientFactory) -> None:
    client = await make_client(raw_json('{"result":{"index_price":1.5}}', status=503))

    with pytest.raises(PriceSourceError, match="503"):
        await client.get_index_price(Ticker.BTC_USD)


async def test_timeout_raises(make_client: ClientFactory) -> None:
    async def slow(request: web.Request) -> web.StreamResponse:
        await asyncio.sleep(1)
        return web.json_response({"result": {"index_price": 1}})

    client = await make_client(slow, 0.1)

    with pytest.raises(PriceSourceError):
        await client.get_index_price(Ticker.BTC_USD)


async def test_connection_refused_raises() -> None:
    async with aiohttp.ClientSession() as session:
        client = DeribitClient(session, "http://127.0.0.1:1/api/v2", 1.0)

        with pytest.raises(PriceSourceError):
            await client.get_index_price(Ticker.BTC_USD)


@pytest.mark.parametrize(
    "body",
    [
        "not json at all",
        "[]",
        "{}",
        '{"result":null}',
        '{"result":{}}',
        '{"result":{"index_price":"82190.06"}}',
        '{"result":{"index_price":null}}',
        '{"result":{"index_price":true}}',
        '{"result":{"index_price":0}}',
        '{"result":{"index_price":-5.5}}',
        '{"result":{"index_price":NaN}}',
    ],
)
async def test_malformed_body_raises(make_client: ClientFactory, body: str) -> None:
    client = await make_client(raw_json(body))

    with pytest.raises(PriceSourceError):
        await client.get_index_price(Ticker.BTC_USD)
