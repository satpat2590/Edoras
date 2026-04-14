#!/usr/bin/env python3
"""
Real-time trading supervisor.

Runs multiple WebSocket clients concurrently — one per exchange.
Currently manages: Coinbase (crypto) and Polymarket (prediction markets).
Designed for future exchanges: Kalshi, Binance, Kraken, etc.

Each feed runs as an independent asyncio task. The supervisor handles:
- Lifecycle management (start, stop, restart on failure)
- Graceful shutdown on SIGTERM/SIGINT
- Per-feed error isolation (one feed crashing doesn't take down the others)

Designed to run as a persistent systemd service.
"""

import asyncio
import logging
import signal
import sqlite3
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from realtime.ingest.base_websocket import BaseWebSocketClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)
logger = logging.getLogger("supervisor")


def _is_exchange_active(exchange_code: str) -> bool:
    """Check if an exchange has active securities in the DB."""
    try:
        conn = sqlite3.connect(config.DB_PATH)
        row = conn.execute(
            "SELECT COUNT(*) FROM securities s "
            "JOIN exchanges e ON s.exchange_id = e.id "
            "WHERE e.code = ? AND s.is_active = 1",
            (exchange_code,),
        ).fetchone()
        conn.close()
        return row[0] > 0
    except Exception:
        return False


def _build_coinbase_client() -> BaseWebSocketClient:
    """Build the Coinbase WebSocket client."""
    from realtime.ingest.coinbase_websocket import CoinbaseWebSocketClient
    symbols = list(dict.fromkeys(
        config.PORTFOLIO_SYMBOLS + config.TOP_CRYPTO_SYMBOLS
    ))
    return CoinbaseWebSocketClient(symbols=symbols, db_path=config.DB_PATH)


def _build_polymarket_client() -> BaseWebSocketClient:
    """Build the Polymarket WebSocket client."""
    from realtime.ingest.polymarket_websocket import PolymarketWebSocketClient
    return PolymarketWebSocketClient(db_path=config.DB_PATH)


# ── Feed registry ─────────────────────────────────────────────────────────
# Add new exchanges here. Each entry:
#   exchange_code: (builder_function, enabled_by_default)
# The builder is only called if the exchange has active securities in the DB.

FEED_REGISTRY = {
    "coinbase": (_build_coinbase_client, True),
    "polymarket": (_build_polymarket_client, True),
    # Future:
    # "kalshi": (_build_kalshi_client, True),
    # "binance": (_build_binance_client, True),
    # "kraken": (_build_kraken_client, True),
}


class RealTimeSupervisor:
    """
    Supervises multiple WebSocket feed clients with graceful shutdown.

    Each feed runs as an independent asyncio task. If one feed fails,
    it is restarted independently without affecting others.
    """

    def __init__(self, feeds: list = None):
        """
        Args:
            feeds: list of exchange codes to run (e.g. ['coinbase', 'polymarket']).
                   If None, auto-detects from DB (all exchanges with active securities).
        """
        self.feed_codes = feeds
        self.clients: dict[str, BaseWebSocketClient] = {}
        self.tasks: dict[str, asyncio.Task] = {}
        self.running = False

    def _discover_feeds(self) -> list:
        """Auto-detect which feeds to run based on active securities."""
        active = []
        for code, (builder, enabled) in FEED_REGISTRY.items():
            if not enabled:
                continue
            if _is_exchange_active(code):
                active.append(code)
                logger.info(f"Feed discovered: {code} (has active securities)")
            else:
                logger.info(f"Feed skipped: {code} (no active securities)")
        return active

    async def _run_feed(self, code: str, builder):
        """Run a single feed with automatic restart on failure."""
        restart_count = 0
        max_restart_delay = 120

        while self.running:
            try:
                client = builder()
                self.clients[code] = client
                logger.info(f"Starting feed: {code} ({len(client.symbols)} symbols)")
                await client.connect()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Feed {code} crashed: {e}", exc_info=True)
                if self.running:
                    restart_count += 1
                    delay = min(2 ** restart_count, max_restart_delay)
                    logger.info(f"Restarting feed {code} in {delay}s (attempt #{restart_count})")
                    await asyncio.sleep(delay)
            finally:
                if code in self.clients:
                    try:
                        await self.clients[code].disconnect()
                    except Exception:
                        pass

    async def run(self):
        self.running = True

        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(self.stop()))

        # Discover or use provided feed list
        codes = self.feed_codes or self._discover_feeds()
        if not codes:
            logger.warning("No feeds to run. Exiting.")
            return

        # Launch each feed as an independent task
        for code in codes:
            if code not in FEED_REGISTRY:
                logger.warning(f"Unknown feed: {code}")
                continue
            builder, _ = FEED_REGISTRY[code]
            self.tasks[code] = asyncio.create_task(
                self._run_feed(code, builder),
                name=f"feed-{code}",
            )

        logger.info(f"Supervisor started: {len(self.tasks)} feeds ({', '.join(codes)})")

        # Wait for all tasks to complete (they run forever until stopped)
        try:
            await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        except asyncio.CancelledError:
            pass

        logger.info("Supervisor stopped.")

    async def stop(self):
        logger.info("Supervisor shutdown requested.")
        self.running = False

        # Stop all clients
        for code, client in self.clients.items():
            client.running = False

        # Cancel all tasks
        for code, task in self.tasks.items():
            if not task.done():
                task.cancel()

        # Wait for tasks to finish
        if self.tasks:
            await asyncio.gather(*self.tasks.values(), return_exceptions=True)


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="Real-time feed supervisor")
    parser.add_argument(
        "--feeds", nargs="*", default=None,
        help="Exchange codes to run (e.g. coinbase polymarket). Auto-detects if omitted.",
    )
    args = parser.parse_args()

    supervisor = RealTimeSupervisor(feeds=args.feeds)
    await supervisor.run()


if __name__ == "__main__":
    asyncio.run(main())
