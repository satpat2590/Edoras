#!/usr/bin/env python3
"""
Edoras CLI — Unified query interface for the trading system.

Usage:
    python3 cli.py snapshot             # Current portfolio + positions
    python3 cli.py trades [--hours 24]  # Recent trades
    python3 cli.py signals [--hours 24] # Recent strategy signals
    python3 cli.py outcomes [--days 7]  # Closed trade outcomes
    python3 cli.py pnl [--days 30]      # Daily P&L series
    python3 cli.py indicators SYMBOL    # Latest indicators for a symbol
    python3 cli.py health               # System health (timers, data freshness)
"""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import get_account_ids, DB_PATH as _DB_PATH_STR

DB_PATH = Path(_DB_PATH_STR)


def get_conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _account_filter(table_alias: str, portfolio_id: int) -> tuple:
    """Return (SQL fragment, params) for filtering by portfolio's accounts.

    Phase 3: queries go through accounts bridge table.
    Falls back to portfolio_id if no accounts found.
    """
    ids = get_account_ids(portfolio_id, db_path=str(DB_PATH))
    if ids:
        placeholders = ','.join('?' * len(ids))
        return f"{table_alias}.account_id IN ({placeholders})", ids
    return f"{table_alias}.portfolio_id = ?", [portfolio_id]


# ── snapshot ─────────────────────────────────────────────────────────────────

def cmd_snapshot(args):
    conn = get_conn()
    c = conn.cursor()

    # Portfolio metadata
    c.execute("SELECT id, name, mode, initial_capital FROM portfolios WHERE is_active = 1")
    portfolios = c.fetchall()

    for pf in portfolios:
        pid = pf["id"]
        print(f"\n{'=' * 70}")
        print(f"  {pf['name']} (id={pid}, mode={pf['mode']}, initial=${pf['initial_capital']:.0f})")
        print(f"{'=' * 70}")

        # Positions (Phase 3: query via accounts)
        filt, params = _account_filter("p", pid)
        c.execute(f"""SELECT p.symbol, p.quantity, p.entry_price, p.current_price,
                            p.pnl, p.pnl_percent, p.entry_time
                     FROM positions p WHERE {filt} AND p.status = 'open'
                     ORDER BY ABS(p.pnl) DESC""", params)
        positions = c.fetchall()

        total_invested = 0
        total_pnl = 0
        if positions:
            print(f"  {'Symbol':<12} {'Qty':>12} {'Entry':>10} {'Current':>10} {'Value':>12} {'P&L':>10} {'%':>8} {'Held'}")
            print(f"  {'-'*12} {'-'*12} {'-'*10} {'-'*10} {'-'*12} {'-'*10} {'-'*8} {'-'*10}")
            for p in positions:
                value = p["quantity"] * (p["current_price"] or p["entry_price"])
                total_invested += value
                total_pnl += p["pnl"] or 0
                held = _held_str(p["entry_time"])
                print(f"  {p['symbol']:<12} {p['quantity']:>12.6g} "
                      f"${p['entry_price']:>9.6g} ${p['current_price'] or 0:>9.6g} "
                      f"${value:>11.2f} "
                      f"${p['pnl']:>+9.2f} {p['pnl_percent']:>+7.2f}% {held}")
        else:
            print("  (no open positions)")

        # Cash (Phase 3: query via accounts)
        filt_t, params_t = _account_filter("t", pid)
        c.execute(f"SELECT t.cash_after FROM trades t WHERE {filt_t} ORDER BY t.id DESC LIMIT 1", params_t)
        row = c.fetchone()
        cash = row["cash_after"] if row else pf["initial_capital"]

        total_value = cash + total_invested
        if pf["initial_capital"] > 0:
            total_return = ((total_value / pf["initial_capital"]) - 1) * 100
            return_str = f"({total_return:+.2f}% all-time)"
        else:
            return_str = "(on-chain)"

        print(f"\n  Cash:     ${cash:>10.2f}")
        print(f"  Invested: ${total_invested:>10.2f}")
        print(f"  Total:    ${total_value:>10.2f}  {return_str}")
        print(f"  Open P&L: ${total_pnl:>+10.2f}")

    conn.close()


# ── trades ───────────────────────────────────────────────────────────────────

