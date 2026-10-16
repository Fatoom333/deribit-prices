"""Deribit public API client."""

import asyncio
import json
from decimal import Decimal
from typing import Any

import aiohttp

from app.domain import PriceSourceError
from app.tickers import Ticker


class DeribitClient:
    """Fetches index prices from the Deribit public JSON-RPC-over-HTTP API.

    The aiohttp session is injected and owned by the caller, so the client never
    opens or closes network resources by itself.
    """

    def __init__(
        self, session: aiohttp.ClientSession, base_url: str, timeout_seconds: float
    ) -> None:
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)

    async def get_index_price(self, ticker: Ticker) -> Decimal:
        """Return the current index price of `ticker` as an exact Decimal."""
        url = f"{self._base_url}/public/get_index_price"
        try:
            async with self._session.get(
                url, params={"index_name": ticker.value}, timeout=self._timeout
            ) as response:
                body = await response.text()
                status = response.status
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise PriceSourceError(f"Deribit request for {ticker} failed: {exc!r}") from exc

        payload = self._parse_body(body, ticker)
        error = payload.get("error")
        if error is not None:
            raise PriceSourceError(f"Deribit returned an error for {ticker}: {error}")
        if status != 200:
            raise PriceSourceError(f"Deribit returned HTTP {status} for {ticker}")
        return self._extract_price(payload, ticker)

    @staticmethod
    def _parse_body(body: str, ticker: Ticker) -> dict[str, Any]:
        # parse_float=Decimal keeps the price exact: it never passes through float.
        try:
            payload = json.loads(body, parse_float=Decimal)
        except ValueError as exc:
            raise PriceSourceError(f"Deribit sent invalid JSON for {ticker}") from exc
        if not isinstance(payload, dict):
            raise PriceSourceError(f"Unexpected Deribit response shape for {ticker}")
        return payload

    @staticmethod
    def _extract_price(payload: dict[str, Any], ticker: Ticker) -> Decimal:
        result = payload.get("result")
        price = result.get("index_price") if isinstance(result, dict) else None
        # bool is a subclass of int, so it has to be excluded explicitly.
        if isinstance(price, bool) or not isinstance(price, (Decimal, int)):
            raise PriceSourceError(f"Deribit response for {ticker} has no numeric index_price")
        value = Decimal(price)
        if not value.is_finite() or value <= 0:
            raise PriceSourceError(f"Deribit returned an invalid price for {ticker}: {value}")
        return value
