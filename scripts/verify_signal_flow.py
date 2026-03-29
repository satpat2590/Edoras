#!/usr/bin/env python3
"""
Verify Signal Flow — traces every symbol's signal path through the execution pipeline.

Shows exactly why each symbol's signals are or aren't executing right now.

Usage:
  python3 scripts/verify_signal_flow.py                # All symbols
  python3 scripts/verify_signal_flow.py BTC-USD        # Single symbol
  python3 scripts/verify_signal_flow.py --recent       # Show recent skip reasons
"""

import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DB_PATH, get_asset_class_profile, get_active_portfolios


def get_portfolio_state():
    """Load portfolio state from disk."""
    state_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "paper_portfolio_full_state.json")
    try:
        with open(state_file) as f:
            return json.load(f)
    except Exception:
        return {"positions": {}, "capital": 0}


def trace_symbol(symbol: str, routes: dict, positions: dict, capital: float,
                 portfolio_value: float, conn: sqlite3.Connection):
    """Trace a single symbol through all execution gates."""
    print(f"\n{'─'*60}")
    print(f"  {symbol}")
    print(f"{'─'*60}")

    # 1. Strategy routing
    route = routes.get(symbol)
    if route:
        strategy = route.get("strategy", "?")
        timeframe = route.get("timeframe", "?")
        weight = route.get("weight")
        params = route.get("params", {})
        print(f"  Route:     {strategy} / {timeframe}")
        if weight is not None:
            print(f"  Weight:    {weight}")
            if weight == 0.0:
                print(f"  ⚠️  Weight is 0.0 — may cause zero-sized trades")
        if params.get("hmm_available") is False:
            print(f"  ⚠️  HMM unavailable — using heuristic fallback")
    else:
        print(f"  Route:     NONE (falls back to legacy signals)")

    # 2. Position check
    held = symbol in positions
    if held:
        pos = positions[symbol]
        qty = pos.get("quantity", 0)
        avg_price = pos.get("avg_price", 0)
        # Get current price
        row = conn.execute(
            "SELECT close FROM candlesticks WHERE symbol=? AND timeframe='1h' "
            "ORDER BY timestamp DESC LIMIT 1", (symbol,)
        ).fetchone()
        current_price = float(row[0]) if row else 0
        pos_value = current_price * qty
        pos_pct = pos_value / portfolio_value if portfolio_value > 0 else 0
        pnl_pct = ((current_price - avg_price) / avg_price * 100) if avg_price > 0 else 0
        print(f"  Position:  HELD (qty={qty:.6g}, value=${pos_value:.2f}, "
              f"alloc={pos_pct:.1%}, P&L={pnl_pct:+.1f}%)")
    else:
        print(f"  Position:  NOT HELD")

    # 3. Profile / limits
    prof = get_asset_class_profile(symbol) if get_asset_class_profile else {}
    max_pct = prof.get("max_position_pct", 0.25)
    min_trade = prof.get("min_trade_usd", 10.0)
    min_hold = prof.get("min_hold_hours", 12)
    print(f"  Limits:    max_pos={max_pct:.0%}, min_trade=${min_trade:.0f}, min_hold={min_hold}h")

    # 4. Cash check
    cash_reserve = portfolio_value * 0.05
    available_cash = capital * 0.95
    print(f"  Cash:      ${capital:.2f} available (${available_cash:.2f} after 5% reserve)")

    # 5. Gate analysis for BUY
    print(f"\n  BUY gates:")
    blocked = False

    if held:
        pos_value = positions[symbol]["quantity"] * (current_price if 'current_price' in dir() else 0)
        pos_pct = pos_value / portfolio_value if portfolio_value > 0 else 0
        if pos_pct >= max_pct * 0.9:
            print(f"    ❌ position_held: alloc {pos_pct:.1%} ≥ {max_pct*0.9:.1%} (90% of max)")
            print(f"       → Blocked even for high-conviction signals")
            blocked = True
        else:
            print(f"    ⚡ position_held: alloc {pos_pct:.1%} < {max_pct*0.9:.1%}")
            print(f"       → Would allow add for strength ≥ 80")
    else:
        print(f"    ✅ No position — BUY allowed")

    # Sizing check for strength 50 (minimum)
    alloc_50 = 0.03
    buy_50 = portfolio_value * alloc_50
    buy_50 = min(buy_50, available_cash)
    if buy_50 < min_trade:
        print(f"    ❌ insufficient_cash: str=50 → ${buy_50:.2f} < min ${min_trade:.0f}")
        blocked = True
    else:
        print(f"    ✅ Cash OK: str=50 → ${buy_50:.2f}, str=80 → ${min(portfolio_value * 0.10, available_cash):.2f}")

    # 6. Gate analysis for SELL
    print(f"\n  SELL gates:")
    if not held:
        print(f"    ❌ no_position_to_sell")
    else:
        entry_date_str = None
        # Try to get entry date from state
        state = get_portfolio_state()
        entry_prices = state.get("entry_prices", {})
        entry_date_str = entry_prices.get(f"{symbol}_date")
        if entry_date_str:
            try:
                entry_dt = datetime.fromisoformat(entry_date_str)
                held_hours = (datetime.now() - entry_dt).total_seconds() / 3600
                if held_hours < min_hold:
                    print(f"    ❌ min_hold_period: held {held_hours:.1f}h < {min_hold}h")
                else:
                    print(f"    ✅ Hold time OK: {held_hours:.1f}h ≥ {min_hold}h")
            except Exception:
                print(f"    ✅ Hold time: unknown entry date")
        else:
            print(f"    ✅ Hold time: no entry date recorded")

    # 7. Recent signals
    print(f"\n  Recent signals (7d):")
    rows = conn.execute(
        "SELECT action, strength, was_executed, signal_time, strategy_name, skip_reason "
        "FROM strategy_signals_log WHERE symbol=? AND signal_time > ? "
        "ORDER BY signal_time DESC LIMIT 5",
        (symbol, (datetime.now() - timedelta(days=7)).isoformat())
    ).fetchall()
    if rows:
        for r in rows:
            status = "✅ EXEC" if r[2] else "❌ SKIP"
            skip = f" ({r[5]})" if r[5] else ""
            print(f"    {r[4]:20s} {r[0]:4s} str={r[1]:5.1f} {status}{skip}  {r[3][:16]}")
    else:
        print(f"    (none)")

    # Verdict
    print()
    if blocked:
        print(f"  VERDICT: ❌ BLOCKED")
    else:
        print(f"  VERDICT: ✅ WOULD EXECUTE (if signal fires)")