def cmd_trades(args):
    conn = get_conn()
    c = conn.cursor()
    cutoff = (datetime.now() - timedelta(hours=args.hours)).isoformat()

    c.execute("""SELECT t.id, t.symbol, t.side, t.quantity, t.price, t.amount_usd, t.fee,
                        t.decision_context, t.created_at, t.portfolio_value, t.cash_after,
                        tr.code as trader_code
                 FROM trades t
                 LEFT JOIN traders tr ON tr.id = t.trader_id
                 WHERE t.created_at > ? ORDER BY t.created_at""", (cutoff,))
    rows = c.fetchall()

    print(f"\n  Trades in last {args.hours}h: {len(rows)}")
    print(f"  {'ID':>4} {'Time':<20} {'Side':<5} {'Symbol':<12} {'Qty':>12} {'Price':>10} {'USD':>10} {'Trader':<16} {'Source':<12}")
    print(f"  {'-'*4} {'-'*20} {'-'*5} {'-'*12} {'-'*12} {'-'*10} {'-'*10} {'-'*16} {'-'*12}")

    for r in rows:
        source = _trade_source(r["decision_context"])
        trader = r["trader_code"] or "?"
        print(f"  {r['id']:>4} {r['created_at'][:19]:<20} {r['side']:<5} {r['symbol']:<12} "
              f"{r['quantity']:>12.6g} ${r['price']:>9.6g} ${r['amount_usd']:>9.2f} {trader:<16} {source:<12}")
        # Show reasoning in verbose mode
        if getattr(args, 'verbose', False) and r["decision_context"]:
            _print_trade_reasoning(r["decision_context"])

    # Summary
    buys = sum(r["amount_usd"] for r in rows if r["side"] == "BUY")
    sells = sum(r["amount_usd"] for r in rows if r["side"] == "SELL")
    fees = sum(r["fee"] for r in rows)
    print(f"\n  Bought: ${buys:.2f}  Sold: ${sells:.2f}  Fees: ${fees:.4f}  Net flow: ${sells - buys:.2f}")
    conn.close()


# ── signals ──────────────────────────────────────────────────────────────────

def cmd_signals(args):
    conn = get_conn()
    c = conn.cursor()
    cutoff = (datetime.now() - timedelta(hours=args.hours)).isoformat()

    c.execute("""SELECT strategy_name, symbol, action, strength, reason,
                        was_executed, outcome_pct, adx, rsi, signal_time
                 FROM strategy_signals_log WHERE signal_time > ?
                 ORDER BY signal_time""", (cutoff,))
    rows = c.fetchall()

    print(f"\n  Signals in last {args.hours}h: {len(rows)}")
    executed = sum(1 for r in rows if r["was_executed"])
    print(f"  Executed: {executed}  Skipped: {len(rows) - executed}")

    print(f"\n  {'Time':<17} {'Exec':<5} {'Act':<5} {'Symbol':<12} {'Str':>5} {'ADX':>5} {'RSI':>5} {'Strategy':<20}")
    print(f"  {'-'*17} {'-'*5} {'-'*5} {'-'*12} {'-'*5} {'-'*5} {'-'*5} {'-'*20}")

    for r in rows:
        ex = "YES" if r["was_executed"] else "-"
        adx = f"{r['adx']:.0f}" if r["adx"] else "-"
        rsi = f"{r['rsi']:.0f}" if r["rsi"] else "-"
        print(f"  {r['signal_time'][:16]:<17} {ex:<5} {r['action']:<5} {r['symbol']:<12} "
              f"{r['strength']:>5.1f} {adx:>5} {rsi:>5} {r['strategy_name']:<20}")

    conn.close()


# ── outcomes ─────────────────────────────────────────────────────────────────

def cmd_outcomes(args):
    conn = get_conn()
    c = conn.cursor()
    cutoff = (datetime.now() - timedelta(days=args.days)).isoformat()

    c.execute("""SELECT symbol, entry_date, exit_date, entry_price, exit_price,
                        outcome_pct, outcome_usd, holding_hours, signal_type,
                        signal_strength, exit_reason, market_regime
                 FROM trade_outcomes WHERE exit_date > ?
                 ORDER BY exit_date""", (cutoff,))
    rows = c.fetchall()

    print(f"\n  Closed trades in last {args.days}d: {len(rows)}")
    if not rows:
        conn.close()
        return

    wins = sum(1 for r in rows if (r["outcome_pct"] or 0) > 0)
    total_usd = sum(r["outcome_usd"] or 0 for r in rows)
    avg_hold = sum(r["holding_hours"] or 0 for r in rows) / len(rows)

    print(f"  Win rate: {wins}/{len(rows)} ({100*wins/len(rows):.0f}%)  "
          f"Net P&L: ${total_usd:+.2f}  Avg hold: {avg_hold:.1f}h")

    print(f"\n  {'Symbol':<12} {'Entry→Exit':<35} {'%':>8} {'USD':>10} {'Hold':>7} {'Signal':<15} {'Exit':<10}")
    print(f"  {'-'*12} {'-'*35} {'-'*8} {'-'*10} {'-'*7} {'-'*15} {'-'*10}")

    for r in rows:
        period = f"{r['entry_date'][:10]} → {r['exit_date'][:10]}"
        sig = r["signal_type"] or "unknown"
        ex = r["exit_reason"] or "-"
        print(f"  {r['symbol']:<12} {period:<35} {r['outcome_pct']:>+7.2f}% "
              f"${r['outcome_usd']:>+9.2f} {r['holding_hours']:>6.1f}h {sig:<15} {ex:<10}")

    conn.close()


