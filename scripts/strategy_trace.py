#!/usr/bin/env python3
"""
Strategy Trace — shows why each routed strategy is silent or firing.

Loads the same indicator window and portfolio context that signal_trading.py uses,
runs each strategy's generate_signals(), and reports exactly what happened.

Usage:
  python3 scripts/strategy_trace.py                # All routed symbols
  python3 scripts/strategy_trace.py BTC-USD        # Single symbol
"""

import json
import logging
import os
import sqlite3
import sys
from datetime import datetime

# Set up path so we can import project modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from config import DB_PATH

# Enable strategy-level logging at INFO
logging.basicConfig(level=logging.INFO, format="%(message)s")
# Suppress non-strategy noise
for mod in ["hmmlearn", "urllib3", "coinbase"]:
    logging.getLogger(mod).setLevel(logging.ERROR)

logger = logging.getLogger(__name__)


def load_routes():
    """Load strategy routing from the Galadriel portfolio."""
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT strategy_routes_json FROM portfolios WHERE id=1"
    ).fetchone()
    conn.close()
    if not row or not row[0]:
        return {}
    return json.loads(row[0])


def get_indicator_window(symbol, timeframe, lookback=120):
    """Load the indicator window — same as signal_trading.get_indicator_window."""
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(
        "SELECT c.timestamp, c.open, c.high, c.low, c.close, c.volume, "
        "i.rsi_14, i.macd_line, i.macd_signal, i.macd_histogram, "
        "i.sma_20, i.sma_50, i.sma_200, i.ema_12, i.ema_26, "
        "i.bb_upper, i.bb_middle, i.bb_lower, i.bb_width, "
        "i.atr_14, i.volume_sma_20, i.volume_ratio, i.adx_14 "
        "FROM candlesticks c "
        "JOIN indicators i ON c.symbol=i.symbol AND c.timeframe=i.timeframe "
        "AND c.timestamp=i.timestamp "
        "WHERE c.symbol=? AND c.timeframe=? "
        "ORDER BY c.timestamp DESC LIMIT ?",
        conn,
        params=(symbol, timeframe, lookback),
    )
    conn.close()
    if df.empty or len(df) < 3:
        return None
    return df.iloc[::-1].reset_index(drop=True)


def get_portfolio_positions():
    """Get current open positions from paper_portfolio state."""
    state_file = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "paper_portfolio_full_state.json"
    )
    try:
        with open(state_file) as f:
            state = json.load(f)
        return state.get("positions", {})
    except FileNotFoundError:
        return {}


