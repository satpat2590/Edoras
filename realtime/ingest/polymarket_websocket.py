#!/usr/bin/env python3
"""
Polymarket WebSocket client for real-time prediction market data.

Connects to the CLOB Market WebSocket (public, no auth) and ingests:
- price_change events → 5m candle buffers → 1h/4h rollups → binary indicator recompute
- book snapshots → periodic order book state (for liquidity tracking)
- market resolution events → mark securities inactive

Symbols are loaded dynamically from the securities table (exchange=polymarket).
New markets discovered by the REST provider are picked up on the next subscription refresh.

Usage:
    client = PolymarketWebSocketClient(db_path="crypto_data.db")
    await client.connect()
"""

import json
import logging
import sqlite3
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

from realtime.ingest.base_websocket import BaseWebSocketClient

logger = logging.getLogger("pm-ws")

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

# How often to check for newly discovered markets (seconds)
SUB_REFRESH_INTERVAL = 600  # 10 minutes


class PolymarketWebSocketClient(BaseWebSocketClient):
    """
    Real-time Polymarket price feed.

    Subscription model:
    - On startup, loads all active Polymarket securities from DB
    - Extracts clob_token_ids from metadata_json → subscribes to those asset IDs
    - Every SUB_REFRESH_INTERVAL, re-queries DB for new markets and subscribes
    - On market resolution event, marks security inactive in DB
    """

    def __init__(self, db_path: str):
        # Load symbols and token mappings from DB
        symbols, token_to_symbol, symbol_to_tokens = self._load_from_db(db_path)

        super().__init__(
            symbols=symbols,
            db_path=db_path,
            symbol_token_map=token_to_symbol,
        )

        # token_id → canonical symbol (for incoming WS messages)
        self._token_to_symbol: Dict[str, str] = token_to_symbol
        # canonical symbol → list of token_ids (for subscribing)
        self._symbol_to_tokens: Dict[str, List[str]] = symbol_to_tokens
        # All subscribed asset IDs (for tracking what's already subscribed)
        self._subscribed_tokens: set = set()
        # Custom heartbeat tracking
        self._last_heartbeat = time.monotonic()

    @staticmethod
    def _load_from_db(db_path: str):
        """Load active Polymarket securities and build token mappings."""
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT s.symbol, s.metadata_json "
            "FROM securities s JOIN exchanges e ON s.exchange_id = e.id "
            "WHERE e.code = 'polymarket' AND s.is_active = 1"
        ).fetchall()
        conn.close()

        symbols = []
        token_to_symbol = {}
        symbol_to_tokens = {}

        for row in rows:
            symbol = row["symbol"]
            try:
                meta = json.loads(row["metadata_json"]) if row["metadata_json"] else {}
            except (json.JSONDecodeError, TypeError):
                meta = {}

            clob_ids = meta.get("clob_token_ids", [])
            if not clob_ids:
                continue

            symbols.append(symbol)
            # Use YES token (index 0) as the primary price source
            yes_token = clob_ids[0]
            token_to_symbol[yes_token] = symbol
            symbol_to_tokens[symbol] = clob_ids

        logger.info(f"Loaded {len(symbols)} Polymarket securities, {len(token_to_symbol)} tokens")
        return symbols, token_to_symbol, symbol_to_tokens

    # ── Abstract interface implementation ─────────────────────────────────

    @property
    def ws_url(self) -> str:
        return WS_URL

    @property
    def exchange_code(self) -> str:
        return "polymarket"

    @property
    def heartbeat_interval(self) -> int:
        # Disable websockets library auto-ping — we handle PING/PONG ourselves
        # because Polymarket uses text-frame PING, not WebSocket protocol pings
        return 0

    @property
    def ping_timeout(self) -> int:
        return 15

    @property
    def sub_refresh_interval(self) -> int:
        return SUB_REFRESH_INTERVAL

    def _build_subscribe_message(self) -> dict:
        """Build CLOB Market subscription payload."""
        all_tokens = []
        for tokens in self._symbol_to_tokens.values():
            all_tokens.extend(tokens)

        self._subscribed_tokens = set(all_tokens)

        return {
            "type": "market",
            "assets_ids": all_tokens,
        }

    async def _parse_message(self, data: dict, ws) -> None:
        """Parse CLOB Market WS messages."""
        # Handle list-wrapped messages (Polymarket sends arrays)
        if isinstance(data, list):
            for item in data:
                await self._parse_message(item, ws)
            return

        event_type = data.get("event_type", "")

        if event_type == "price_change":
            self._handle_price_change(data)
        elif event_type == "book":
            pass  # order book snapshot — skip for now, can store later
        elif event_type == "last_trade_price":
            self._handle_last_trade(data)
        elif event_type == "market_resolved":
            await self._handle_resolution(data)
        elif event_type == "tick_size_change":
            pass  # informational
        elif event_type == "new_market":
            logger.info(f"[polymarket] New market detected via WS — will pick up on next refresh")

    # ── Heartbeat ─────────────────────────────────────────────────────────

    async def _on_connected(self, ws) -> None:
        """Start heartbeat task."""
        self._last_heartbeat = time.monotonic()
        self._heartbeat_task = asyncio.ensure_future(self._heartbeat_loop(ws))

    async def _heartbeat_loop(self, ws):
        """Send PING every 10s as required by Polymarket."""
        import asyncio
        try:
            while self.running:
                await asyncio.sleep(10)
                try:
                    await ws.send("PING")
                    self._last_heartbeat = time.monotonic()
                except Exception:
                    break
        except asyncio.CancelledError:
            pass

    async def _on_subscribed(self, ws) -> None:
        logger.info(
            f"[polymarket] Subscribed to {len(self._subscribed_tokens)} tokens "
            f"across {len(self.symbols)} markets"
        )

    # ── Event handlers ────────────────────────────────────────────────────

    def _handle_price_change(self, data: dict):
        """Process a price_change event into a candle tick."""
        asset_id = data.get("asset_id", "")
        symbol = self._token_to_symbol.get(asset_id)
        if not symbol:
            return

        try:
            price = float(data.get("price", 0))
            if price <= 0 or price > 1:
                return  # invalid for binary market

            # Polymarket doesn't provide per-tick volume in WS price_change
            ts = data.get("timestamp")
            if ts:
                # Polymarket timestamps can be ISO or epoch
                if isinstance(ts, str):
                    epoch = int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp())
                else:
                    epoch = int(ts)
            else:
                epoch = int(time.time())

            self._on_tick(symbol, price, volume=0.0, epoch_ts=epoch)

        except (ValueError, TypeError) as e:
            logger.debug(f"[polymarket] Bad price_change: {e}")

    def _handle_last_trade(self, data: dict):
        """Process last_trade_price as an additional price tick."""
        asset_id = data.get("asset_id", "")
        symbol = self._token_to_symbol.get(asset_id)
        if not symbol:
            return

        try:
            price = float(data.get("price", 0))
            if 0 < price <= 1:
                self._on_tick(symbol, price, volume=0.0)
        except (ValueError, TypeError):
            pass

    async def _handle_resolution(self, data: dict):
        """Mark a resolved market as inactive in the securities table."""
        asset_id = data.get("asset_id", "")
        symbol = self._token_to_symbol.get(asset_id)
        if not symbol:
            return

        outcome = data.get("outcome", "unknown")
        logger.info(f"[polymarket] Market resolved: {symbol} → {outcome}")

        try:
            if self.db:
                await self.db.execute(
                    "UPDATE securities SET is_active = 0, "
                    "metadata_json = json_set(metadata_json, '$.resolution', ?) "
                    "WHERE symbol = ?",
                    (str(outcome), symbol),
                )
                await self.db.commit()

            # Remove from tracking
            self._token_to_symbol.pop(asset_id, None)
            self._symbol_to_tokens.pop(symbol, None)
            self._subscribed_tokens.discard(asset_id)
            if symbol in self.symbols:
                self.symbols.remove(symbol)

        except Exception as e:
            logger.error(f"[polymarket] Failed to handle resolution for {symbol}: {e}")

    # ── Dynamic subscription management ───────────────────────────────────

    async def _manage_subscriptions(self, ws) -> None:
        """Check for newly discovered markets and subscribe to them."""
        try:
            new_symbols, new_token_map, new_sym_tokens = self._load_from_db(self.db_path)

            # Find tokens we haven't subscribed to yet
            new_tokens = []
            for sym in new_symbols:
                if sym not in self._symbol_to_tokens:
                    tokens = new_sym_tokens.get(sym, [])
                    new_tokens.extend(tokens)
                    self._symbol_to_tokens[sym] = tokens
                    for t in tokens:
                        self._token_to_symbol[t] = sym
                    self.symbols.append(sym)

            if new_tokens:
                # Send incremental subscription
                sub_msg = {
                    "type": "market",
                    "assets_ids": new_tokens,
                }
                await ws.send(json.dumps(sub_msg))
                self._subscribed_tokens.update(new_tokens)
                logger.info(
                    f"[polymarket] Subscribed to {len(new_tokens)} new tokens "
                    f"({len(self.symbols)} total markets)"
                )

        except Exception as e:
            logger.error(f"[polymarket] Subscription refresh failed: {e}")

    # ── Graceful shutdown override ────────────────────────────────────────

    async def disconnect(self):
        """Cancel heartbeat task and flush."""
        if hasattr(self, "_heartbeat_task") and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        await super().disconnect()


# Need asyncio import at module level for the heartbeat
import asyncio


async def main():
    """Run standalone for testing."""
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from config import DB_PATH

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )

    client = PolymarketWebSocketClient(db_path=DB_PATH)
    if not client.symbols:
        print("No active Polymarket securities found. Run providers/polymarket.py first.")
        return

    print(f"Connecting to Polymarket WS with {len(client.symbols)} markets...")
    try:
        await client.connect()
    except KeyboardInterrupt:
        pass
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
