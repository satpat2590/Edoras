#!/usr/bin/env python3
"""
Exchange-agnostic WebSocket base class for real-time market data ingestion.

Provides the common pipeline:
    ticks → 5m candle buffer → flush to DB → 1h rollup → 4h rollup → indicator recompute

Subclasses implement exchange-specific logic:
    - WS URL and connection params
    - Subscribe/unsubscribe message format
    - Tick parsing (extract symbol, price, volume, timestamp)
    - Heartbeat protocol
    - Any exchange-specific event handling (e.g., market resolution)

Designed for future exchanges: Coinbase, Polymarket, Kalshi, Binance, Kraken, etc.
"""

import abc
import asyncio
import json
import logging
import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Tuple

import aiosqlite

logger = logging.getLogger(__name__)

# ── Timing constants ──────────────────────────────────────────────────────
CANDLE_5M = 300
CANDLE_1H = 3600
CANDLE_4H = 4 * 3600
FLUSH_INTERVAL = 60       # flush candle buffers to DB every 60s
STATS_INTERVAL = 300      # log stats every 5 min
RECONNECT_BASE = 1
RECONNECT_CAP = 60


@dataclass
class CandleBuffer:
    """In-memory OHLCV candle being built from ticks."""
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    volume: float = 0.0
    tick_count: int = 0

    def update(self, price: float, volume: float = 0.0):
        if self.tick_count == 0:
            self.open = self.high = self.low = self.close = price
        else:
            self.high = max(self.high, price)
            self.low = min(self.low, price)
            self.close = price
        self.volume += volume
        self.tick_count += 1

    def as_tuple(self):
        return (self.open, self.high, self.low, self.close, self.volume)

    def reset(self):
        self.open = self.high = self.low = self.close = self.volume = 0.0
        self.tick_count = 0