def show_recent_skips(conn: sqlite3.Connection, limit: int = 30):
    """Show recent skip reasons across all symbols."""
    print(f"\n{'='*70}")
    print(f"  Recent Signal Skip Reasons (last 7 days)")
    print(f"{'='*70}")

    rows = conn.execute(
        "SELECT strategy_name, symbol, action, strength, skip_reason, signal_time "
        "FROM strategy_signals_log "
        "WHERE signal_time > ? AND was_executed = 0 AND skip_reason IS NOT NULL "
        "ORDER BY signal_time DESC LIMIT ?",
        ((datetime.now() - timedelta(days=7)).isoformat(), limit)
    ).fetchall()

    if not rows:
        print("  No skip reasons recorded yet (logging was just added).")
        print("  Run the signal engine once and check again.")
        return

    print(f"  {'Strategy':<20s} {'Symbol':<12s} {'Act':>4s} {'Str':>5s} {'Skip Reason':<30s} {'Time':<16s}")
    print(f"  {'-'*20} {'-'*12} {'-'*4} {'-'*5} {'-'*30} {'-'*16}")
    for r in rows:
        print(f"  {r[0]:<20s} {r[1]:<12s} {r[2]:>4s} {r[3]:5.1f} {r[4]:<30s} {r[5][:16]}")

    # Summary
    print(f"\n  Skip reason summary:")
    summary = conn.execute(
        "SELECT skip_reason, COUNT(*) FROM strategy_signals_log "
        "WHERE signal_time > ? AND was_executed = 0 AND skip_reason IS NOT NULL "
        "GROUP BY skip_reason ORDER BY COUNT(*) DESC",
        ((datetime.now() - timedelta(days=7)).isoformat(),)
    ).fetchall()
    for r in summary:
        print(f"    {r[0]:<35s} {r[1]:>4d} signals")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Verify signal flow through execution pipeline")
    parser.add_argument("symbol", nargs="?", help="Trace a specific symbol")
    parser.add_argument("--recent", action="store_true", help="Show recent skip reasons")
    parser.add_argument("--portfolio", type=int, default=1, help="Portfolio ID")
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Load portfolio config
    portfolios = get_active_portfolios()
    pf = next((p for p in portfolios if p["id"] == args.portfolio), None)
    if not pf:
        print(f"Portfolio {args.portfolio} not found")
        sys.exit(1)

    symbols = pf["symbols"]
    routes = pf["strategy_routes"]

    # Load portfolio state
    state = get_portfolio_state()
    positions = state.get("positions", {})
    capital = state.get("capital", 0)
    portfolio_value = capital + sum(
        (conn.execute(
            "SELECT close FROM candlesticks WHERE symbol=? AND timeframe='1h' "
            "ORDER BY timestamp DESC LIMIT 1", (sym,)
        ).fetchone() or [0])[0] * pos.get("quantity", 0)
        for sym, pos in positions.items()
    )

    print(f"{'='*60}")
    print(f"  Signal Flow Trace — {pf['name']} ({pf['mode']})")
    print(f"  Portfolio: ${portfolio_value:.2f} | Cash: ${capital:.2f} | "
          f"Positions: {len(positions)}")
    print(f"  Symbols: {len(symbols)} routed | "
          f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}")

    # Check risk state
    risk_state_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "risk_state.json")
    try:
        with open(risk_state_file) as f:
            risk = json.load(f)
        if risk.get("circuit_breaker_active"):
            print(f"\n  🚨 CIRCUIT BREAKER ACTIVE — all buys suppressed!")
        else:
            print(f"\n  ✅ Circuit breaker: OFF")
    except Exception:
        print(f"\n  ⚠️  Could not read risk state")

    if args.recent:
        show_recent_skips(conn)
    elif args.symbol:
        trace_symbol(args.symbol, routes, positions, capital, portfolio_value, conn)
    else:
        for sym in sorted(symbols):
            trace_symbol(sym, routes, positions, capital, portfolio_value, conn)

    conn.close()


if __name__ == "__main__":
    main()