# ── pnl ──────────────────────────────────────────────────────────────────────

def cmd_pnl(args):
    conn = get_conn()
    c = conn.cursor()
    cutoff = (datetime.now() - timedelta(days=args.days)).strftime("%Y-%m-%d")

    # Phase 3: paper_snapshots still uses portfolio_id (not in trades/positions scope)
    c.execute("""SELECT date, portfolio_value, cash, num_positions
                 FROM paper_snapshots
                 WHERE date >= ? AND portfolio_id = 1
                 ORDER BY date""", (cutoff,))
    rows = c.fetchall()

    if not rows:
        print("  No snapshots found in range.")
        conn.close()
        return

    print(f"\n  {'Date':<12} {'Value':>10} {'Cash':>10} {'Positions':>10} {'Daily':>10}")
    print(f"  {'-'*12} {'-'*10} {'-'*10} {'-'*10} {'-'*10}")

    prev_val = None
    for r in rows:
        daily = ""
        if prev_val is not None:
            chg = r["portfolio_value"] - prev_val
            daily = f"${chg:>+9.2f}"
        prev_val = r["portfolio_value"]
        print(f"  {r['date']:<12} ${r['portfolio_value']:>9.2f} ${r['cash']:>9.2f} "
              f"{r['num_positions']:>10} {daily:>10}")

    conn.close()


# ── indicators ───────────────────────────────────────────────────────────────

def cmd_indicators(args):
    conn = get_conn()
    c = conn.cursor()
    symbol = args.symbol.upper()
    # Only add -USD if symbol doesn't already have a chain suffix
    if not symbol.endswith("-USD") and not symbol.endswith("-BASE") and not symbol.endswith("-ETH"):
        symbol += "-USD"

    for tf in ["1h", "4h", "1d"]:
        c.execute("""SELECT * FROM indicators
                     WHERE symbol = ? AND timeframe = ?
                     ORDER BY timestamp DESC LIMIT 1""", (symbol, tf))
        row = c.fetchone()
        if not row:
            continue
        print(f"\n  {symbol} [{tf}] — ts={row['timestamp']}")
        keys = ["rsi_14", "adx_14", "macd_line", "macd_signal", "macd_histogram",
                "sma_20", "sma_50", "sma_200", "ema_12", "ema_26",
                "bb_upper", "bb_middle", "bb_lower", "bb_width",
                "atr_14", "volume_sma_20", "volume_ratio"]
        for k in keys:
            v = row[k]
            if v is not None:
                print(f"    {k:<20} {v:>12.6g}")

    conn.close()


# ── health ───────────────────────────────────────────────────────────────────

