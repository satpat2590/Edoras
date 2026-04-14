#!/usr/bin/env python3
"""
DEX trade execution via Bankr API.

Handles:
  - Buy/sell swaps through Bankr's natural language API
  - Position tracking in the unified trades + positions tables
  - On-chain transaction logging in dex_transactions
  - Safety checks (liquidity, slippage, position limits)
  - Balance reconciliation between on-chain and DB state
"""

import json
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    DB_PATH, DEX_CONFIG, PORTFOLIO_ARWEN, TRADER_ALEPH, resolve_account_id,
)
from dex.bankr_client import BankrClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


class DexExecutor:
    """DEX trade execution via Bankr, synced to Arwen portfolio."""

    def __init__(
        self,
        db_path: str = DB_PATH,
        portfolio_id: int = PORTFOLIO_ARWEN,
        trader_id: int = TRADER_ALEPH,
        dry_run: bool = False,
    ):
        self.db_path = db_path
        self.portfolio_id = portfolio_id
        self.trader_id = trader_id
        self.dry_run = dry_run
        self.bankr = BankrClient()
        try:
            self._account_id = resolve_account_id(
                portfolio_id, venue_code="bankr", db_path=db_path
            )
        except (ValueError, Exception):
            self._account_id = None

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ── Safety checks ──────────────────────────────────────────────────────

    def check_dex_safety(self, symbol: str, amount_usd: float) -> Optional[str]:
        """Pre-trade safety checks. Returns error message or None if safe."""
        cfg = DEX_CONFIG

        if not cfg.get("enabled"):
            return "DEX trading is disabled"

        if amount_usd > cfg["max_single_order_usd"]:
            return f"Order ${amount_usd:.2f} exceeds max ${cfg['max_single_order_usd']:.2f}"

        # Daily volume check (Phase 3: query via account_id)
        conn = self._get_conn()
        cutoff = (datetime.now() - timedelta(hours=24)).isoformat()
        if self._account_id:
            row = conn.execute(
                "SELECT COALESCE(SUM(amount_usd), 0) FROM trades "
                "WHERE account_id = ? AND created_at > ?",
                (self._account_id, cutoff),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COALESCE(SUM(amount_usd), 0) FROM trades "
                "WHERE portfolio_id = ? AND created_at > ?",
                (self.portfolio_id, cutoff),
            ).fetchone()
        daily_vol = row[0]
        conn.close()
        if daily_vol + amount_usd > cfg["max_daily_volume_usd"]:
            return f"Daily volume ${daily_vol + amount_usd:.2f} exceeds limit ${cfg['max_daily_volume_usd']:.2f}"

        # DEX token metadata checks
        conn = self._get_conn()
        row = conn.execute(
            "SELECT dt.liquidity, dt.volume_24h, dt.holder_count, dt.is_verified "
            "FROM securities s JOIN dex_tokens dt ON dt.security_id = s.id "
            "WHERE s.symbol = ? AND s.is_dex = 1 LIMIT 1",
            (symbol,),
        ).fetchone()
        conn.close()

        if row:
            if row["liquidity"] and row["liquidity"] < cfg["min_liquidity_usd"]:
                return f"Liquidity ${row['liquidity']:.0f} below minimum ${cfg['min_liquidity_usd']:.0f}"
            if row["holder_count"] and row["holder_count"] < cfg["min_holder_count"]:
                return f"Holder count {row['holder_count']} below minimum {cfg['min_holder_count']}"

        # Position concentration check (Phase 3: query via account_id)
        conn = self._get_conn()
        if self._account_id:
            total_row = conn.execute(
                "SELECT COALESCE(SUM(quantity * COALESCE(current_price, entry_price)), 0) "
                "FROM positions WHERE account_id = ? AND status = 'open'",
                (self._account_id,),
            ).fetchone()
            pos_row = conn.execute(
                "SELECT quantity * COALESCE(current_price, entry_price) as value "
                "FROM positions WHERE account_id = ? AND symbol = ? AND status = 'open'",
                (self._account_id, symbol),
            ).fetchone()
        else:
            total_row = conn.execute(
                "SELECT COALESCE(SUM(quantity * COALESCE(current_price, entry_price)), 0) "
                "FROM positions WHERE portfolio_id = ? AND status = 'open'",
                (self.portfolio_id,),
            ).fetchone()
            pos_row = conn.execute(
                "SELECT quantity * COALESCE(current_price, entry_price) as value "
                "FROM positions WHERE portfolio_id = ? AND symbol = ? AND status = 'open'",
                (self.portfolio_id, symbol),
            ).fetchone()
        existing_value = pos_row["value"] if pos_row else 0
        total_value = total_row[0] if total_row else 0
        conn.close()

        if total_value > 0:
            new_pct = (existing_value + amount_usd) / (total_value + amount_usd)
            if new_pct > cfg["max_position_size_percent"] / 100:
                return f"Position would be {new_pct:.0%} of portfolio (max {cfg['max_position_size_percent']}%)"

        return None

    # ── Execution ──────────────────────────────────────────────────────────

    def execute_buy(self, symbol: str, amount_usd: float,
                    chain: str = None, reason: str = "") -> dict:
        """Buy a DEX token: ETH → Token swap via Bankr.

        Returns: {"success": bool, "trade_id": int, "tx_hash": str, ...}
        """
        chain = chain or DEX_CONFIG["default_chain"]
        token = symbol.replace("-BASE", "").replace("-ETH", "").replace("-USD", "")

        # Safety checks
        error = self.check_dex_safety(symbol, amount_usd)
        if error:
            logger.warning(f"[dex] Buy rejected: {error}")
            return {"success": False, "error": error}

        if self.dry_run:
            logger.info(f"[dex] DRY-RUN: Would buy ${amount_usd:.2f} of {symbol} on {chain}")
            return {"success": True, "mode": "dry-run", "symbol": symbol,
                    "amount_usd": amount_usd}

        # Calculate ETH amount to swap
        eth_price = self._get_eth_price()
        if not eth_price:
            return {"success": False, "error": "Could not determine ETH price"}
        eth_amount = amount_usd / eth_price

        # Execute swap
        logger.info(f"[dex] Executing buy: {eth_amount:.6f} ETH → {token} on {chain}")
        swap_result = self.bankr.execute_swap(
            from_token="ETH",
            to_token=token,
            amount=round(eth_amount, 6),
            chain=chain,
            max_slippage=DEX_CONFIG["max_slippage_percent"],
        )

        status = swap_result.get("status", "unknown")
        tx_hash = swap_result.get("tx_hash")
        amount_out = swap_result.get("amount_out")

        if status not in ("completed", "done", "success"):
            self._record_dex_transaction(
                chain=chain, action="swap", from_token="ETH", to_token=token,
                amount_in=eth_amount, amount_out=0, price=0,
                tx_hash=tx_hash, status="failed",
                bankr_job_id=swap_result.get("job_id"),
                error_message=str(swap_result.get("raw", {}).get("error", status)),
            )
            return {"success": False, "error": f"Swap {status}", "raw": swap_result}

        # Calculate effective price
        price = amount_usd / amount_out if amount_out else 0
        quantity = amount_out or 0
        fee = amount_usd * 0.003  # estimate 0.3% DEX fee

        # Record in trades table
        trade_id = self._record_trade(
            symbol=symbol, side="BUY", quantity=quantity, price=price,
            amount_usd=amount_usd, fee=fee,
            decision_context=json.dumps({
                "signal_type": "dex_manual",
                "chain": chain,
                "tx_hash": tx_hash,
                "bankr_job_id": swap_result.get("job_id"),
                "reason": reason,
                "eth_price": eth_price,
                "eth_spent": eth_amount,
            }),
        )

        # Record in dex_transactions
        self._record_dex_transaction(
            chain=chain, action="swap", from_token="ETH", to_token=token,
            amount_in=eth_amount, amount_out=quantity, price=price,
            tx_hash=tx_hash, status="confirmed",
            bankr_job_id=swap_result.get("job_id"),
            trade_id=trade_id,
        )

        # Update position
        self._update_position(symbol, "BUY", quantity, price)

        logger.info(f"[dex] Buy complete: {quantity:.6g} {symbol} @ ${price:.6g} (tx: {tx_hash})")
        return {
            "success": True, "trade_id": trade_id, "tx_hash": tx_hash,
            "symbol": symbol, "quantity": quantity, "price": price,
            "amount_usd": amount_usd, "chain": chain,
        }

    def execute_sell(self, symbol: str, amount: float = None,
                     sell_pct: float = None, chain: str = None,
                     reason: str = "") -> dict:
        """Sell a DEX token: Token → ETH swap via Bankr.

        Specify either amount (token quantity) or sell_pct (0.0-1.0).
        """
        chain = chain or DEX_CONFIG["default_chain"]
        token = symbol.replace("-BASE", "").replace("-ETH", "").replace("-USD", "")

        # Get current position (Phase 3: query via account_id)
        conn = self._get_conn()
        if self._account_id:
            pos = conn.execute(
                "SELECT quantity, entry_price, current_price FROM positions "
                "WHERE account_id = ? AND symbol = ? AND status = 'open'",
                (self._account_id, symbol),
            ).fetchone()
        else:
            pos = conn.execute(
                "SELECT quantity, entry_price, current_price FROM positions "
                "WHERE portfolio_id = ? AND symbol = ? AND status = 'open'",
                (self.portfolio_id, symbol),
            ).fetchone()
        conn.close()

        if not pos:
            return {"success": False, "error": f"No open position in {symbol}"}

        if sell_pct is not None:
            quantity = pos["quantity"] * min(max(sell_pct, 0), 1.0)
        elif amount is not None:
            quantity = min(amount, pos["quantity"])
        else:
            quantity = pos["quantity"]  # sell all

        if quantity <= 0:
            return {"success": False, "error": "Nothing to sell"}

        if self.dry_run:
            logger.info(f"[dex] DRY-RUN: Would sell {quantity:.6g} {symbol} on {chain}")
            return {"success": True, "mode": "dry-run", "symbol": symbol,
                    "quantity": quantity}

        # Execute swap
        logger.info(f"[dex] Executing sell: {quantity:.6g} {token} → ETH on {chain}")
        swap_result = self.bankr.execute_swap(
            from_token=token,
            to_token="ETH",
            amount=round(quantity, 8),
            chain=chain,
            max_slippage=DEX_CONFIG["max_slippage_percent"],
        )

        status = swap_result.get("status", "unknown")
        tx_hash = swap_result.get("tx_hash")
        eth_received = swap_result.get("amount_out", 0)

        if status not in ("completed", "done", "success"):
            self._record_dex_transaction(
                chain=chain, action="swap", from_token=token, to_token="ETH",
                amount_in=quantity, amount_out=0, price=0,
                tx_hash=tx_hash, status="failed",
                bankr_job_id=swap_result.get("job_id"),
                error_message=str(swap_result.get("raw", {}).get("error", status)),
            )
            return {"success": False, "error": f"Swap {status}", "raw": swap_result}

        eth_price = self._get_eth_price()
        amount_usd = (eth_received or 0) * (eth_price or 0)
        price = amount_usd / quantity if quantity else 0
        fee = amount_usd * 0.003

        trade_id = self._record_trade(
            symbol=symbol, side="SELL", quantity=quantity, price=price,
            amount_usd=amount_usd, fee=fee,
            decision_context=json.dumps({
                "signal_type": "dex_manual",
                "chain": chain,
                "tx_hash": tx_hash,
                "bankr_job_id": swap_result.get("job_id"),
                "reason": reason,
                "eth_received": eth_received,
                "eth_price": eth_price,
            }),
        )

        self._record_dex_transaction(
            chain=chain, action="swap", from_token=token, to_token="ETH",
            amount_in=quantity, amount_out=eth_received or 0, price=price,
            tx_hash=tx_hash, status="confirmed",
            bankr_job_id=swap_result.get("job_id"),
            trade_id=trade_id,
        )

        self._update_position(symbol, "SELL", quantity, price)

        logger.info(f"[dex] Sell complete: {quantity:.6g} {symbol} → {eth_received:.6g} ETH")
        return {
            "success": True, "trade_id": trade_id, "tx_hash": tx_hash,
            "symbol": symbol, "quantity": quantity, "price": price,
            "amount_usd": amount_usd, "chain": chain,
        }

    # ── Portfolio sync ─────────────────────────────────────────────────────

    def sync_balances(self) -> dict:
        """Reconcile DB positions with actual on-chain wallet balances."""
        balance_data = self.bankr.get_balances()
        balances = balance_data.get("balances", [])

        conn = self._get_conn()
        if self._account_id:
            db_positions = conn.execute(
                "SELECT symbol, quantity FROM positions "
                "WHERE account_id = ? AND status = 'open'",
                (self._account_id,),
            ).fetchall()
        else:
            db_positions = conn.execute(
                "SELECT symbol, quantity FROM positions "
                "WHERE portfolio_id = ? AND status = 'open'",
                (self.portfolio_id,),
            ).fetchall()
        conn.close()

        db_map = {row["symbol"]: row["quantity"] for row in db_positions}

        # Build chain balance map (token -> amount)
        chain_map = {}
        for b in balances:
            token = b.get("token", "").upper()
            chain_map[token] = b.get("amount", 0)

        report = {
            "on_chain": chain_map,
            "in_db": db_map,
            "discrepancies": [],
        }

        # Check each DB position against chain
        for symbol, db_qty in db_map.items():
            token = symbol.replace("-BASE", "").replace("-ETH", "").replace("-USD", "")
            chain_qty = chain_map.get(token, 0)
            if abs(db_qty - chain_qty) > 0.0001 * max(db_qty, 1):
                report["discrepancies"].append({
                    "symbol": symbol,
                    "db_quantity": db_qty,
                    "chain_quantity": chain_qty,
                    "difference": chain_qty - db_qty,
                })

        return report

    def get_wallet_summary(self) -> dict:
        """Get current wallet state from Bankr."""
        return self.bankr.get_balances()

    # ── DB helpers ─────────────────────────────────────────────────────────

    def _record_trade(self, symbol: str, side: str, quantity: float,
                      price: float, amount_usd: float, fee: float,
                      decision_context: str = None) -> int:
        """Insert into unified trades table. Returns trade ID."""
        conn = self._get_conn()

        # Get current portfolio value (Phase 3: query via account_id)
        if self._account_id:
            pv_row = conn.execute(
                "SELECT COALESCE(SUM(quantity * COALESCE(current_price, entry_price)), 0) "
                "FROM positions WHERE account_id = ? AND status = 'open'",
                (self._account_id,),
            ).fetchone()
            last_cash = conn.execute(
                "SELECT cash_after FROM trades WHERE account_id = ? ORDER BY id DESC LIMIT 1",
                (self._account_id,),
            ).fetchone()
        else:
            pv_row = conn.execute(
                "SELECT COALESCE(SUM(quantity * COALESCE(current_price, entry_price)), 0) "
                "FROM positions WHERE portfolio_id = ? AND status = 'open'",
                (self.portfolio_id,),
            ).fetchone()
            last_cash = conn.execute(
                "SELECT cash_after FROM trades WHERE portfolio_id = ? ORDER BY id DESC LIMIT 1",
                (self.portfolio_id,),
            ).fetchone()
        portfolio_value = pv_row[0] if pv_row else 0

        # Cash tracking: for DEX, cash is on-chain ETH — approximate
        cash = last_cash["cash_after"] if last_cash else 0

        if side == "BUY":
            cash -= amount_usd
        else:
            cash += amount_usd

        conn.execute("""
            INSERT INTO trades
                (portfolio_id, account_id, symbol, side, quantity, price, amount_usd, fee,
                 order_type, status, decision_context, trader_id,
                 portfolio_value, cash_after, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'market', 'filled', ?, ?, ?, ?, ?)
        """, (
            self.portfolio_id, self._account_id, symbol, side, quantity, price, amount_usd, fee,
            decision_context, self.trader_id, portfolio_value, cash,
            datetime.now().isoformat(),
        ))
        trade_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        conn.close()
        return trade_id

    def _record_dex_transaction(self, chain: str, action: str,
                                 from_token: str, to_token: str,
                                 amount_in: float, amount_out: float,
                                 price: float, tx_hash: str = None,
                                 status: str = "pending",
                                 bankr_job_id: str = None,
                                 trade_id: int = None,
                                 error_message: str = None):
        """Insert into dex_transactions table."""
        conn = self._get_conn()

        # Look up security_id if possible
        sec_row = conn.execute(
            "SELECT id FROM securities WHERE symbol LIKE ? AND is_dex = 1 LIMIT 1",
            (f"%{to_token if action == 'swap' else from_token}%",),
        ).fetchone()
        sec_id = sec_row[0] if sec_row else None

        conn.execute("""
            INSERT OR IGNORE INTO dex_transactions
                (trade_id, portfolio_id, security_id, tx_hash, chain, action,
                 from_token, to_token, amount_in, amount_out, price,
                 bankr_job_id, status, error_message, created_at,
                 confirmed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            trade_id, self.portfolio_id, sec_id, tx_hash, chain, action,
            from_token, to_token, amount_in, amount_out, price,
            bankr_job_id, status, error_message, datetime.now().isoformat(),
            datetime.now().isoformat() if status == "confirmed" else None,
        ))
        conn.commit()
        conn.close()

    def _update_position(self, symbol: str, side: str,
                         quantity: float, price: float):
        """Update positions table — weighted average for buys, reduce for sells."""
        conn = self._get_conn()
        if self._account_id:
            pos = conn.execute(
                "SELECT id, quantity, entry_price FROM positions "
                "WHERE account_id = ? AND symbol = ? AND status = 'open'",
                (self._account_id, symbol),
            ).fetchone()
        else:
            pos = conn.execute(
                "SELECT id, quantity, entry_price FROM positions "
                "WHERE portfolio_id = ? AND symbol = ? AND status = 'open'",
                (self.portfolio_id, symbol),
            ).fetchone()

        now = datetime.now().isoformat()

        if side == "BUY":
            if pos:
                # Weighted average cost basis
                old_qty = pos["quantity"]
                old_avg = pos["entry_price"]
                new_qty = old_qty + quantity
                new_avg = (old_qty * old_avg + quantity * price) / new_qty if new_qty else 0
                conn.execute(
                    "UPDATE positions SET quantity = ?, entry_price = ?, "
                    "current_price = ?, updated_at = ? WHERE id = ?",
                    (new_qty, new_avg, price, now, pos["id"]),
                )
            else:
                conn.execute("""
                    INSERT INTO positions
                        (portfolio_id, account_id, symbol, quantity, entry_price, entry_time,
                         current_price, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)
                """, (self.portfolio_id, self._account_id, symbol, quantity, price, now, price, now, now))

        elif side == "SELL":
            if pos:
                remaining = pos["quantity"] - quantity
                if remaining <= 0.000001:
                    # Close position
                    pnl = (price - pos["entry_price"]) * pos["quantity"]
                    pnl_pct = ((price / pos["entry_price"]) - 1) * 100 if pos["entry_price"] else 0
                    conn.execute(
                        "UPDATE positions SET quantity = 0, current_price = ?, "
                        "pnl = ?, pnl_percent = ?, status = 'closed', updated_at = ? "
                        "WHERE id = ?",
                        (price, pnl, pnl_pct, now, pos["id"]),
                    )
                else:
                    pnl = (price - pos["entry_price"]) * quantity
                    conn.execute(
                        "UPDATE positions SET quantity = ?, current_price = ?, "
                        "pnl = ?, updated_at = ? WHERE id = ?",
                        (remaining, price, pnl, now, pos["id"]),
                    )

        conn.commit()
        conn.close()

    def _get_eth_price(self) -> Optional[float]:
        """Get ETH price from DB (Coinbase feed) or Bankr."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT close FROM candlesticks WHERE symbol = 'ETH-USD' "
            "AND timeframe = '1h' ORDER BY timestamp DESC LIMIT 1",
        ).fetchone()
        conn.close()
        if row:
            return float(row[0])
        # Fallback to Bankr
        return self.bankr.get_token_price("ETH", "ethereum")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="DEX Executor")
    parser.add_argument("action", choices=["buy", "sell", "balance", "sync", "health"])
    parser.add_argument("--symbol", type=str, help="Token symbol (e.g. VVV-BASE)")
    parser.add_argument("--amount", type=float, help="USD amount for buy, token qty for sell")
    parser.add_argument("--pct", type=float, help="Sell percentage (0-100)")
    parser.add_argument("--chain", type=str, default="base")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--reason", type=str, default="")
    args = parser.parse_args()

    executor = DexExecutor(dry_run=args.dry_run)

    if args.action == "buy":
        if not args.symbol or not args.amount:
            print("Error: --symbol and --amount required for buy")
            return
        result = executor.execute_buy(args.symbol, args.amount, args.chain, args.reason)
        print(json.dumps(result, indent=2, default=str))

    elif args.action == "sell":
        if not args.symbol:
            print("Error: --symbol required for sell")
            return
        sell_pct = args.pct / 100 if args.pct else None
        result = executor.execute_sell(args.symbol, amount=args.amount,
                                       sell_pct=sell_pct, chain=args.chain,
                                       reason=args.reason)
        print(json.dumps(result, indent=2, default=str))

    elif args.action == "balance":
        result = executor.get_wallet_summary()
        balances = result.get("balances", [])
        if balances:
            print(f"\n  {'Token':<10} {'Chain':<10} {'Amount':>15} {'USD Value':>12}")
            print(f"  {'-'*10} {'-'*10} {'-'*15} {'-'*12}")
            for b in balances:
                print(f"  {b['token']:<10} {b['chain']:<10} {b['amount']:>15.6g} "
                      f"${b['usd_value']:>11.2f}")
        else:
            print("  No balances found (or API returned empty)")
            if result.get("raw"):
                print(f"  Raw: {json.dumps(result['raw'], indent=2, default=str)[:500]}")

    elif args.action == "sync":
        result = executor.sync_balances()
        print(f"\n  On-chain: {result.get('on_chain', {})}")
        print(f"  In DB: {result.get('in_db', {})}")
        if result.get("discrepancies"):
            print(f"\n  Discrepancies:")
            for d in result["discrepancies"]:
                print(f"    {d['symbol']}: DB={d['db_quantity']:.6g} "
                      f"Chain={d['chain_quantity']:.6g} "
                      f"Diff={d['difference']:+.6g}")
        else:
            print("\n  No discrepancies found")

    elif args.action == "health":
        result = executor.bankr.health_check()
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
