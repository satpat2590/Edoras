#!/usr/bin/env python3
"""
Trade Journal — records completed trade outcomes and learns from them.

Tracks entry/exit prices, signal type, holding period, outcome, and market
regime for every closed position. Provides performance analytics by signal
type, symbol, and regime to feed back into trading decisions.
"""

import json
import logging
import os
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "crypto_data.db")

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS trade_outcomes (
    trade_id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    entry_date TEXT NOT NULL,
    exit_date TEXT NOT NULL,
    entry_price REAL NOT NULL,
    exit_price REAL NOT NULL,
    quantity REAL NOT NULL,
    signal_type TEXT,
    signal_strength REAL,
    outcome_pct REAL NOT NULL,
    outcome_usd REAL NOT NULL,
    holding_hours REAL,
    exit_reason TEXT,
    market_regime TEXT,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

CREATE_INDEX_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_trade_outcomes_symbol ON trade_outcomes(symbol);",
    "CREATE INDEX IF NOT EXISTS idx_trade_outcomes_signal ON trade_outcomes(signal_type);",
    "CREATE INDEX IF NOT EXISTS idx_trade_outcomes_exit_date ON trade_outcomes(exit_date);",
]


class TradeJournal:
    """Records and analyzes completed trade outcomes."""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._ensure_schema()

    def _ensure_schema(self):
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(CREATE_TABLE_SQL)
        for idx_sql in CREATE_INDEX_SQL:
            cur.execute(idx_sql)
        conn.commit()
        conn.close()

    def record_outcome(
        self,
        symbol: str,
        entry_date: str,
        exit_date: str,
        entry_price: float,
        exit_price: float,
        quantity: float,
        signal_type: str = None,
        signal_strength: float = None,
        exit_reason: str = None,
        market_regime: str = None,
        notes: str = None,
    ) -> int:
        """Record a completed trade outcome. Returns the trade_id."""
        outcome_pct = ((exit_price - entry_price) / entry_price) * 100 if entry_price else 0
        outcome_usd = (exit_price - entry_price) * quantity

        # Calculate holding period in hours
        holding_hours = None
        try:
            entry_dt = datetime.fromisoformat(entry_date)
            exit_dt = datetime.fromisoformat(exit_date)
            holding_hours = (exit_dt - entry_dt).total_seconds() / 3600
        except (ValueError, TypeError):
            pass

        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO trade_outcomes
            (symbol, entry_date, exit_date, entry_price, exit_price, quantity,
             signal_type, signal_strength, outcome_pct, outcome_usd,
             holding_hours, exit_reason, market_regime, notes)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                symbol, entry_date, exit_date, entry_price, exit_price, quantity,
                signal_type, signal_strength, round(outcome_pct, 4), round(outcome_usd, 6),
                round(holding_hours, 2) if holding_hours else None,
                exit_reason, market_regime, notes,
            ),
        )
        trade_id = cur.lastrowid
        conn.commit()
        conn.close()
        logger.info(
            f"Journal: {symbol} {outcome_pct:+.2f}% (${outcome_usd:+.4f}) "
            f"signal={signal_type} exit={exit_reason}"
        )

        # Embed rich trade context for semantic retrieval
        self._embed_outcome(trade_id, symbol, entry_price, exit_price,
                            outcome_pct, signal_type, signal_strength,
                            exit_reason, market_regime, holding_hours)

        return trade_id

    def _embed_outcome(self, trade_id, symbol, entry_price, exit_price,
                       outcome_pct, signal_type, signal_strength,
                       exit_reason, market_regime, holding_hours):
        """Embed a rich text description of the trade for semantic retrieval."""
        try:
            from vector_store import VectorStore, embed_text

            # Build rich context string
            parts = [
                f"Closed {symbol} position.",
                f"Entry ${entry_price:.4f}, exit ${exit_price:.4f}.",
                f"Outcome: {outcome_pct:+.2f}%.",
            ]
            if signal_type:
                parts.append(f"Signal: {signal_type}.")
            if signal_strength:
                parts.append(f"Signal strength: {signal_strength:.0f}.")
            if exit_reason:
                parts.append(f"Exit reason: {exit_reason}.")
            if market_regime:
                parts.append(f"Market regime: {market_regime}.")
            if holding_hours:
                if holding_hours < 24:
                    parts.append(f"Held for {holding_hours:.1f} hours.")
                else:
                    parts.append(f"Held for {holding_hours/24:.1f} days.")
            if outcome_pct > 5:
                parts.append("Strong winner.")
            elif outcome_pct > 0:
                parts.append("Small gain.")
            elif outcome_pct > -5:
                parts.append("Small loss.")
            else:
                parts.append("Significant loss.")

            text = " ".join(parts)
            embedding = embed_text(text)

            vs = VectorStore(self.db_path)
            vs.ensure_collection("trade_outcomes_vec")
            vs.add("trade_outcomes_vec", trade_id, embedding)
            logger.debug(f"Embedded trade outcome {trade_id}")
        except Exception as e:
            logger.debug(f"Trade outcome embedding failed (non-critical): {e}")

    # ── Analytics ─────────────────────────────────────────────────────────

    def get_performance_by_signal_type(self) -> List[Dict]:
        """Win rate, avg return, and count grouped by signal_type."""
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("""
            SELECT
                COALESCE(signal_type, 'unknown') as signal_type,
                COUNT(*) as total_trades,
                SUM(CASE WHEN outcome_pct > 0 THEN 1 ELSE 0 END) as wins,
                ROUND(AVG(outcome_pct), 2) as avg_return_pct,
                ROUND(SUM(outcome_usd), 4) as total_pnl_usd,
                ROUND(AVG(holding_hours), 1) as avg_holding_hours,
                ROUND(AVG(signal_strength), 1) as avg_strength
            FROM trade_outcomes
            GROUP BY signal_type
            ORDER BY avg_return_pct DESC
        """)
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        conn.close()
        for r in rows:
            r["win_rate"] = round(r["wins"] / r["total_trades"] * 100, 1) if r["total_trades"] else 0
        return rows

    def get_performance_by_symbol(self) -> List[Dict]:
        """Performance grouped by symbol."""
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("""
            SELECT
                symbol,
                COUNT(*) as total_trades,
                SUM(CASE WHEN outcome_pct > 0 THEN 1 ELSE 0 END) as wins,
                ROUND(AVG(outcome_pct), 2) as avg_return_pct,
                ROUND(SUM(outcome_usd), 4) as total_pnl_usd
            FROM trade_outcomes
            GROUP BY symbol
            ORDER BY total_pnl_usd DESC
        """)
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        conn.close()
        for r in rows:
            r["win_rate"] = round(r["wins"] / r["total_trades"] * 100, 1) if r["total_trades"] else 0
        return rows

    def get_performance_by_regime(self) -> List[Dict]:
        """Performance grouped by market regime."""
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("""
            SELECT
                COALESCE(market_regime, 'unknown') as regime,
                COUNT(*) as total_trades,
                SUM(CASE WHEN outcome_pct > 0 THEN 1 ELSE 0 END) as wins,
                ROUND(AVG(outcome_pct), 2) as avg_return_pct,
                ROUND(SUM(outcome_usd), 4) as total_pnl_usd
            FROM trade_outcomes
            GROUP BY market_regime
            ORDER BY avg_return_pct DESC
        """)
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        conn.close()
        for r in rows:
            r["win_rate"] = round(r["wins"] / r["total_trades"] * 100, 1) if r["total_trades"] else 0
        return rows

    def get_recent_outcomes(self, limit: int = 20) -> List[Dict]:
        """Get most recent trade outcomes."""
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(
            """SELECT * FROM trade_outcomes ORDER BY exit_date DESC LIMIT ?""",
            (limit,),
        )
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        conn.close()
        return rows

    def get_signal_edge(self) -> Dict:
        """
        Calculate which signal types have a statistical edge.
        Returns signals ranked by expected value (win_rate * avg_win - loss_rate * avg_loss).
        """
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("""
            SELECT
                COALESCE(signal_type, 'unknown') as signal_type,
                COUNT(*) as n,
                AVG(CASE WHEN outcome_pct > 0 THEN outcome_pct END) as avg_win,
                AVG(CASE WHEN outcome_pct <= 0 THEN outcome_pct END) as avg_loss,
                SUM(CASE WHEN outcome_pct > 0 THEN 1.0 ELSE 0.0 END) / COUNT(*) as win_rate
            FROM trade_outcomes
            GROUP BY signal_type
            HAVING n >= 5
        """)
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        conn.close()

        for r in rows:
            avg_win = r["avg_win"] or 0
            avg_loss = abs(r["avg_loss"] or 0)
            wr = r["win_rate"] or 0
            r["expected_value"] = round(wr * avg_win - (1 - wr) * avg_loss, 2)
            r["avg_win"] = round(avg_win, 2)
            r["avg_loss"] = round(avg_loss, 2)
            r["win_rate"] = round(wr * 100, 1)

        rows.sort(key=lambda x: x["expected_value"], reverse=True)
        return rows

    def find_similar_trades(self, symbol: str, signal_type: str = None,
                            market_regime: str = None, k: int = 5,
                            current_price: float = None,
                            signal_strength: float = None,
                            volatility: str = None,
                            action: str = None) -> List[Dict]:
        """
        Find past trades with similar context using semantic search.
        Useful for answering: "What happened last time we got this signal?"

        Richer queries produce better vector matches against the embedded
        trade outcome descriptions (which include price, outcome %, exit
        reason, regime, holding period, and win/loss label).
        """
        try:
            from vector_store import VectorStore, embed_text

            # Build a rich query that mirrors the embedded trade descriptions
            parts = [f"Trading {symbol}."]
            if action:
                parts.append(f"Considering {action} position.")
            if signal_type:
                parts.append(f"Signal: {signal_type}.")
            if signal_strength is not None:
                parts.append(f"Signal strength: {signal_strength:.0f}.")
            if current_price is not None:
                parts.append(f"Current price ${current_price:.4f}.")
            if market_regime:
                parts.append(f"Market regime: {market_regime}.")
            if volatility:
                parts.append(f"Volatility: {volatility}.")

            query_emb = embed_text(" ".join(parts))
            vs = VectorStore(self.db_path)
            vs.ensure_collection("trade_outcomes_vec")

            # KNN search in vec table
            results = vs.search("trade_outcomes_vec", query_emb, k=k * 2)

            if not results:
                return []

            # Enrich with full trade_outcomes data
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()
            trade_ids = [r["id"] for r in results]
            placeholders = ",".join(["?"] * len(trade_ids))
            cur.execute(
                f"SELECT * FROM trade_outcomes WHERE trade_id IN ({placeholders})",
                trade_ids,
            )
            cols = [d[0] for d in cur.description]
            trades = {row[0]: dict(zip(cols, row)) for row in cur.fetchall()}
            conn.close()

            enriched = []
            for r in results:
                tid = r["id"]
                if tid in trades:
                    t = trades[tid]
                    t["distance"] = r.get("distance", 999)
                    t["similarity"] = r.get("similarity", 0)
                    enriched.append(t)
                    if len(enriched) >= k:
                        break

            return enriched
        except Exception as e:
            logger.warning(f"Similar trade search failed: {e}")
            return []

    def format_journal_report(self) -> str:
        """Format a readable journal report for Telegram or logging."""
        lines = ["**Trade Journal Summary**", ""]

        by_signal = self.get_performance_by_signal_type()
        if by_signal:
            lines.append("**By Signal Type:**")
            for r in by_signal:
                lines.append(
                    f"  {r['signal_type']}: {r['total_trades']} trades, "
                    f"{r['win_rate']}% win rate, avg {r['avg_return_pct']:+.2f}%, "
                    f"PnL ${r['total_pnl_usd']:+.4f}"
                )
            lines.append("")

        by_regime = self.get_performance_by_regime()
        if by_regime:
            lines.append("**By Market Regime:**")
            for r in by_regime:
                lines.append(
                    f"  {r['regime']}: {r['total_trades']} trades, "
                    f"{r['win_rate']}% win rate, avg {r['avg_return_pct']:+.2f}%"
                )
            lines.append("")

        recent = self.get_recent_outcomes(10)
        if recent:
            lines.append("**Recent Trades:**")
            for r in recent:
                lines.append(
                    f"  {r['exit_date'][:10]} {r['symbol']}: "
                    f"{r['outcome_pct']:+.2f}% ({r['exit_reason'] or '?'})"
                )
            lines.append("")

        if not by_signal and not recent:
            lines.append("No completed trades recorded yet.")

        return "\n".join(lines)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Trade Journal")
    parser.add_argument("--report", action="store_true", help="Print journal report")
    parser.add_argument("--edge", action="store_true", help="Show signal edge analysis")
    args = parser.parse_args()

    journal = TradeJournal()

    if args.edge:
        edges = journal.get_signal_edge()
        if edges:
            print("Signal Edge Analysis (min 5 trades):")
            for e in edges:
                print(f"  {e['signal_type']}: EV={e['expected_value']:+.2f}%, "
                      f"WR={e['win_rate']}%, avg_win={e['avg_win']}%, avg_loss={e['avg_loss']}%")
        else:
            print("Not enough data yet (need 5+ trades per signal type)")
    elif args.report:
        print(journal.format_journal_report())
    else:
        parser.print_help()