def cmd_health(args):
    conn = get_conn()
    c = conn.cursor()

    # Data freshness
    print("\n  Data Freshness")
    print(f"  {'-'*50}")
    for tf in ["1h", "4h", "1d"]:
        c.execute("SELECT MAX(timestamp) FROM candlesticks WHERE timeframe = ?", (tf,))
        row = c.fetchone()
        if row and row[0]:
            age_h = (time.time() - row[0]) / 3600
            print(f"    {tf}: {age_h:.1f}h ago")

    # Positions & cash (cross-portfolio, no account filter needed)
    c.execute("SELECT COUNT(*) FROM positions WHERE status = 'open'")
    pos_count = c.fetchone()[0]
    c.execute("SELECT cash_after FROM trades ORDER BY id DESC LIMIT 1")
    row = c.fetchone()
    cash = row[0] if row else 0
    print(f"\n  Portfolio: {pos_count} positions, ${cash:.2f} cash")

    # Recent trade count
    cutoff_24h = (datetime.now() - timedelta(hours=24)).isoformat()
    c.execute("SELECT COUNT(*) FROM trades WHERE created_at > ?", (cutoff_24h,))
    print(f"  Trades (24h): {c.fetchone()[0]}")

    conn.close()

    # Systemd timers
    print(f"\n  Systemd Timers")
    print(f"  {'-'*50}")
    try:
        result = subprocess.run(
            ["systemctl", "--user", "list-timers", "--all", "--no-pager"],
            capture_output=True, text=True, timeout=5)
        for line in result.stdout.strip().split("\n"):
            if any(x in line for x in [
                "signal-trading", "intraday-update", "risk-guardian",
                "daily-analysis", "trading-agent", "gateway-watchdog",
                "portfolio-report", "signal-alerts"
            ]):
                print(f"    {line.strip()}")
    except Exception:
        print("    (could not query timers)")


# ── dex ──────────────────────────────────────────────────────────────────────

def cmd_dex(args):
    """DEX subcommands for Arwen portfolio (on-chain via Bankr)."""
    sub = args.dex_action

    if sub == "balance":
        from dex.dex_executor import DexExecutor
        executor = DexExecutor()
        result = executor.get_wallet_summary()
        balances = result.get("balances", [])
        if balances:
            total_usd = 0
            print(f"\n  Arwen Wallet (Bankr DEX)")
            print(f"  {'Token':<10} {'Chain':<10} {'Amount':>15} {'USD Value':>12}")
            print(f"  {'-'*10} {'-'*10} {'-'*15} {'-'*12}")
            for b in balances:
                usd = b.get("usd_value", 0)
                total_usd += usd
                print(f"  {b['token']:<10} {b['chain']:<10} {b['amount']:>15.6g} "
                      f"${usd:>11.2f}")
            print(f"\n  Total: ${total_usd:.2f}")
        else:
            print("  No balances returned from Bankr API")
            raw = result.get("raw")
            if raw:
                print(f"  Raw response: {json.dumps(raw, default=str)[:300]}")

    elif sub == "buy":
        if not args.symbol or not args.amount:
            print("Error: symbol and --amount required")
            return
        from dex.dex_executor import DexExecutor
        executor = DexExecutor(dry_run=args.dry_run)
        result = executor.execute_buy(args.symbol, args.amount,
                                       chain=args.chain, reason=args.reason)
        if result.get("success"):
            if result.get("mode") == "dry-run":
                print(f"\n  DRY-RUN: Would buy ${result.get('amount_usd', args.amount):.2f} of {result.get('symbol', args.symbol)}")
            else:
                print(f"\n  BUY {result.get('quantity', 0):.6g} {result.get('symbol', '')} @ ${result.get('price', 0):.6g}")
                print(f"  Amount: ${result.get('amount_usd', 0):.2f}  Chain: {result.get('chain', '')}")
                if result.get("tx_hash"):
                    print(f"  TX: {result['tx_hash']}")
        else:
            print(f"  Failed: {result.get('error', 'unknown error')}")

    elif sub == "sell":
        if not args.symbol:
            print("Error: symbol required")
            return
        from dex.dex_executor import DexExecutor
        executor = DexExecutor(dry_run=args.dry_run)
        sell_pct = args.pct / 100 if args.pct else None
        result = executor.execute_sell(args.symbol, amount=args.amount,
                                        sell_pct=sell_pct, chain=args.chain,
                                        reason=args.reason)
        if result.get("success"):
            if result.get("mode") == "dry-run":
                print(f"\n  DRY-RUN: Would sell {result.get('quantity', 0):.6g} {result.get('symbol', args.symbol)}")
            else:
                print(f"\n  SELL {result.get('quantity', 0):.6g} {result.get('symbol', '')} @ ${result.get('price', 0):.6g}")
                print(f"  Amount: ${result.get('amount_usd', 0):.2f}  Chain: {result.get('chain', '')}")
                if result.get("tx_hash"):
                    print(f"  TX: {result['tx_hash']}")
        else:
            print(f"  Failed: {result.get('error', 'unknown error')}")

    elif sub == "sync":
        from dex.dex_executor import DexExecutor
        executor = DexExecutor()
        result = executor.sync_balances()
        print(f"\n  Balance Reconciliation (Arwen)")
        print(f"  {'Token':<12} {'On-chain':>12} {'In DB':>12} {'Diff':>12}")
        print(f"  {'-'*12} {'-'*12} {'-'*12} {'-'*12}")
        on_chain = result.get("on_chain", {})
        in_db = result.get("in_db", {})
        all_tokens = set(list(on_chain.keys()) + [
            s.replace("-BASE", "").replace("-ETH", "") for s in in_db.keys()
        ])
        for token in sorted(all_tokens):
            chain_qty = on_chain.get(token, 0)
            # Find matching DB symbol
            db_qty = 0
            for sym, qty in in_db.items():
                if token in sym:
                    db_qty = qty
                    break
            diff = chain_qty - db_qty
            marker = " *" if abs(diff) > 0.0001 * max(chain_qty, db_qty, 1) else ""
            print(f"  {token:<12} {chain_qty:>12.6g} {db_qty:>12.6g} {diff:>+12.6g}{marker}")
        if result.get("discrepancies"):
            print(f"\n  * = discrepancy detected")
        else:
            print(f"\n  All positions in sync")

    elif sub == "health":
        from dex.bankr_client import BankrClient
        client = BankrClient()
        result = client.health_check()
        print(f"\n  Bankr API: {result.get('status', 'unknown')}")
        print(f"  Requests today: {result.get('requests_today', 0)}")

    elif sub == "txns":
        conn = get_conn()
        cutoff = (datetime.now() - timedelta(hours=args.hours)).isoformat()
        # Phase 3: dex_transactions still uses portfolio_id (not in trades/positions scope)
        rows = conn.execute("""
            SELECT dt.id, dt.chain, dt.action, dt.from_token, dt.to_token,
                   dt.amount_in, dt.amount_out, dt.price, dt.tx_hash,
                   dt.status, dt.created_at
            FROM dex_transactions dt
            WHERE dt.portfolio_id = 4 AND dt.created_at > ?
            ORDER BY dt.created_at
        """, (cutoff,)).fetchall()
        conn.close()

        print(f"\n  DEX Transactions (last {args.hours}h): {len(rows)}")
        if rows:
            print(f"  {'ID':>4} {'Time':<17} {'Chain':<6} {'Action':<6} "
                  f"{'From→To':<18} {'In':>12} {'Out':>12} {'Status':<10}")
            print(f"  {'-'*4} {'-'*17} {'-'*6} {'-'*6} {'-'*18} {'-'*12} {'-'*12} {'-'*10}")
            for r in rows:
                pair = f"{r['from_token']}→{r['to_token']}"
                print(f"  {r['id']:>4} {r['created_at'][:16]:<17} {r['chain']:<6} "
                      f"{r['action']:<6} {pair:<18} {r['amount_in']:>12.6g} "
                      f"{r['amount_out']:>12.6g} {r['status']:<10}")

    else:
        print(f"Unknown dex action: {sub}")
        print("Available: balance, buy, sell, sync, health, txns")


