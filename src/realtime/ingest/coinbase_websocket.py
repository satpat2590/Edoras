#!/usr/bin/env python3
"""
Coinbase WebSocket client for real-time market data.

Connects to the public Coinbase WebSocket feed, aggregates ticks into
5-minute candles, rolls up to 1-hour and 4-hour candles, and triggers
indicator recomputation when new candles close.

No API key required — uses the public ticker feed.

Now inherits from BaseWebSocketClient for shared candle/rollup/indicator logic.
"""

import json
import logging
import time
from datetime import datetime, timezone
from typing import List

from realtime.ingest.base_websocket import BaseWebSocketClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)
logger = logging.getLogger("ws-feed")

WS_URL = "wss://ws-feed.exchange.coinbase.com"


class CoinbaseWebSocketClient(BaseWebSocketClient):
    """
    Production Coinbase WebSocket client:
    - Subscribes to ticker channel for all symbols
    - Aggregates ticks into 5m candles (in-memory, flushed periodically)
    - Rolls up 5m → 1h → 4h candles
    - Triggers indicator recomputation for symbols with new data
    """

    def __init__(self, symbols: List[str], db_path: str, heartbeat_interval: int = 30):
        super().__init__(symbols=symbols, db_path=db_path)
        self._custom_heartbeat = heartbeat_interval

    # ── Abstract interface implementation ─────────────────────────────────

    @property
    def ws_url(self) -> str:
        return WS_URL

    @property
    def exchange_code(self) -> str:
        return "coinbase"

    @property
    def heartbeat_interval(self) -> int:
        return self._custom_heartbeat

    @property
    def ping_timeout(self) -> int:
        return 10

    def _build_subscribe_message(self) -> dict:
        return {
            "type": "subscribe",
            "product_ids": self.symbols,
            "channels": [
                {"name": "ticker", "product_ids": self.symbols},
            ],
        }

    async def _parse_message(self, data: dict, ws) -> None:
        msg_type = data.get("type")

        if msg_type == "ticker":
            self._process_coinbase_tick(data)
        elif msg_type == "error":
            logger.error(f"[coinbase] WS error: {data}")
        elif msg_type == "subscriptions":
            channels = data.get("channels", [])
            logger.info(f"[coinbase] Subscription confirmed ({len(channels)} channels)")

    # ── Coinbase-specific tick processing ─────────────────────────────────

    def _process_coinbase_tick(self, data: dict):
        """Parse a Coinbase ticker message into a candle tick."""
        try:
            symbol = data["product_id"]
            price = float(data["price"])
            last_size = float(data.get("last_size", 0))

            time_str = data.get("time", "")
            if time_str:
                ts = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
                epoch = int(ts.timestamp())
            else:
                epoch = int(time.time())

            self._on_tick(symbol, price, volume=last_size, epoch_ts=epoch)

        except (KeyError, ValueError) as e:
            logger.debug(f"[coinbase] Bad tick: {e}")


async def main():
    """Run standalone for testing."""
    import asyncio
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from config import PORTFOLIO_SYMBOLS, TOP_CRYPTO_SYMBOLS, DB_PATH

    symbols = list(dict.fromkeys(PORTFOLIO_SYMBOLS + TOP_CRYPTO_SYMBOLS))

    client = CoinbaseWebSocketClient(symbols=symbols, db_path=DB_PATH)
    try:
        await client.connect()
    except KeyboardInterrupt:
        pass
    finally:
        await client.disconnect()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
