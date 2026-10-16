from enum import StrEnum


class Ticker(StrEnum):
    """Index names supported by the collector and the API.

    Values match Deribit index names, so they are passed to the exchange as-is.
    """

    BTC_USD = "btc_usd"
    ETH_USD = "eth_usd"