# ── helpers ──────────────────────────────────────────────────────────────────

def _held_str(entry_time_str):
    if not entry_time_str:
        return "-"
    try:
        entry = datetime.fromisoformat(entry_time_str)
        delta = datetime.now() - entry
        hours = delta.total_seconds() / 3600
        if hours < 1:
            return f"{delta.total_seconds()/60:.0f}m"
        if hours < 48:
            return f"{hours:.1f}h"
        return f"{hours/24:.1f}d"
    except Exception:
        return "-"


def _trade_source(decision_context):
    if not decision_context:
        return "unknown"
    try:
        ctx = json.loads(decision_context)
        sig_type = ctx.get("signal_type")
        exit_reason = ctx.get("exit_reason")
        if sig_type == "risk_exit":
            return f"risk/{ctx.get('exit_type', '?')}"
        if exit_reason == "llm_signal" or sig_type == "llm":
            conviction = ctx.get("conviction", "")
            return f"llm/{conviction}" if conviction else "llm_agent"
        if sig_type:
            return f"signal/{sig_type}"
        return "unknown"
    except Exception:
        return "unknown"


def _print_trade_reasoning(decision_context):
    """Print structured reasoning from a trade's decision_context."""
    try:
        ctx = json.loads(decision_context)
        reasoning = ctx.get("reasoning", {})
        if not reasoning:
            # Legacy format: just show signal info
            sig = ctx.get("signal_type", "")
            strength = ctx.get("signal_strength")
            reason = ctx.get("reason", "")
            if sig or reason:
                parts = []
                if sig:
                    parts.append(f"signal={sig}")
                if strength is not None:
                    parts.append(f"strength={strength}")
                if reason:
                    parts.append(reason[:100])
                print(f"         {' | '.join(parts)}")
            return

        # Structured reasoning
        thesis = reasoning.get("thesis", "")
        if thesis:
            print(f"         Thesis: {thesis[:150]}")
        trend = reasoning.get("trend_regime") or ctx.get("trend_regime")
        if trend:
            print(f"         Trend: {trend}")
        supporting = reasoning.get("supporting", [])
        if supporting:
            print(f"         +: {', '.join(str(s) for s in supporting[:5])}")
        contradicting = reasoning.get("contradicting", [])
        if contradicting:
            print(f"         -: {', '.join(str(s) for s in contradicting[:5])}")
        risk_note = reasoning.get("risk_note")
        if risk_note:
            print(f"         Risk: {risk_note[:120]}")
        adjustments = ctx.get("guardrail_adjustments", [])
        if adjustments:
            print(f"         Guardrails: {', '.join(adjustments[:5])}")
    except Exception:
        pass