def trace_symbol(symbol, route_cfg):
    """Trace a single symbol through its strategy."""
    strategy_name = route_cfg["strategy"]
    timeframe = route_cfg["timeframe"]
    params = route_cfg.get("params", {})

    print(f"\n{'='*70}")
    print(f"  {symbol} → {strategy_name} ({timeframe})")
    print(f"{'='*70}")

    # Load strategy class
    from backtest.strategies import STRATEGY_REGISTRY
    cls = STRATEGY_REGISTRY.get(strategy_name)
    if cls is None:
        print(f"  ERROR: Strategy '{strategy_name}' not found in registry")
        return None

    # Instantiate
    try:
        instance = cls(**params) if params else cls()
    except TypeError:
        instance = cls()
        print(f"  NOTE: Params {params} rejected, using defaults")

    # Load data
    df = get_indicator_window(symbol, timeframe)
    if df is None:
        print(f"  ERROR: No indicator data for {symbol}/{timeframe}")
        return None

    # Show latest indicators
    curr = df.iloc[-1]
    price = curr.get("close", 0)
    adx = curr.get("adx_14")
    rsi = curr.get("rsi_14")
    bb_upper = curr.get("bb_upper")
    bb_lower = curr.get("bb_lower")
    macd_h = curr.get("macd_histogram")
    vol_ratio = curr.get("volume_ratio")
    sma_20 = curr.get("sma_20")
    sma_50 = curr.get("sma_50")

    bb_pos = None
    if bb_upper and bb_lower and not pd.isna(bb_upper) and not pd.isna(bb_lower):
        bb_range = bb_upper - bb_lower
        if bb_range > 0:
            bb_pos = (price - bb_lower) / bb_range

    ts = int(curr.get("timestamp", 0))
    age_hrs = (datetime.now().timestamp() - ts) / 3600

    print(f"\n  Latest bar: {datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M')} ({age_hrs:.1f}h ago)")
    print(f"  Price:      ${price:,.2f}")
    print(f"  ADX:        {adx:.1f}" + (" (trending)" if adx and adx > 25 else " (ranging)"))
    print(f"  RSI:        {rsi:.1f}" + (" (oversold)" if rsi and rsi < 35 else " (overbought)" if rsi and rsi > 65 else ""))
    if bb_pos is not None:
        print(f"  BB pos:     {bb_pos:.3f}" + (" (near lower)" if bb_pos < 0.1 else " (near upper)" if bb_pos > 0.9 else " (neutral)"))
    else:
        print(f"  BB pos:     N/A (missing BB data)")
    print(f"  MACD hist:  {macd_h:.6f}" if macd_h else "  MACD hist:  N/A")
    print(f"  Vol ratio:  {vol_ratio:.2f}" if vol_ratio else "  Vol ratio:  N/A")
    if sma_20 and sma_50:
        trend = "up" if price > sma_20 > sma_50 else "down" if price < sma_20 < sma_50 else "mixed"
        print(f"  SMA trend:  {trend} (price {'>' if price > sma_20 else '<'} SMA20 {'>' if sma_20 > sma_50 else '<'} SMA50)")

    # Get position
    positions = get_portfolio_positions()
    pos_qty = 0
    if symbol in positions:
        pos_qty = positions[symbol].get("quantity", 0)
    print(f"  Position:   {'HELD (qty=' + f'{pos_qty:.6g}' + ')' if pos_qty > 0 else 'NONE'}")

    # Build portfolio context (same as signal_trading.run_backtested_strategy)
    portfolio_ctx = {
        "capital": 1000.0,
        "position_qty": pos_qty,
        "entry_price": 0,
        "symbol": symbol,
    }

    # Run strategy (logging will print the reason)
    print(f"\n  Running {strategy_name}.generate_signals()...")
    signals = instance.generate_signals(df, portfolio_ctx)

    if signals:
        sig = signals[0]
        strength = sig["weight"] * 100
        print(f"\n  >>> SIGNAL: {sig['action']} strength={strength:.0f}")
        print(f"      Reason: {sig['reason']}")
    else:
        print(f"\n  >>> SILENT (see strategy log above)")

    return {
        "symbol": symbol,
        "strategy": strategy_name,
        "adx": adx,
        "rsi": rsi,
        "bb_pos": bb_pos,
        "result": f"{signals[0]['action']} str={signals[0]['weight']*100:.0f}" if signals else "SILENT",
    }


def main():
    target_symbol = None
    if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
        target_symbol = sys.argv[1].upper()
        if not target_symbol.endswith("-USD"):
            target_symbol += "-USD"

    routes = load_routes()
    if not routes:
        print("No strategy routes configured for Galadriel")
        return

    results = []
    for symbol, cfg in sorted(routes.items()):
        if target_symbol and symbol != target_symbol:
            continue
        result = trace_symbol(symbol, cfg)
        if result:
            results.append(result)

    if len(results) > 1:
        # Summary table
        print(f"\n{'='*70}")
        print("  SUMMARY")
        print(f"{'='*70}")
        print(f"  {'Symbol':<12} {'Strategy':<20} {'ADX':>5} {'RSI':>5} {'BB%':>6}  Result")
        print(f"  {'-'*12} {'-'*20} {'-'*5} {'-'*5} {'-'*6}  {'-'*25}")
        for r in results:
            adx_str = f"{r['adx']:.1f}" if r['adx'] else "N/A"
            rsi_str = f"{r['rsi']:.1f}" if r['rsi'] else "N/A"
            bb_str = f"{r['bb_pos']:.2f}" if r['bb_pos'] is not None else "N/A"
            print(f"  {r['symbol']:<12} {r['strategy']:<20} {adx_str:>5} {rsi_str:>5} {bb_str:>6}  {r['result']}")

    firing = [r for r in results if r["result"] != "SILENT"]
    silent = [r for r in results if r["result"] == "SILENT"]
    print(f"\n  {len(firing)} firing, {len(silent)} silent out of {len(results)} routed symbols")

    # ── Exit Overlay Diagnostics ──
    trace_exit_overlay(routes, target_symbol)