class BaseWebSocketClient(abc.ABC):
    """
    Abstract base for exchange WebSocket clients.

    Subclasses MUST implement:
        ws_url            — property returning the WebSocket URL
        exchange_code     — property returning exchange code (e.g. 'coinbase', 'polymarket')
        _build_subscribe_message(symbols) — returns JSON-serializable subscribe payload
        _parse_message(data) — parse raw WS message, call self._on_tick() for price updates
        _heartbeat_interval — property returning ping interval in seconds

    Subclasses MAY override:
        _on_connected(ws)        — called after WS connects (before subscribe)
        _on_subscribed(ws)       — called after subscribe message sent
        _on_event(data)          — handle non-tick events (e.g. market resolution)
        _get_indicator_profile(symbol) — return indicator profile for a symbol
        _manage_subscriptions()  — periodic subscription management (new/expired markets)
    """

    def __init__(self, symbols: List[str], db_path: str, symbol_token_map: Dict[str, str] = None):
        """
        Args:
            symbols: list of canonical symbol strings (e.g. 'BTC-USD', 'PM:FED-NO-CHANGE')
            db_path: path to SQLite database
            symbol_token_map: optional mapping of internal token/product IDs to canonical symbols
                              (e.g. {'0x1234...': 'PM:FED-NO-CHANGE'} for Polymarket)
        """
        self.symbols = list(symbols)
        self.db_path = db_path
        self.symbol_token_map = symbol_token_map or {}

        self.running = False
        self.reconnect_delay = RECONNECT_BASE
        self.db: Optional[aiosqlite.Connection] = None

        # In-memory candle buffers: {(symbol, interval_ts): CandleBuffer}
        self._candles_5m: Dict[tuple, CandleBuffer] = defaultdict(CandleBuffer)

        # Pending rollups
        self._pending_1h_rollups: Set[Tuple[str, int]] = set()
        self._pending_4h_rollups: Set[Tuple[str, int]] = set()

        # Metrics
        self._tick_count = 0
        self._candles_flushed = 0
        self._last_stats = time.monotonic()
        self._last_flush = time.monotonic()
        self._last_sub_refresh = time.monotonic()
        self._last_tick_time: Dict[str, float] = {}

    # ── Abstract interface ────────────────────────────────────────────────

    @property
    @abc.abstractmethod
    def ws_url(self) -> str:
        """WebSocket endpoint URL."""

    @property
    @abc.abstractmethod
    def exchange_code(self) -> str:
        """Exchange identifier (matches exchanges.code in DB)."""

    @property
    def heartbeat_interval(self) -> int:
        """Ping interval in seconds. Override for stricter exchanges."""
        return 30

    @property
    def ping_timeout(self) -> int:
        """Ping timeout in seconds."""
        return 10

    @property
    def sub_refresh_interval(self) -> int:
        """How often to check for new symbols to subscribe (seconds). 0 = never."""
        return 0

    @abc.abstractmethod
    def _build_subscribe_message(self) -> dict:
        """Build the subscription payload for the current symbol list."""

    @abc.abstractmethod
    async def _parse_message(self, data: dict, ws) -> None:
        """
        Parse a decoded JSON message from the WebSocket.
        For price updates, call self._on_tick(symbol, price, volume, epoch_ts).
        For other events, handle as needed (e.g. resolution, errors).
        """

    # ── Optional hooks ────────────────────────────────────────────────────

    async def _on_connected(self, ws) -> None:
        """Called right after WebSocket connection established, before subscribe."""
        pass

    async def _on_subscribed(self, ws) -> None:
        """Called after subscribe message sent."""
        pass

    async def _send_heartbeat(self, ws) -> None:
        """Override for exchanges that need custom heartbeat (e.g. PING text frames)."""
        pass

    def _get_indicator_profile(self, symbol: str) -> str:
        """Return indicator profile for a symbol. Override to query DB."""
        return "standard"

    async def _manage_subscriptions(self, ws) -> None:
        """Override to dynamically add/remove subscriptions (e.g. new Polymarket markets)."""
        pass

    # ── Tick ingestion ────────────────────────────────────────────────────

    def _on_tick(self, symbol: str, price: float, volume: float = 0.0, epoch_ts: int = None):
        """Process a single price tick into the 5m candle buffer."""
        if epoch_ts is None:
            epoch_ts = int(time.time())

        interval_ts = (epoch_ts // CANDLE_5M) * CANDLE_5M
        key = (symbol, interval_ts)
        self._candles_5m[key].update(price, volume)
        self._tick_count += 1
        self._last_tick_time[symbol] = time.monotonic()

    # ── Database ──────────────────────────────────────────────────────────

    async def _connect_db(self):
        self.db = await aiosqlite.connect(self.db_path)
        await self.db.execute("PRAGMA journal_mode=WAL")
        await self.db.execute("PRAGMA synchronous=NORMAL")
        logger.info(f"[{self.exchange_code}] DB connected: {self.db_path}")

    async def _flush_candles(self):
        """Write completed 5m candles from buffer to DB."""
        if not self.db or not self._candles_5m:
            return

        now_ts = int(time.time())
        current_interval = (now_ts // CANDLE_5M) * CANDLE_5M
        flushed = 0

        keys_to_flush = [
            k for k in list(self._candles_5m.keys())
            if k[1] < current_interval  # only flush closed intervals
        ]

        for key in keys_to_flush:
            symbol, interval_ts = key
            buf = self._candles_5m.pop(key)
            if buf.tick_count == 0:
                continue

            try:
                await self.db.execute(
                    "INSERT OR REPLACE INTO candlesticks "
                    "(symbol, timeframe, timestamp, open, high, low, close, volume) "
                    "VALUES (?, '5m', ?, ?, ?, ?, ?, ?)",
                    (symbol, interval_ts, *buf.as_tuple()),
                )
                flushed += 1

                # Mark parent intervals for rollup
                hour_ts = (interval_ts // CANDLE_1H) * CANDLE_1H
                self._pending_1h_rollups.add((symbol, hour_ts))
            except Exception as e:
                logger.error(f"[{self.exchange_code}] Flush failed {symbol}@{interval_ts}: {e}")

        if flushed:
            await self.db.commit()
            self._candles_flushed += flushed

        self._last_flush = time.monotonic()

    async def _rollup_1h_candles(self):
        """Roll up closed 5m candles into 1h candles."""
        if not self.db or not self._pending_1h_rollups:
            return

        now_ts = int(time.time())
        current_hour = (now_ts // CANDLE_1H) * CANDLE_1H

        ready = [(s, h) for s, h in self._pending_1h_rollups if h < current_hour]
        if not ready:
            return

        symbols_updated = set()
        for symbol, hour_ts in ready:
            self._pending_1h_rollups.discard((symbol, hour_ts))
            try:
                async with self.db.execute(
                    "SELECT open, high, low, close, volume, timestamp FROM candlesticks "
                    "WHERE symbol = ? AND timeframe = '5m' "
                    "AND timestamp >= ? AND timestamp < ? ORDER BY timestamp",
                    (symbol, hour_ts, hour_ts + CANDLE_1H),
                ) as cursor:
                    rows = await cursor.fetchall()

                if not rows:
                    continue

                h_open = rows[0][0]
                h_high = max(r[1] for r in rows)
                h_low = min(r[2] for r in rows)
                h_close = rows[-1][3]
                h_volume = sum(r[4] for r in rows)

                await self.db.execute(
                    "INSERT OR REPLACE INTO candlesticks "
                    "(symbol, timeframe, timestamp, open, high, low, close, volume) "
                    "VALUES (?, '1h', ?, ?, ?, ?, ?, ?)",
                    (symbol, hour_ts, h_open, h_high, h_low, h_close, h_volume),
                )
                symbols_updated.add(symbol)
                logger.info(
                    f"[{self.exchange_code}] 1h candle: {symbol} @ "
                    f"{datetime.fromtimestamp(hour_ts, tz=timezone.utc).strftime('%H:%M')} "
                    f"O={h_open:.4f} H={h_high:.4f} L={h_low:.4f} C={h_close:.4f}"
                )

                # Mark parent 4h block
                block_4h_ts = (hour_ts // CANDLE_4H) * CANDLE_4H
                self._pending_4h_rollups.add((symbol, block_4h_ts))
            except Exception as e:
                logger.error(f"[{self.exchange_code}] 1h rollup failed {symbol}@{hour_ts}: {e}")

        if symbols_updated:
            await self.db.commit()
            await self._rollup_4h_candles()
            # Trigger indicator recomputation in background thread
            asyncio.get_event_loop().run_in_executor(
                None, self._recompute_indicators, list(symbols_updated), ("1h",)
            )

    async def _rollup_4h_candles(self):
        """Roll up closed 1h candles into 4h candles."""
        if not self.db or not self._pending_4h_rollups:
            return

        now_ts = int(time.time())
        current_4h = (now_ts // CANDLE_4H) * CANDLE_4H

        ready = [(s, b) for s, b in self._pending_4h_rollups if b < current_4h]
        if not ready:
            return

        symbols_updated_4h = set()
        for symbol, block_ts in ready:
            self._pending_4h_rollups.discard((symbol, block_ts))
            try:
                async with self.db.execute(
                    "SELECT open, high, low, close, volume, timestamp FROM candlesticks "
                    "WHERE symbol = ? AND timeframe = '1h' "
                    "AND timestamp >= ? AND timestamp < ? ORDER BY timestamp",
                    (symbol, block_ts, block_ts + CANDLE_4H),
                ) as cursor:
                    rows = await cursor.fetchall()

                if not rows:
                    continue

                b_open = rows[0][0]
                b_high = max(r[1] for r in rows)
                b_low = min(r[2] for r in rows)
                b_close = rows[-1][3]
                b_volume = sum(r[4] for r in rows)

                await self.db.execute(
                    "INSERT OR REPLACE INTO candlesticks "
                    "(symbol, timeframe, timestamp, open, high, low, close, volume) "
                    "VALUES (?, '4h', ?, ?, ?, ?, ?, ?)",
                    (symbol, block_ts, b_open, b_high, b_low, b_close, b_volume),
                )
                symbols_updated_4h.add(symbol)
                logger.info(
                    f"[{self.exchange_code}] 4h candle: {symbol} @ "
                    f"{datetime.fromtimestamp(block_ts, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')} "
                    f"({len(rows)} 1h candles)"
                )
            except Exception as e:
                logger.error(f"[{self.exchange_code}] 4h rollup failed {symbol}@{block_ts}: {e}")

        if symbols_updated_4h:
            await self.db.commit()
            asyncio.get_event_loop().run_in_executor(
                None, self._recompute_indicators, list(symbols_updated_4h), ("4h",)
            )

    # ── Indicator recomputation (runs in thread pool) ─────────────────────

    def _recompute_indicators(self, symbols: List[str], timeframes: tuple = ("1h",)):
        """Recompute indicators for given symbols, respecting indicator_profile."""
        import sys
        import os
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        try:
            from indicator_calculator import (
                calculate_all_indicators, INDICATOR_COLUMNS,
                calculate_binary_indicators, BINARY_INDICATOR_COLUMNS,
            )
            import pandas as pd

            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row

            for symbol in symbols:
                # Look up indicator profile from securities table
                profile = "standard"
                row = conn.execute(
                    "SELECT indicator_profile FROM securities WHERE symbol=? AND is_active=1 LIMIT 1",
                    (symbol,),
                ).fetchone()
                if row and row["indicator_profile"]:
                    profile = row["indicator_profile"]

                if profile == "none":
                    continue

                for tf in timeframes:
                    df = pd.read_sql_query(
                        "SELECT timestamp, open, high, low, close, volume "
                        "FROM candlesticks WHERE symbol=? AND timeframe=? ORDER BY timestamp",
                        conn, params=(symbol, tf),
                    )
                    if len(df) < 20:
                        continue

                    if profile == "binary":
                        # Binary prediction market indicators
                        df = calculate_binary_indicators(df)
                        columns = BINARY_INDICATOR_COLUMNS
                    else:
                        # Standard crypto/equity indicators
                        if len(df) < 50:
                            continue
                        df = calculate_all_indicators(df)
                        columns = INDICATOR_COLUMNS

                    cur = conn.cursor()
                    first_col = columns[0]
                    for _, irow in df.iterrows():
                        if pd.isna(irow.get(first_col)):
                            continue
                        vals = [
                            float(irow[col]) if not pd.isna(irow.get(col)) else None
                            for col in columns
                        ]
                        cur.execute(
                            "INSERT OR REPLACE INTO indicators "
                            "(symbol, timeframe, timestamp, "
                            + ", ".join(columns)
                            + ") VALUES (?,?,?," + ",".join(["?"] * len(columns)) + ")",
                            (symbol, tf, int(irow["timestamp"]), *vals),
                        )
                    conn.commit()
                    logger.info(f"[{self.exchange_code}] Indicators recomputed: {symbol}/{tf} (profile={profile})")
            conn.close()
        except Exception as e:
            logger.error(f"[{self.exchange_code}] Indicator recompute failed: {e}", exc_info=True)

    # ── Connection lifecycle ──────────────────────────────────────────────

    async def connect(self):
        """Main connection loop with automatic reconnection."""
        import websockets

        self.running = True
        await self._connect_db()

        while self.running:
            try:
                logger.info(
                    f"[{self.exchange_code}] Connecting to {self.ws_url} "
                    f"({len(self.symbols)} symbols)..."
                )
                async with websockets.connect(
                    self.ws_url,
                    ping_interval=self.heartbeat_interval if self.heartbeat_interval > 0 else None,
                    ping_timeout=self.ping_timeout,
                    close_timeout=10,
                ) as ws:
                    self.reconnect_delay = RECONNECT_BASE
                    await self._on_connected(ws)
                    await ws.send(json.dumps(self._build_subscribe_message()))
                    await self._on_subscribed(ws)
                    await self._message_loop(ws)

            except Exception as e:
                level = logging.WARNING if "Connection" in type(e).__name__ else logging.ERROR
                logger.log(level, f"[{self.exchange_code}] Disconnected: {e}")
                if self.running:
                    await self._reconnect_wait()

    async def _message_loop(self, ws):
        """Main message processing loop."""
        async for raw in ws:
            try:
                # Some exchanges send text pings
                if isinstance(raw, str) and raw.strip().upper() == "PING":
                    await ws.send("PONG")
                    continue

                data = json.loads(raw) if isinstance(raw, str) else json.loads(raw.decode())
                await self._parse_message(data, ws)

                # Periodic maintenance
                now = time.monotonic()
                if now - self._last_flush >= FLUSH_INTERVAL:
                    await self._flush_candles()
                    await self._rollup_1h_candles()
                if now - self._last_stats >= STATS_INTERVAL:
                    self._log_stats()
                # Dynamic subscription refresh
                if self.sub_refresh_interval > 0 and now - self._last_sub_refresh >= self.sub_refresh_interval:
                    await self._manage_subscriptions(ws)
                    self._last_sub_refresh = now

            except json.JSONDecodeError:
                pass
            except Exception as e:
                logger.error(f"[{self.exchange_code}] Message error: {e}")

    async def _reconnect_wait(self):
        logger.info(f"[{self.exchange_code}] Reconnecting in {self.reconnect_delay}s...")
        await asyncio.sleep(self.reconnect_delay)
        self.reconnect_delay = min(self.reconnect_delay * 2, RECONNECT_CAP)

    def _log_stats(self):
        now = time.monotonic()
        elapsed = now - self._last_stats
        active = sum(1 for t in self._last_tick_time.values() if now - t < 120)
        logger.info(
            f"[{self.exchange_code}] Stats: {self._tick_count} ticks | "
            f"{self._candles_flushed} candles flushed | "
            f"{len(self._candles_5m)} buffers open | "
            f"{active}/{len(self.symbols)} symbols active | "
            f"{self._tick_count / max(elapsed, 1):.1f} ticks/s"
        )
        self._tick_count = 0
        self._candles_flushed = 0
        self._last_stats = now

    async def disconnect(self):
        """Graceful shutdown — flush remaining candles."""
        logger.info(f"[{self.exchange_code}] Shutting down...")
        self.running = False
        if self.db:
            # Force-flush even current interval
            for key in list(self._candles_5m.keys()):
                symbol, interval_ts = key
                buf = self._candles_5m.pop(key)
                if buf.tick_count == 0:
                    continue
                try:
                    await self.db.execute(
                        "INSERT OR REPLACE INTO candlesticks "
                        "(symbol, timeframe, timestamp, open, high, low, close, volume) "
                        "VALUES (?, '5m', ?, ?, ?, ?, ?, ?)",
                        (symbol, interval_ts, *buf.as_tuple()),
                    )
                except Exception:
                    pass
            await self.db.commit()
            await self.db.close()
        logger.info(f"[{self.exchange_code}] Stopped.")