# ── main ─────────────────────────────────────────────────────────────────────

def cmd_signal_trace(args):
    """Trace signal flow through execution gates."""
    import subprocess
    script_dir = os.path.dirname(os.path.abspath(__file__))
    cmd = [sys.executable, os.path.join(script_dir, "scripts", "verify_signal_flow.py")]
    if args.symbol:
        cmd.append(args.symbol)
    if args.recent:
        cmd.append("--recent")
    subprocess.run(cmd)


def cmd_strategy_trace(args):
    """Trace strategy internals — shows why each strategy is silent or firing."""
    import subprocess
    script_dir = os.path.dirname(os.path.abspath(__file__))
    cmd = [sys.executable, os.path.join(script_dir, "scripts", "strategy_trace.py")]
    if args.symbol:
        cmd.append(args.symbol)
    subprocess.run(cmd)


def main():
    parser = argparse.ArgumentParser(
        prog="edoras",
        description="Edoras Trading System CLI",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("snapshot", help="Current portfolio state")

    p = sub.add_parser("trades", help="Recent trades")
    p.add_argument("--hours", type=int, default=24)
    p.add_argument("-v", "--verbose", action="store_true", help="Show trade reasoning")

    p = sub.add_parser("signals", help="Recent strategy signals")
    p.add_argument("--hours", type=int, default=24)

    p = sub.add_parser("outcomes", help="Closed trade outcomes")
    p.add_argument("--days", type=int, default=7)

    p = sub.add_parser("pnl", help="Daily P&L series")
    p.add_argument("--days", type=int, default=30)

    p = sub.add_parser("indicators", help="Latest indicators for a symbol")
    p.add_argument("symbol")

    sub.add_parser("health", help="System health check")

    # DEX subcommands
    dex_p = sub.add_parser("dex", help="DEX operations (Arwen portfolio)")
    dex_p.add_argument("dex_action", choices=["balance", "buy", "sell", "sync", "health", "txns"],
                       help="DEX action")
    dex_p.add_argument("symbol", nargs="?", help="Token symbol (e.g. VVV-BASE)")
    dex_p.add_argument("--amount", type=float, help="USD amount (buy) or token qty (sell)")
    dex_p.add_argument("--pct", type=float, help="Sell percentage (0-100)")
    dex_p.add_argument("--chain", type=str, default="base")
    dex_p.add_argument("--dry-run", action="store_true", help="Simulate without executing")
    dex_p.add_argument("--reason", type=str, default="", help="Trade reason")
    dex_p.add_argument("--hours", type=int, default=24, help="Lookback hours (txns)")

    # Signal flow trace
    p = sub.add_parser("signal-trace", help="Trace signal flow through execution gates")
    p.add_argument("symbol", nargs="?", help="Trace a specific symbol")
    p.add_argument("--recent", action="store_true", help="Show recent skip reasons")

    # Strategy trace
    p = sub.add_parser("strategy-trace", help="Trace strategy internals (why silent/firing)")
    p.add_argument("symbol", nargs="?", help="Trace a specific symbol")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    commands = {
        "snapshot": cmd_snapshot,
        "trades": cmd_trades,
        "signals": cmd_signals,
        "outcomes": cmd_outcomes,
        "pnl": cmd_pnl,
        "indicators": cmd_indicators,
        "health": cmd_health,
        "dex": cmd_dex,
        "signal-trace": cmd_signal_trace,
        "strategy-trace": cmd_strategy_trace,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
