#!/usr/bin/env python3
"""
Tax lot processor — FIFO cost basis tracking, realized gains, and cost attribution.

Processes trades into tax_lots, lot_dispositions, and cost_ledger entries.
"""

import sqlite3
from datetime import datetime, timedelta

from config import DB_PATH


class TaxProcessor:
    def __init__(self, db_path=DB_PATH):
        self.db_path = db_path

    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def process_trade(self, trade_id: int):
        """Process a single trade: populate cost_ledger, create/deplete tax lots."""
        conn = self._conn()
        trade = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
        if not trade:
            conn.close()
            return
        trade = dict(trade)

        # 1. Populate cost ledger
        total_costs = self._populate_cost_ledger(conn, trade)

        # 2. Create or deplete tax lots
        if trade["side"] == "BUY":
            self._create_tax_lot(conn, trade, total_costs)
        elif trade["side"] == "SELL":
            self._deplete_lots_fifo(conn, trade, total_costs)

        conn.commit()
        conn.close()

    def _populate_cost_ledger(self, conn, trade: dict) -> float:
        """Create cost_ledger entries for a trade. Returns total cost USD."""
        trade_id = trade["id"]
        portfolio_id = trade["portfolio_id"]
        symbol = trade["symbol"]
        strategy_id = trade.get("strategy_id")
        total = 0.0

        # Check if already populated
        existing = conn.execute(
            "SELECT COUNT(*) FROM cost_ledger WHERE trade_id = ?", (trade_id,)
        ).fetchone()[0]
        if existing > 0:
            # Return existing total
            row = conn.execute(
                "SELECT SUM(amount_usd) FROM cost_ledger WHERE trade_id = ?", (trade_id,)
            ).fetchone()
            return row[0] or 0.0

        # Exchange fee
        fee = trade.get("fee") or 0.0
        if fee > 0:
            conn.execute(
                "INSERT INTO cost_ledger (trade_id, portfolio_id, symbol, cost_type, amount_usd, strategy_id, detail) "
                "VALUES (?, ?, ?, 'exchange_fee', ?, ?, ?)",
                (trade_id, portfolio_id, symbol, fee, strategy_id, f"fee={fee}"),
            )
            total += fee

        # Gas fee (DEX trades)
        gas_used = trade.get("gas_used")
        gas_price_gwei = trade.get("gas_price_gwei")
        if gas_used and gas_price_gwei:
            # Convert gas to ETH: gas_used * gas_price_gwei * 1e-9
            gas_eth = gas_used * gas_price_gwei * 1e-9
            # Look up ETH price at trade time
            eth_price = self._get_eth_price(conn, trade["created_at"])
            gas_usd = gas_eth * eth_price
            if gas_usd > 0:
                conn.execute(
                    "INSERT INTO cost_ledger (trade_id, portfolio_id, symbol, cost_type, amount_usd, strategy_id, detail) "
                    "VALUES (?, ?, ?, 'gas_fee', ?, ?, ?)",
                    (trade_id, portfolio_id, symbol, gas_usd, strategy_id,
                     f"gas={gas_used} gwei={gas_price_gwei} eth_price={eth_price:.2f}"),
                )
                total += gas_usd

        # Slippage
        slippage_bps = trade.get("slippage_bps")
        amount_usd = trade.get("amount_usd") or 0.0
        if slippage_bps and amount_usd:
            slippage_usd = abs(slippage_bps) * amount_usd / 10000.0
            if slippage_usd > 0:
                conn.execute(
                    "INSERT INTO cost_ledger (trade_id, portfolio_id, symbol, cost_type, amount_usd, strategy_id, detail) "
                    "VALUES (?, ?, ?, 'slippage', ?, ?, ?)",
                    (trade_id, portfolio_id, symbol, slippage_usd, strategy_id,
                     f"slippage_bps={slippage_bps}"),
                )
                total += slippage_usd

        return total

    def _get_eth_price(self, conn, timestamp: str) -> float:
        """Look up ETH price from candlesticks at or before the given timestamp."""
        row = conn.execute(
            "SELECT close FROM candlesticks WHERE symbol='ETH-USD' AND timeframe='1h' "
            "AND timestamp <= ? ORDER BY timestamp DESC LIMIT 1",
            (timestamp,),
        ).fetchone()
        if row:
            return row[0]
        # Fallback: get latest ETH price available
        row = conn.execute(
            "SELECT close FROM candlesticks WHERE symbol='ETH-USD' AND timeframe='1h' "
            "ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
        return row[0] if row else 2000.0  # last resort fallback

    def _create_tax_lot(self, conn, trade: dict, total_costs: float):
        """Create a tax lot from a BUY trade."""
        # Check idempotency
        existing = conn.execute(
            "SELECT id FROM tax_lots WHERE buy_trade_id = ?", (trade["id"],)
        ).fetchone()
        if existing:
            return

        quantity = trade["quantity"]
        price = trade["price"]
        cost_basis_per_unit = price + (total_costs / quantity if quantity > 0 else 0)
        total_cost_basis = cost_basis_per_unit * quantity

        conn.execute(
            "INSERT INTO tax_lots (portfolio_id, symbol, buy_trade_id, acquired_at, "
            "original_quantity, remaining_quantity, cost_basis_per_unit, total_cost_basis, "
            "status, strategy_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)",
            (trade["portfolio_id"], trade["symbol"], trade["id"], trade["created_at"],
             quantity, quantity, cost_basis_per_unit, total_cost_basis, trade.get("strategy_id")),
        )

    def _deplete_lots_fifo(self, conn, trade: dict, total_sell_costs: float):
        """FIFO lot depletion for a SELL trade."""
        # Check idempotency
        existing = conn.execute(
            "SELECT COUNT(*) FROM lot_dispositions WHERE sell_trade_id = ?", (trade["id"],)
        ).fetchone()[0]
        if existing > 0:
            return

        portfolio_id = trade["portfolio_id"]
        symbol = trade["symbol"]
        sell_quantity = trade["quantity"]
        sell_price = trade["price"]

        # Per-unit sell cost adjustment
        cost_per_unit_sell = total_sell_costs / sell_quantity if sell_quantity > 0 else 0
        proceeds_per_unit = sell_price - cost_per_unit_sell

        # Get open lots FIFO order
        lots = conn.execute(
            "SELECT * FROM tax_lots WHERE portfolio_id = ? AND symbol = ? AND status = 'open' "
            "AND remaining_quantity > 0 ORDER BY acquired_at ASC",
            (portfolio_id, symbol),
        ).fetchall()

        remaining_to_sell = sell_quantity
        for lot in lots:
            if remaining_to_sell <= 0:
                break
            lot = dict(lot)
            consume = min(lot["remaining_quantity"], remaining_to_sell)

            # Calculate holding period
            acquired = datetime.fromisoformat(lot["acquired_at"].replace("Z", "+00:00") if "Z" in lot["acquired_at"] else lot["acquired_at"])
            disposed = datetime.fromisoformat(trade["created_at"].replace("Z", "+00:00") if "Z" in trade["created_at"] else trade["created_at"])
            holding_days = max((disposed - acquired).days, 0)
            term = "long" if holding_days >= 365 else "short"

            realized_gain = (proceeds_per_unit - lot["cost_basis_per_unit"]) * consume

            conn.execute(
                "INSERT INTO lot_dispositions (tax_lot_id, sell_trade_id, portfolio_id, symbol, "
                "quantity, proceeds_per_unit, cost_basis_per_unit, realized_gain_usd, "
                "holding_period_days, term, disposed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (lot["id"], trade["id"], portfolio_id, symbol,
                 consume, proceeds_per_unit, lot["cost_basis_per_unit"], realized_gain,
                 holding_days, term, trade["created_at"]),
            )

            new_remaining = lot["remaining_quantity"] - consume
            new_status = "depleted" if new_remaining <= 1e-12 else "open"
            conn.execute(
                "UPDATE tax_lots SET remaining_quantity = ?, status = ? WHERE id = ?",
                (max(new_remaining, 0), new_status, lot["id"]),
            )

            remaining_to_sell -= consume

    def detect_wash_sales(self, portfolio_id: int = None):
        """Scan dispositions for wash sales (same symbol bought within 30 days of loss sale)."""
        conn = self._conn()

        where = "WHERE d.realized_gain_usd < 0 AND d.is_wash_sale = 0"
        params = []
        if portfolio_id is not None:
            where += " AND d.portfolio_id = ?"
            params.append(portfolio_id)

        dispositions = conn.execute(
            f"SELECT d.*, tl.acquired_at AS lot_acquired_at FROM lot_dispositions d "
            f"JOIN tax_lots tl ON d.tax_lot_id = tl.id {where} "
            f"ORDER BY d.disposed_at",
            params,
        ).fetchall()

        updated = 0
        for disp in dispositions:
            disp = dict(disp)
            disposed_at = datetime.fromisoformat(
                disp["disposed_at"].replace("Z", "+00:00") if "Z" in disp["disposed_at"] else disp["disposed_at"]
            )
            window_start = disposed_at - timedelta(days=30)
            window_end = disposed_at + timedelta(days=30)

            # Look for replacement purchase within 30-day window
            replacement = conn.execute(
                "SELECT id FROM tax_lots WHERE portfolio_id = ? AND symbol = ? "
                "AND buy_trade_id != ? AND acquired_at BETWEEN ? AND ? "
                "ORDER BY acquired_at ASC LIMIT 1",
                (disp["portfolio_id"], disp["symbol"], disp["sell_trade_id"],
                 window_start.isoformat(), window_end.isoformat()),
            ).fetchone()

            if replacement:
                wash_disallowed = abs(disp["realized_gain_usd"])
                conn.execute(
                    "UPDATE lot_dispositions SET is_wash_sale = 1, wash_disallowed_usd = ?, "
                    "replacement_lot_id = ? WHERE id = ?",
                    (wash_disallowed, replacement[0], disp["id"]),
                )
                # Add disallowed amount to replacement lot's cost basis
                conn.execute(
                    "UPDATE tax_lots SET cost_basis_per_unit = cost_basis_per_unit + (? / remaining_quantity), "
                    "total_cost_basis = total_cost_basis + ? WHERE id = ? AND remaining_quantity > 0",
                    (wash_disallowed, wash_disallowed, replacement[0]),
                )
                updated += 1

        conn.commit()
        conn.close()
        return updated

    def backfill_all(self):
        """Process all existing trades chronologically."""
        conn = self._conn()
        trades = conn.execute(
            "SELECT id FROM trades WHERE status = 'filled' ORDER BY created_at ASC"
        ).fetchall()
        conn.close()

        print(f"Backfilling {len(trades)} trades...")
        for i, row in enumerate(trades):
            self.process_trade(row["id"])
            if (i + 1) % 20 == 0:
                print(f"  Processed {i + 1}/{len(trades)} trades")

        print(f"  Processed {len(trades)} trades total")

        # Wash sale detection
        wash_count = self.detect_wash_sales()
        print(f"  Detected {wash_count} wash sales")

    def get_realized_gains(self, portfolio_id: int, year: int = None) -> dict:
        """Summary of realized gains by term."""
        conn = self._conn()
        where = "WHERE portfolio_id = ?"
        params = [portfolio_id]
        if year:
            where += " AND disposed_at >= ? AND disposed_at < ?"
            params.extend([f"{year}-01-01", f"{year + 1}-01-01"])

        rows = conn.execute(
            f"SELECT term, "
            f"SUM(realized_gain_usd) AS total_gain, "
            f"SUM(wash_disallowed_usd) AS total_wash_disallowed, "
            f"SUM(realized_gain_usd - wash_disallowed_usd) AS adjusted_gain, "
            f"COUNT(*) AS disposition_count "
            f"FROM lot_dispositions {where} GROUP BY term",
            params,
        ).fetchall()
        conn.close()

        result = {"short": {}, "long": {}, "total": {}}
        total_gain = 0.0
        total_adjusted = 0.0
        total_wash = 0.0
        total_count = 0

        for row in rows:
            row = dict(row)
            term = row["term"]
            result[term] = {
                "total_gain": row["total_gain"],
                "wash_disallowed": row["total_wash_disallowed"],
                "adjusted_gain": row["adjusted_gain"],
                "disposition_count": row["disposition_count"],
            }
            total_gain += row["total_gain"] or 0
            total_adjusted += row["adjusted_gain"] or 0
            total_wash += row["total_wash_disallowed"] or 0
            total_count += row["disposition_count"] or 0

        result["total"] = {
            "total_gain": total_gain,
            "wash_disallowed": total_wash,
            "adjusted_gain": total_adjusted,
            "disposition_count": total_count,
        }
        return result

    def get_cost_basis(self, portfolio_id: int) -> dict:
        """Current cost basis for open positions."""
        conn = self._conn()
        rows = conn.execute(
            "SELECT symbol, SUM(remaining_quantity) AS quantity, "
            "SUM(remaining_quantity * cost_basis_per_unit) AS total_cost_basis, "
            "SUM(remaining_quantity * cost_basis_per_unit) / SUM(remaining_quantity) AS avg_cost_basis "
            "FROM tax_lots WHERE portfolio_id = ? AND status = 'open' AND remaining_quantity > 0 "
            "GROUP BY symbol",
            (portfolio_id,),
        ).fetchall()
        conn.close()

        return {
            row["symbol"]: {
                "quantity": row["quantity"],
                "total_cost_basis": row["total_cost_basis"],
                "avg_cost_basis": row["avg_cost_basis"],
            }
            for row in rows
        }

    def get_cost_attribution(self, portfolio_id: int) -> dict:
        """Cost breakdown by strategy and type."""
        conn = self._conn()
        rows = conn.execute(
            "SELECT cost_type, strategy_id, sr.name AS strategy_name, "
            "SUM(amount_usd) AS total_usd, COUNT(*) AS count "
            "FROM cost_ledger cl "
            "LEFT JOIN strategy_registry sr ON cl.strategy_id = sr.id "
            "WHERE cl.portfolio_id = ? "
            "GROUP BY cost_type, strategy_id",
            (portfolio_id,),
        ).fetchall()
        conn.close()

        result = {}
        for row in rows:
            row = dict(row)
            cost_type = row["cost_type"]
            if cost_type not in result:
                result[cost_type] = []
            result[cost_type].append({
                "strategy_id": row["strategy_id"],
                "strategy_name": row["strategy_name"],
                "total_usd": row["total_usd"],
                "count": row["count"],
            })
        return result


if __name__ == "__main__":
    from migration.tax_cost_tables import migrate

    print("Running tax & cost tables migration...")
    migrate()
    print()

    print("Running backfill...")
    processor = TaxProcessor()
    processor.backfill_all()

    print()
    print("=== Realized Gains (Portfolio 1) ===")
    gains = processor.get_realized_gains(1)
    for term, data in gains.items():
        if data:
            print(f"  {term}: {data}")

    print()
    print("=== Cost Basis (Portfolio 1) ===")
    basis = processor.get_cost_basis(1)
    for symbol, data in basis.items():
        print(f"  {symbol}: qty={data['quantity']:.6f} basis=${data['total_cost_basis']:.2f} avg=${data['avg_cost_basis']:.2f}")

    print()
    print("=== Cost Attribution (Portfolio 1) ===")
    costs = processor.get_cost_attribution(1)
    for cost_type, entries in costs.items():
        total = sum(e["total_usd"] for e in entries)
        print(f"  {cost_type}: ${total:.4f} ({sum(e['count'] for e in entries)} events)")
