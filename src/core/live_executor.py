#!/usr/bin/env python3
"""
Live order execution via Coinbase API.
Supports dry-run, paper, and live modes with strict safety limits.
Includes position reconciliation and order management.
"""

import os
import sys
import json
import time
import sqlite3
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    DB_PATH,
    LIVE_MAX_SINGLE_ORDER_USD,
    LIVE_MAX_DAILY_VOLUME_USD,
    LIVE_MAX_OPEN_ORDERS,
    LIVE_MIN_ORDER_INTERVAL_SEC,
    TELEGRAM_CHAT_ID,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


@dataclass
class OrderRecord:
    order_id: str
    symbol: str
    side: str  # "BUY" or "SELL"
    amount_usd: float
    price: float
    quantity: float
    status: str  # "pending", "filled", "cancelled", "failed"
    mode: str  # "live", "dry-run", "paper"
    timestamp: datetime = field(default_factory=datetime.now)
    fill_price: Optional[float] = None
    error: Optional[str] = None


class LiveExecutor:
    """
    Handles order execution with multiple modes and safety limits.

    Modes:
    - 'paper': log trades only, no API calls
    - 'dry-run': validate and log what would be executed
    - 'live': actually place orders via Coinbase (requires LIVE_TRADING_ENABLED=true)
    """

    def __init__(
        self,
        mode: str = "paper",
        api_key: str = None,
        api_secret: str = None,
    ):
        self.mode = mode
        self.order_log: List[OrderRecord] = []
        self.order_log_file = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "order_log.json"
        )
        self.last_order_time = 0.0
        self._load_order_log()

        # Only initialize Coinbase client for live mode
        self.client = None
        if mode == "live":
            if os.getenv("LIVE_TRADING_ENABLED") != "true":
                raise RuntimeError(
                    "Live trading requires LIVE_TRADING_ENABLED=true environment variable. "
                    "This is a safety check to prevent accidental live trades."
                )
            api_key = api_key or os.getenv("COINBASE_API_KEY")
            api_secret = api_secret or os.getenv("COINBASE_API_SECRET")
            if not api_key or not api_secret:
                raise ValueError("Coinbase API credentials required for live mode")
            if api_secret and "-----BEGIN EC PRIVATE KEY-----" in api_secret:
                api_secret = api_secret.replace("\\n", "\n")
            from coinbase.rest import RESTClient
            self.client = RESTClient(api_key=api_key, api_secret=api_secret)

    # ── Safety checks ────────────────────────────────────────────────────

    def _check_safety_limits(self, amount_usd: float) -> Optional[str]:
        """Validate order against safety limits. Returns error message or None."""
        # Max single order
        if amount_usd > LIVE_MAX_SINGLE_ORDER_USD:
            return f"Order ${amount_usd:.2f} exceeds max single order ${LIVE_MAX_SINGLE_ORDER_USD:.2f}"

        # Max daily volume
        today = datetime.now().date()
        daily_volume = sum(
            o.amount_usd for o in self.order_log
            if o.timestamp.date() == today and o.status in ("filled", "pending")
        )
        if daily_volume + amount_usd > LIVE_MAX_DAILY_VOLUME_USD:
            return f"Daily volume ${daily_volume + amount_usd:.2f} would exceed limit ${LIVE_MAX_DAILY_VOLUME_USD:.2f}"

        # Min interval between orders
        elapsed = time.time() - self.last_order_time
        if elapsed < LIVE_MIN_ORDER_INTERVAL_SEC:
            return f"Too soon since last order ({elapsed:.0f}s < {LIVE_MIN_ORDER_INTERVAL_SEC}s)"

        # Max open orders
        pending = sum(1 for o in self.order_log if o.status == "pending")
        if pending >= LIVE_MAX_OPEN_ORDERS:
            return f"Too many open orders ({pending} >= {LIVE_MAX_OPEN_ORDERS})"

        return None

    # ── Order execution ──────────────────────────────────────────────────

    def place_market_order(self, symbol: str, side: str, amount_usd: float) -> OrderRecord:
        """Place a market order with safety checks."""
        # Safety validation
        error = self._check_safety_limits(amount_usd)
        if error:
            record = OrderRecord(
                order_id=f"rejected-{int(time.time())}",
                symbol=symbol,
                side=side,
                amount_usd=amount_usd,
                price=0,
                quantity=0,
                status="failed",
                mode=self.mode,
                error=error,
            )
            self.order_log.append(record)
            self._save_order_log()
            logger.warning(f"Order rejected: {error}")
            return record

        if self.mode == "paper":
            return self._paper_order(symbol, side, amount_usd)
        elif self.mode == "dry-run":
            return self._dry_run_order(symbol, side, amount_usd)
        elif self.mode == "live":
            return self._live_order(symbol, side, amount_usd)
        else:
            raise ValueError(f"Unknown mode: {self.mode}")

    def _paper_order(self, symbol: str, side: str, amount_usd: float) -> OrderRecord:
        """Simulate an order using latest DB price."""
        price = self._get_price(symbol)
        quantity = amount_usd / price if price > 0 else 0

        record = OrderRecord(
            order_id=f"paper-{int(time.time() * 1000)}",
            symbol=symbol,
            side=side,
            amount_usd=amount_usd,
            price=price,
            quantity=quantity,
            status="filled",
            mode="paper",
            fill_price=price,
        )

        self.order_log.append(record)
        self.last_order_time = time.time()
        self._save_order_log()
        logger.info(f"[PAPER] {side} ${amount_usd:.2f} of {symbol} @ ${price:.4f}")
        return record

    def _dry_run_order(self, symbol: str, side: str, amount_usd: float) -> OrderRecord:
        """Validate and log what would be executed."""
        price = self._get_price(symbol)
        quantity = amount_usd / price if price > 0 else 0

        record = OrderRecord(
            order_id=f"dryrun-{int(time.time() * 1000)}",
            symbol=symbol,
            side=side,
            amount_usd=amount_usd,
            price=price,
            quantity=quantity,
            status="filled",
            mode="dry-run",
            fill_price=price,
        )

        self.order_log.append(record)
        self.last_order_time = time.time()
        self._save_order_log()
        logger.info(f"[DRY-RUN] Would {side} ${amount_usd:.2f} of {symbol} @ ${price:.4f}")
        return record

    def _live_order(self, symbol: str, side: str, amount_usd: float) -> OrderRecord:
        """Execute a real order via Coinbase API."""
        try:
            import uuid
            client_oid = str(uuid.uuid4())

            order_config = {
                "quote_size": str(round(amount_usd, 2)),
            }

            response = self.client.create_order(
                client_order_id=client_oid,
                product_id=symbol,
                side=side.upper(),
                order_configuration={"market_market_ioc": order_config},
            )

            order_id = getattr(response, "order_id", client_oid)
            success = getattr(response, "success", False)

            record = OrderRecord(
                order_id=order_id,
                symbol=symbol,
                side=side,
                amount_usd=amount_usd,
                price=0,
                quantity=0,
                status="pending" if success else "failed",
                mode="live",
                error=None if success else str(getattr(response, "error_response", "Unknown error")),
            )

            self.order_log.append(record)
            self.last_order_time = time.time()
            self._save_order_log()

            if success:
                logger.info(f"[LIVE] {side} ${amount_usd:.2f} of {symbol} — order_id={order_id}")
            else:
                logger.error(f"[LIVE] Order failed: {record.error}")

            return record

        except Exception as e:
            record = OrderRecord(
                order_id=f"error-{int(time.time())}",
                symbol=symbol,
                side=side,
                amount_usd=amount_usd,
                price=0,
                quantity=0,
                status="failed",
                mode="live",
                error=str(e),
            )
            self.order_log.append(record)
            self._save_order_log()
            logger.error(f"[LIVE] Exception: {e}")
            return record

    # ── Position reconciliation ──────────────────────────────────────────

    def reconcile_positions(self) -> Dict:
        """Compare actual Coinbase positions with expected state."""
        if not self.client:
            return {"error": "Reconciliation requires live mode with API access"}

        try:
            accounts = self.client.get_accounts()
            actual = {}

            if hasattr(accounts, "accounts"):
                for acct in accounts.accounts:
                    symbol = f"{acct.currency}-USD"
                    balance = float(getattr(acct, "available_balance", {}).get("value", 0))
                    if balance > 0.01:  # ignore dust
                        actual[symbol] = balance

            return {
                "actual_positions": actual,
                "timestamp": datetime.now().isoformat(),
            }

        except Exception as e:
            return {"error": str(e)}

    # ── Helpers ──────────────────────────────────────────────────────────

    def _get_price(self, symbol: str) -> float:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute(
            "SELECT close FROM candlesticks WHERE symbol=? AND timeframe='1h' "
            "ORDER BY timestamp DESC LIMIT 1",
            (symbol,),
        )
        row = cur.fetchone()
        conn.close()
        return float(row[0]) if row else 0.0

    def _load_order_log(self):
        if not os.path.exists(self.order_log_file):
            return
        try:
            with open(self.order_log_file, "r") as f:
                data = json.load(f)
            for entry in data:
                entry["timestamp"] = datetime.fromisoformat(entry["timestamp"])
                self.order_log.append(OrderRecord(**entry))
        except Exception as e:
            logger.warning(f"Could not load order log: {e}")

    def _save_order_log(self):
        try:
            data = []
            for o in self.order_log[-500:]:  # keep last 500
                d = {
                    "order_id": o.order_id,
                    "symbol": o.symbol,
                    "side": o.side,
                    "amount_usd": o.amount_usd,
                    "price": o.price,
                    "quantity": o.quantity,
                    "status": o.status,
                    "mode": o.mode,
                    "timestamp": o.timestamp.isoformat(),
                    "fill_price": o.fill_price,
                    "error": o.error,
                }
                data.append(d)
            with open(self.order_log_file, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.warning(f"Could not save order log: {e}")

    def get_daily_summary(self) -> str:
        """Generate a summary of today's orders."""
        today = datetime.now().date()
        today_orders = [o for o in self.order_log if o.timestamp.date() == today]

        if not today_orders:
            return "No orders today."

        lines = []
        lines.append(f"📋 **Order Summary ({today})**")
        lines.append(f"Mode: {self.mode}")
        lines.append("")

        total_volume = 0
        for o in today_orders:
            emoji = "✅" if o.status == "filled" else "❌" if o.status == "failed" else "⏳"
            lines.append(f"{emoji} {o.side} {o.symbol} ${o.amount_usd:.2f} [{o.status}]")
            if o.error:
                lines.append(f"   Error: {o.error}")
            if o.status == "filled":
                total_volume += o.amount_usd

        lines.append(f"\nTotal volume: ${total_volume:.2f}")
        lines.append(f"Remaining daily limit: ${LIVE_MAX_DAILY_VOLUME_USD - total_volume:.2f}")
        return "\n".join(lines)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Live Executor")
    parser.add_argument("--mode", default="paper", choices=["paper", "dry-run", "live"])
    parser.add_argument("--buy", type=str, help="Symbol to buy")
    parser.add_argument("--sell", type=str, help="Symbol to sell")
    parser.add_argument("--amount", type=float, default=25.0, help="USD amount")
    parser.add_argument("--reconcile", action="store_true", help="Reconcile positions")
    parser.add_argument("--summary", action="store_true", help="Show daily summary")
    args = parser.parse_args()

    executor = LiveExecutor(mode=args.mode)

    if args.buy:
        result = executor.place_market_order(args.buy, "BUY", args.amount)
        print(f"Order: {result.status} — {result.order_id}")
        if result.error:
            print(f"Error: {result.error}")
    elif args.sell:
        result = executor.place_market_order(args.sell, "SELL", args.amount)
        print(f"Order: {result.status} — {result.order_id}")
    elif args.reconcile:
        report = executor.reconcile_positions()
        print(json.dumps(report, indent=2))
    elif args.summary:
        print(executor.get_daily_summary())
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