def trace_exit_overlay(routes, target_symbol=None):
    """Show exit overlay analysis for all held positions."""
    positions = get_portfolio_positions()
    held = {sym: pos for sym, pos in positions.items()
            if pos.get("quantity", 0) > 0}

    if not held:
        return

    try:
        from exit_overlay import ExitOverlay
    except ImportError:
        print("\n  Exit overlay not available (import failed)")
        return

    eo = ExitOverlay(db_path=DB_PATH)

    # If tracing a single symbol, only show that one if held
    if target_symbol:
        if target_symbol not in held:
            return
        held = {target_symbol: held[target_symbol]}

    print(f"\n{'='*70}")
    print("  EXIT OVERLAY — Held Positions")
    print(f"{'='*70}")

    exit_results = []
    for symbol, pos_data in sorted(held.items()):
        position = eo._get_position_entry(symbol) or {}
        if not position.get("entry_price") and pos_data.get("avg_price"):
            position["entry_price"] = pos_data["avg_price"]

        df = eo._get_indicator_window(symbol, "4h", lookback=60)

        # Calculate live P&L if not in DB
        if "pnl_percent" not in position and df is not None and len(df) > 0:
            current_price = df.iloc[-1]["close"]
            entry_price = position.get("entry_price", 0)
            if entry_price > 0:
                position["pnl_percent"] = eo._pnl_from_price(entry_price, current_price)

        hours = eo._hours_held(position.get("entry_time", ""))
        pnl = position.get("pnl_percent", 0) or 0
        pnl_frac = pnl / 100.0 if abs(pnl) > 1 else pnl

        routed_strat = routes.get(symbol, {}).get("strategy")
        print(f"\n  {symbol} (held {hours/24:.1f}d, P&L: {pnl_frac:+.1%})"
              + (f" [routed: {routed_strat}]" if routed_strat else " [UNROUTED]"))

        checks = {}
        best = None

        if df is not None:
            sig = eo.check_momentum_exit(symbol, df, position)
            if sig:
                checks["Momentum"] = f"SELL (strength {sig['strength']:.0f})"
                if best is None or sig["strength"] > best["strength"]:
                    best = sig
            else:
                close = df["close"]
                if len(close) > 21:
                    mom = (close.iloc[-1] / close.iloc[-21]) - 1
                    checks["Momentum"] = f"HOLD ({21}d return={mom:+.2%})"
                else:
                    checks["Momentum"] = "HOLD (insufficient data)"

            sig = eo.check_trend_break_exit(symbol, df, position)
            if sig:
                checks["Trend break"] = f"SELL (strength {sig['strength']:.0f})"
                if best is None or sig["strength"] > best["strength"]:
                    best = sig
            else:
                bar = df.iloc[-1]
                parts = []
                p, s20, s50 = bar.get("close"), bar.get("sma_20"), bar.get("sma_50")
                if p and s20 and p >= s20:
                    parts.append("price>=SMA20")
                if s20 and s50 and s20 >= s50:
                    parts.append("SMA20>=SMA50")
                adx = bar.get("adx_14")
                if adx and adx <= 20:
                    parts.append(f"ADX={adx:.0f}<=20")
                checks["Trend break"] = f"HOLD ({', '.join(parts) if parts else 'conditions not met'})"

            sig = eo.check_volatility_exit(symbol, df, position)
            if sig:
                checks["Volatility"] = f"SELL (strength {sig['strength']:.0f})"
                if best is None or sig["strength"] > best["strength"]:
                    best = sig
            else:
                checks["Volatility"] = "HOLD (ATR below threshold)"

            sig = eo.check_correlation_exit(symbol, df, position, held)
            if sig:
                checks["Correlation"] = f"SELL (strength {sig['strength']:.0f})"
                if best is None or sig["strength"] > best["strength"]:
                    best = sig
            else:
                checks["Correlation"] = "HOLD"

        sig = eo.check_time_deterioration_exit(symbol, position)
        if sig:
            checks["Time decay"] = f"SELL (strength {sig['strength']:.0f})"
            if best is None or sig["strength"] > best["strength"]:
                best = sig
        else:
            max_d = eo.config["max_hold_days"]
            checks["Time decay"] = f"HOLD (held {hours/24:.1f}d < {max_d}d max)"

        for check_name, result_str in checks.items():
            marker = ">>>" if "SELL" in result_str else "   "
            print(f"    {marker} {check_name + ':':<16} {result_str}")

        if best:
            print(f"    === STRONGEST: {best['exit_type']} (strength {best['strength']:.0f})")
            exit_results.append((symbol, pnl_frac, best["exit_type"], best["strength"]))
        else:
            exit_results.append((symbol, pnl_frac, "HOLD", None))

    # Summary table
    if len(exit_results) > 1:
        print(f"\n  Exit Overlay Summary:")
        print(f"  {'Symbol':<12} {'P&L%':>7}  {'Exit Condition':<22} {'Strength':>8}")
        print(f"  {'-'*12} {'-'*7}  {'-'*22} {'-'*8}")
        for sym, pnl, cond, strength in exit_results:
            s_str = str(int(strength)) if strength else "-"
            print(f"  {sym:<12} {pnl:>+6.1%}  {cond:<22} {s_str:>8}")


if __name__ == "__main__":
    main()
