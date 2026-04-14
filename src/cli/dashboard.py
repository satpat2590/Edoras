#!/usr/bin/env python3
"""
Edoras Dashboard — Live TUI for monitoring the trading system.

Usage:
    edoras-dashboard                         # portfolio view, auto-refresh 30s
    edoras-dashboard --view system           # data engineering / system view
    edoras-dashboard --view system --once    # single render
    edoras-dashboard -r 10                   # refresh every 10s

Views:
    portfolio  — portfolios, positions, trades, signals, strategies, risk (default)
    system     — data feed freshness, per-symbol coverage, services & timers, DB stats
"""

import argparse
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

try:
    from config import DB_PATH as _CONFIG_DB_PATH
    DEFAULT_DB = Path(_CONFIG_DB_PATH)
except ImportError:
    DEFAULT_DB = Path.home() / "edoras" / "crypto_data.db"
DB_PATH = Path(os.environ.get("EDORAS_DB", str(DEFAULT_DB)))

# ── Helpers ────────────────────────────────────────────────────────────────


def get_conn():
    conn = sqlite3.connect(str(DB_PATH), timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def age_str(epoch_ts):
    """Human-readable age from a unix timestamp."""
    if not epoch_ts:
        return "never"
    delta = time.time() - epoch_ts
    if delta < 0:
        return "future?"
    if delta < 60:
        return f"{delta:.0f}s"
    if delta < 3600:
        return f"{delta / 60:.0f}m"
    if delta < 86400:
        return f"{delta / 3600:.1f}h"
    return f"{delta / 86400:.1f}d"


def freshness_style(hours, feed=""):
    if hours is None:
        return "dim"
    if hours < 2:
        return "green"
    if hours < 6:
        return "yellow"
    if hours < 24:
        return "dark_orange"
    return "red"


# Per-feed staleness thresholds (hours). Feed name substring -> max acceptable age.
_STALE_THRESHOLDS = {
    "5m": 1,
    "1h": 4,
    "4h": 10,
    "1d": 28,
    "Equity": 100,  # equity markets closed on weekends; up to ~4 days normal
    "DEX Candles": 6,
    "PM Candles": 4,
    "Indicators": 4,
    "Scores": 12,
    "Market Regime": 28,
    "Collection Log": 12,
    "DEX Txns": None,  # no threshold; informational
}


def pnl_style(val):
    if val is None or val == 0:
        return "dim"
    return "green" if val > 0 else "red"


def pct_str(val):
    if val is None:
        return "-"
    return f"{val:+.2f}%"


# ── Data Queries ───────────────────────────────────────────────────────────


def query_portfolios(conn):
    c = conn.cursor()
    c.execute("SELECT id, name, mode, initial_capital FROM portfolios WHERE is_active = 1")
    portfolios = c.fetchall()
    results = []
    for pf in portfolios:
        pid = pf["id"]
        # open positions
        c.execute(
            """SELECT symbol, quantity, entry_price, current_price,
                        pnl, pnl_percent, entry_time
                   FROM positions WHERE portfolio_id = ? AND status = 'open'
                   ORDER BY ABS(pnl) DESC""",
            (pid,),
        )
        positions = c.fetchall()
        # cash from latest trade
        c.execute(
            "SELECT cash_after FROM trades WHERE portfolio_id = ? ORDER BY id DESC LIMIT 1", (pid,)
        )
        row = c.fetchone()
        cash = row["cash_after"] if row else pf["initial_capital"]
        invested = sum(p["quantity"] * (p["current_price"] or p["entry_price"]) for p in positions)
        open_pnl = sum(p["pnl"] or 0 for p in positions)
        total_val = cash + invested
        init = pf["initial_capital"]
        all_time_pct = ((total_val / init) - 1) * 100 if init > 0 else None
        results.append(
            {
                "id": pid,
                "name": pf["name"],
                "mode": pf["mode"],
                "initial": init,
                "cash": cash,
                "invested": invested,
                "total": total_val,
                "open_pnl": open_pnl,
                "all_time_pct": all_time_pct,
                "positions": [dict(p) for p in positions],
            }
        )
    return results


def _epoch_age_h(epoch_ts):
    """Hours since a unix epoch timestamp, or None."""
    if not epoch_ts:
        return None
    return (time.time() - epoch_ts) / 3600


def _iso_age_h(iso_str):
    """Hours since an ISO-format datetime string, or None."""
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str)
        return (datetime.now() - dt).total_seconds() / 3600
    except Exception:
        return None


# Filters to partition candlesticks by data source
_CRYPTO_FILTER = "symbol NOT LIKE 'PM:%' AND symbol NOT LIKE '%-BASE' AND symbol NOT IN ('AAPL','MSFT','GOOGL','AMZN','META','NVDA','TSLA','JPM','JNJ','V','SPY','QQQ','^VIX')"
_EQUITY_FILTER = "symbol IN ('AAPL','MSFT','GOOGL','AMZN','META','NVDA','TSLA','JPM','JNJ','V','SPY','QQQ','^VIX')"
_DEX_FILTER = "symbol LIKE '%-BASE'"
_PM_FILTER = "symbol LIKE 'PM:%'"


def query_data_freshness(conn):
    """Per-source, per-timeframe freshness."""
    c = conn.cursor()
    rows = []

    # ── Crypto candles (Coinbase) by timeframe ──
    for tf in ["5m", "1h", "4h", "1d"]:
        c.execute(
            f"SELECT MAX(timestamp) as latest, COUNT(*) as cnt FROM candlesticks WHERE timeframe = ? AND {_CRYPTO_FILTER}",
            (tf,),
        )
        r = c.fetchone()
        source = "WebSocket" if tf == "5m" else "Coinbase"
        rows.append(
            {
                "source": source,
                "feed": f"Crypto ({tf})",
                "latest_ts": r["latest"],
                "age_h": _epoch_age_h(r["latest"]),
                "rows": r["cnt"],
            }
        )

    # ── Equity candles (Yahoo) — markets closed on weekends ──
    weekday = datetime.now().weekday()  # 0=Mon, 5=Sat, 6=Sun
    equity_closed = weekday >= 5  # Saturday or Sunday
    for tf in ["1h", "4h", "1d"]:
        c.execute(
            f"SELECT MAX(timestamp) as latest, COUNT(*) as cnt FROM candlesticks WHERE timeframe = ? AND {_EQUITY_FILTER}",
            (tf,),
        )
        r = c.fetchone()
        rows.append(
            {
                "source": "Yahoo",
                "feed": f"Equity ({tf})",
                "latest_ts": r["latest"],
                "age_h": _epoch_age_h(r["latest"]),
                "rows": r["cnt"],
                "market_closed": equity_closed,
            }
        )

    # ── DEX candles (GeckoTerminal) ──
    c.execute(
        f"SELECT MAX(timestamp) as latest, COUNT(*) as cnt FROM candlesticks WHERE {_DEX_FILTER}"
    )
    r = c.fetchone()
    rows.append(
        {
            "source": "Gecko/DEX",
            "feed": "DEX Candles",
            "latest_ts": r["latest"],
            "age_h": _epoch_age_h(r["latest"]),
            "rows": r["cnt"],
        }
    )

    # ── Polymarket candles ──
    c.execute(
        f"SELECT MAX(timestamp) as latest, COUNT(*) as cnt FROM candlesticks WHERE {_PM_FILTER}"
    )
    r = c.fetchone()
    rows.append(
        {
            "source": "Polymarket",
            "feed": "PM Candles",
            "latest_ts": r["latest"],
            "age_h": _epoch_age_h(r["latest"]),
            "rows": r["cnt"],
        }
    )

    # ── Indicators ──
    c.execute("SELECT MAX(timestamp) as latest, COUNT(*) as cnt FROM indicators")
    r = c.fetchone()
    rows.append(
        {
            "source": "Computed",
            "feed": "Indicators",
            "latest_ts": r["latest"],
            "age_h": _epoch_age_h(r["latest"]),
            "rows": r["cnt"],
        }
    )

    # ── Sentiment ──
    c.execute("SELECT MAX(timestamp) as latest, COUNT(*) as cnt FROM sentiment_scores")
    r = c.fetchone()
    rows.append(
        {
            "source": "Sentiment",
            "feed": "Scores",
            "latest_ts": None,
            "age_h": _iso_age_h(r["latest"])
            if not isinstance(r["latest"], (int, float))
            else _epoch_age_h(r["latest"]),
            "rows": r["cnt"],
        }
    )

    # ── Market regime ──
    c.execute("SELECT MAX(date) as latest, COUNT(*) as cnt FROM market_regime")
    r = c.fetchone()
    rows.append(
        {
            "source": "Regime",
            "feed": "Market Regime",
            "latest_ts": None,
            "age_h": _iso_age_h(r["latest"]),
            "rows": r["cnt"],
        }
    )

    # ── Collection log ──
    c.execute("SELECT MAX(last_updated) as latest FROM collection_log")
    r = c.fetchone()
    rows.append(
        {
            "source": "Collector",
            "feed": "Collection Log",
            "latest_ts": None,
            "age_h": _iso_age_h(r["latest"]),
            "rows": None,
        }
    )

    # ── DEX transactions ──
    c.execute("SELECT MAX(created_at) as latest, COUNT(*) as cnt FROM dex_transactions")
    r = c.fetchone()
    rows.append(
        {
            "source": "Bankr DEX",
            "feed": "DEX Txns",
            "latest_ts": None,
            "age_h": _iso_age_h(r["latest"]),
            "rows": r["cnt"],
        }
    )

    return rows


def query_symbol_coverage(conn):
    """Per-symbol, per-timeframe freshness for the system view."""
    c = conn.cursor()
    c.execute("""
        SELECT symbol, timeframe, MAX(timestamp) as latest, COUNT(*) as cnt
        FROM candlesticks
        WHERE symbol NOT LIKE 'PM:%'
        GROUP BY symbol, timeframe
        ORDER BY symbol, timeframe
    """)
    rows = []
    for r in c.fetchall():
        age_h = _epoch_age_h(r["latest"])
        rows.append(
            {
                "symbol": r["symbol"],
                "timeframe": r["timeframe"],
                "latest_ts": r["latest"],
                "age_h": age_h,
                "rows": r["cnt"],
            }
        )
    return rows


def query_db_stats(conn):
    """Database table sizes and overall stats."""
    c = conn.cursor()
    tables = [
        "candlesticks",
        "indicators",
        "trades",
        "positions",
        "trade_outcomes",
        "strategy_signals_log",
        "risk_events",
        "sentiment_scores",
        "market_regime",
        "dex_transactions",
        "portfolio_analysis",
        "correlations",
        "ticks",
    ]
    results = []
    for tbl in tables:
        try:
            c.execute(f"SELECT COUNT(*) as cnt FROM [{tbl}]")
            cnt = c.fetchone()["cnt"]
            results.append({"table": tbl, "rows": cnt})
        except Exception:
            results.append({"table": tbl, "rows": None})
    # DB file size
    db_size = None
    try:
        db_size = DB_PATH.stat().st_size
    except Exception:
        pass
    return {"tables": results, "db_size_bytes": db_size}


def query_recent_trades(conn, hours=24):
    cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
    c = conn.cursor()
    c.execute(
        """SELECT t.symbol, t.side, t.quantity, t.price, t.amount_usd, t.fee,
                        t.created_at, tr.code as trader
                 FROM trades t
                 LEFT JOIN traders tr ON tr.id = t.trader_id
                 WHERE t.created_at > ? ORDER BY t.created_at DESC LIMIT 12""",
        (cutoff,),
    )
    return [dict(r) for r in c.fetchall()]


def query_recent_signals(conn, hours=24):
    cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
    c = conn.cursor()
    c.execute(
        """SELECT strategy_name, symbol, action, strength, was_executed,
                        skip_reason, signal_time
                 FROM strategy_signals_log WHERE signal_time > ?
                 ORDER BY signal_time DESC LIMIT 12""",
        (cutoff,),
    )
    return [dict(r) for r in c.fetchall()]


def query_recent_risk_events(conn, hours=48):
    cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
    c = conn.cursor()
    c.execute(
        """SELECT event_type, symbol, trigger_price, current_price,
                        action_taken, created_at
                 FROM risk_events WHERE created_at > ?
                 ORDER BY created_at DESC LIMIT 8""",
        (cutoff,),
    )
    return [dict(r) for r in c.fetchall()]


def query_strategy_performance(conn):
    c = conn.cursor()
    # From trade_outcomes: per signal_type realized PnL
    c.execute("""SELECT signal_type as strategy, COUNT(*) as trades,
                        SUM(CASE WHEN outcome_pct > 0 THEN 1 ELSE 0 END) as wins,
                        SUM(outcome_usd) as total_pnl,
                        AVG(outcome_pct) as avg_pct,
                        AVG(holding_hours) as avg_hold_h
                 FROM trade_outcomes
                 GROUP BY signal_type
                 ORDER BY total_pnl DESC""")
    return [dict(r) for r in c.fetchall()]


def query_trade_stats(conn):
    c = conn.cursor()
    # All-time
    c.execute("SELECT COUNT(*) as total, SUM(amount_usd) as volume FROM trades")
    all_time = dict(c.fetchone())
    # 24h
    cutoff = (datetime.now() - timedelta(hours=24)).isoformat()
    c.execute(
        "SELECT COUNT(*) as total, SUM(amount_usd) as volume FROM trades WHERE created_at > ?",
        (cutoff,),
    )
    last_24h = dict(c.fetchone())
    # 7d
    cutoff7 = (datetime.now() - timedelta(days=7)).isoformat()
    c.execute(
        "SELECT COUNT(*) as total, SUM(amount_usd) as volume FROM trades WHERE created_at > ?",
        (cutoff7,),
    )
    last_7d = dict(c.fetchone())
    # realized PnL
    c.execute("SELECT SUM(outcome_usd) as realized, COUNT(*) as closed FROM trade_outcomes")
    realized = dict(c.fetchone())
    return {"all_time": all_time, "24h": last_24h, "7d": last_7d, "realized": realized}


def query_timer_status():
    """Discover all edoras-related systemd timers and persistent services."""
    results = []

    # ── Persistent services (always-running daemons) ──
    SERVICES = ["coinbase-websocket"]
    for svc in SERVICES:
        try:
            out = subprocess.run(
                [
                    "systemctl",
                    "--user",
                    "show",
                    f"{svc}.service",
                    "--property=ActiveState,SubState,ActiveEnterTimestamp",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            props = dict(
                line.split("=", 1) for line in out.stdout.strip().split("\n") if "=" in line
            )
            active = props.get("ActiveState", "unknown")
            sub = props.get("SubState", "")
            since_raw = props.get("ActiveEnterTimestamp", "")
            since = ""
            if since_raw and since_raw != "n/a":
                try:
                    dt = datetime.strptime(since_raw.strip(), "%a %Y-%m-%d %H:%M:%S %Z")
                    since = age_str(dt.timestamp())
                except Exception:
                    since = since_raw.strip()[:16]
            status = "running" if active == "active" else active
            results.append(
                {
                    "name": svc,
                    "kind": "service",
                    "status": status,
                    "last_run": f"up {since}" if status == "running" and since else "",
                    "next_run": "always-on",
                }
            )
        except Exception:
            results.append(
                {
                    "name": svc,
                    "kind": "service",
                    "status": "unknown",
                    "last_run": "",
                    "next_run": "",
                }
            )

    # ── Timers — discover all crypto/edoras/trading timers dynamically ──
    try:
        out = subprocess.run(
            ["systemctl", "--user", "list-timers", "--all", "--no-pager"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        lines = out.stdout.strip().split("\n")
        # Skip header line; stop at the summary line ("N timers listed.")
        for line in lines[1:]:
            if not line.strip() or "timers listed" in line:
                break
            # Columns: NEXT  LEFT  LAST  PASSED  UNIT  ACTIVATES
            # The UNIT column contains the timer name; find it by the .timer suffix
            parts = line.split()
            timer_name = ""
            for p in parts:
                if p.endswith(".timer"):
                    timer_name = p.replace(".timer", "")
                    break
            if not timer_name:
                continue
            # Skip system timers not related to edoras
            skip = {"com.system76.FirmwareManager.Notify", "pop-upgrade-notify"}
            if timer_name in skip:
                continue

            # Parse LAST and NEXT from systemctl show for reliability
            try:
                show = subprocess.run(
                    [
                        "systemctl",
                        "--user",
                        "show",
                        f"{timer_name}.timer",
                        "--property=LastTriggerUSec,NextElapseUSecRealtime",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                props = dict(l.split("=", 1) for l in show.stdout.strip().split("\n") if "=" in l)
                last_raw = props.get("LastTriggerUSec", "")
                next_raw = props.get("NextElapseUSecRealtime", "")

                last_str = ""
                if last_raw and last_raw not in ("n/a", "0"):
                    try:
                        dt = datetime.strptime(last_raw.strip(), "%a %Y-%m-%d %H:%M:%S %Z")
                        last_str = age_str(dt.timestamp())
                    except Exception:
                        last_str = ""

                next_str = ""
                if next_raw and next_raw not in ("n/a", "0"):
                    try:
                        dt = datetime.strptime(next_raw.strip(), "%a %Y-%m-%d %H:%M:%S %Z")
                        delta = dt.timestamp() - time.time()
                        if delta < 60:
                            next_str = "<1m"
                        elif delta < 3600:
                            next_str = f"{delta / 60:.0f}m"
                        elif delta < 86400:
                            next_str = f"{delta / 3600:.1f}h"
                        else:
                            next_str = f"{delta / 86400:.1f}d"
                    except Exception:
                        next_str = ""
            except Exception:
                last_str = ""
                next_str = ""

            results.append(
                {
                    "name": timer_name,
                    "kind": "timer",
                    "status": "active",
                    "last_run": last_str,
                    "next_run": next_str,
                }
            )
    except Exception:
        results.append(
            {"name": "timers", "kind": "timer", "status": "error", "last_run": "", "next_run": ""}
        )

    return results


# ── Panel Builders ─────────────────────────────────────────────────────────


def build_portfolio_panel(portfolios):
    table = Table(show_header=True, header_style="bold cyan", box=None, pad_edge=False)
    table.add_column("Portfolio", style="bold")
    table.add_column("Mode", style="dim")
    table.add_column("Value", justify="right")
    table.add_column("Cash", justify="right")
    table.add_column("Invested", justify="right")
    table.add_column("Open P&L", justify="right")
    table.add_column("All-Time", justify="right")
    table.add_column("Pos", justify="right")

    for pf in portfolios:
        pnl_s = pnl_style(pf["open_pnl"])
        at_s = pnl_style(pf["all_time_pct"])
        at_str = pct_str(pf["all_time_pct"]) if pf["all_time_pct"] is not None else "n/a"
        table.add_row(
            pf["name"],
            pf["mode"],
            f"${pf['total']:.2f}",
            f"${pf['cash']:.2f}",
            f"${pf['invested']:.2f}",
            Text(f"${pf['open_pnl']:+.2f}", style=pnl_s),
            Text(at_str, style=at_s),
            str(len(pf["positions"])),
        )
    return Panel(table, title="Portfolios", border_style="blue")


def build_positions_panel(portfolios):
    table = Table(show_header=True, header_style="bold cyan", box=None, pad_edge=False)
    table.add_column("Symbol", style="bold", width=12)
    table.add_column("Quantity", justify="right", width=12)
    table.add_column("Entry", justify="right", width=10)
    table.add_column("Current", justify="right", width=10)
    table.add_column("P&L $", justify="right", width=10)
    table.add_column("P&L %", justify="right", width=10)
    table.add_column("Held", justify="right", style="dim", width=8)

    for pf in portfolios:
        for p in pf["positions"][:10]:  # cap at 10 per portfolio
            pnl = p["pnl"] or 0
            pnl_pct = p["pnl_percent"] or 0
            style = pnl_style(pnl)
            # held time
            held = "-"
            if p["entry_time"]:
                try:
                    entry = datetime.fromisoformat(p["entry_time"])
                    h = (datetime.now() - entry).total_seconds() / 3600
                    held = f"{h:.0f}h" if h < 48 else f"{h / 24:.1f}d"
                except Exception:
                    pass
            table.add_row(
                p["symbol"],
                f"{p['quantity']:.6g}",
                f"${p['entry_price']:.4g}",
                f"${(p['current_price'] or 0):.4g}",
                Text(f"${pnl:+.2f}", style=style),
                Text(f"{pnl_pct:+.2f}%", style=style),
                held,
            )
    return Panel(table, title="Open Positions", border_style="blue")


def build_freshness_panel(feeds):
    table = Table(show_header=True, header_style="bold cyan", box=None, pad_edge=False)
    table.add_column("Source")
    table.add_column("Feed")
    table.add_column("Age", justify="right")
    table.add_column("Rows", justify="right", style="dim")
    table.add_column("Status", justify="center")

    for f in feeds:
        age_h = f["age_h"]
        feed_name = f["feed"]
        style = freshness_style(age_h, feed_name)
        age_display = f"{age_h:.1f}h" if age_h is not None else "n/a"
        if age_h is not None and age_h < 1:
            age_display = f"{age_h * 60:.0f}m"

        # Determine OK/STALE using per-feed thresholds
        threshold = None
        for key, thr in _STALE_THRESHOLDS.items():
            if key in feed_name:
                threshold = thr
                break
        market_closed = f.get("market_closed", False)
        if age_h is None:
            status, status_style = "?", "dim"
        elif market_closed:
            status, status_style = "CLOSED", "dim"
        elif threshold is None:
            status, status_style = "OK", "green"  # no threshold = informational
        elif age_h <= threshold:
            status, status_style = "OK", "green"
        else:
            status, status_style = "STALE", "red"

        rows_str = f"{f['rows']:,}" if f["rows"] is not None else "-"
        table.add_row(
            f["source"],
            feed_name,
            Text(age_display, style=style),
            rows_str,
            Text(status, style=status_style),
        )
    return Panel(table, title="Data Feed Freshness", border_style="green")


def build_trades_panel(trades):
    table = Table(show_header=True, header_style="bold cyan", box=None, pad_edge=False)
    table.add_column("Time", style="dim", width=10)
    table.add_column("Side", width=6)
    table.add_column("Symbol", width=12)
    table.add_column("Quantity", justify="right", width=14)
    table.add_column("Price", justify="right", width=12)
    table.add_column("Amount", justify="right", width=14)
    table.add_column("Trader", style="dim", width=12)

    for t in trades:
        side_style = "bold green" if t["side"] == "BUY" else "bold red"
        raw_t = t["created_at"] or ""
        try:
            dt = datetime.fromisoformat(raw_t)
            compact_t = dt.strftime("%m/%d %H:%M")
        except Exception:
            compact_t = raw_t[:11]
        table.add_row(
            compact_t,
            Text(t["side"], style=side_style),
            t["symbol"],
            f"{t['quantity']:.6g}",
            f"${t['price']:.4g}",
            f"${t['amount_usd']:.2f}",
            t["trader"] or "?",
        )
    if not trades:
        table.add_row("", Text("No trades in last 24h", style="dim"), "", "", "", "", "")
    return Panel(table, title="Recent Trades (24h)", border_style="yellow")


def build_signals_panel(signals):
    table = Table(show_header=True, header_style="bold cyan", box=None, pad_edge=False)
    table.add_column("Time", style="dim", width=10)
    table.add_column("Action", width=6)
    table.add_column("Symbol", width=12)
    table.add_column("Strength", justify="right", width=8)
    table.add_column("Executed", width=8)
    table.add_column("Strategy", width=25)
    table.add_column("Skip Reason", style="dim", width=35)

    for s in signals:
        act_style = (
            "bold green" if s["action"] == "BUY" else "bold red" if s["action"] == "SELL" else "dim"
        )
        exec_str = "✓ YES" if s["was_executed"] else "✗ no"
        exec_style = "bold green" if s["was_executed"] else "dim red"
        skip = (s["skip_reason"] or "")[:34]
        # Compact time: "MM/DD HH:MM"
        raw_t = s["signal_time"] or ""
        try:
            dt = datetime.fromisoformat(raw_t)
            compact_t = dt.strftime("%m/%d %H:%M")
        except Exception:
            compact_t = raw_t[:11]
        table.add_row(
            compact_t,
            Text(s["action"], style=act_style),
            s["symbol"],
            f"{s['strength']:.0f}",
            Text(exec_str, style=exec_style),
            (s["strategy_name"] or "unknown")[:25],
            skip,
        )
    if not signals:
        table.add_row("", Text("No signals in last 24h", style="dim"), "", "", "", "", "")
    return Panel(table, title="Recent Signals (24h)", border_style="yellow")


def build_risk_panel(events):
    table = Table(show_header=True, header_style="bold cyan", box=None, pad_edge=False)
    table.add_column("Time", style="dim")
    table.add_column("Type")
    table.add_column("Symbol")
    table.add_column("Trigger", justify="right")
    table.add_column("Current", justify="right")
    table.add_column("Action")

    for e in events:
        raw_t = e["created_at"] or ""
        try:
            dt = datetime.fromisoformat(raw_t)
            compact_t = dt.strftime("%m/%d %H:%M")
        except Exception:
            compact_t = raw_t[:11]
        table.add_row(
            compact_t,
            e["event_type"],
            e["symbol"],
            f"${e['trigger_price']:.4g}" if e["trigger_price"] else "-",
            f"${e['current_price']:.4g}" if e["current_price"] else "-",
            e["action_taken"] or "-",
        )
    if not events:
        table.add_row("", Text("No risk events in last 48h", style="dim green"), "", "", "", "")
    return Panel(table, title="Risk Events (48h)", border_style="red")


def build_strategy_panel(strategies):
    table = Table(show_header=True, header_style="bold cyan", box=None, pad_edge=False)
    table.add_column("Strategy", width=20)
    table.add_column("Trades", justify="right", width=8)
    table.add_column("Win%", justify="right", width=8)
    table.add_column("P&L $", justify="right", width=12)
    table.add_column("Avg %", justify="right", width=10)
    table.add_column("Avg Hold", justify="right", style="dim", width=10)

    for s in strategies:
        trades = s["trades"] or 0
        wins = s["wins"] or 0
        win_pct = (wins / trades * 100) if trades > 0 else 0
        pnl = s["total_pnl"] or 0
        style = pnl_style(pnl)
        win_style = "bold green" if win_pct >= 50 else "bold red" if win_pct > 0 else "dim"
        avg_hold = f"{s['avg_hold_h']:.0f}h" if s["avg_hold_h"] else "-"
        table.add_row(
            (s["strategy"] or "unknown")[:20],
            str(trades),
            Text(f"{win_pct:.0f}%", style=win_style),
            Text(f"${pnl:+.2f}", style=style),
            Text(pct_str(s["avg_pct"]), style=pnl_style(s["avg_pct"])),
            avg_hold,
        )
    if not strategies:
        table.add_row(Text("No closed trades yet", style="dim"), "", "", "", "", "")
    return Panel(table, title="Strategy Performance (Realized)", border_style="magenta")


def build_stats_panel(stats):
    lines = []
    a = stats["all_time"]
    h24 = stats["24h"]
    d7 = stats["7d"]
    r = stats["realized"]

    lines.append(f"All-time: {a['total'] or 0} trades, ${a['volume'] or 0:,.0f} volume")
    lines.append(f"7-day:    {d7['total'] or 0} trades, ${d7['volume'] or 0:,.0f} volume")
    lines.append(f"24-hour:  {h24['total'] or 0} trades, ${h24['volume'] or 0:,.0f} volume")
    realized = r["realized"] or 0
    closed = r["closed"] or 0
    r_style = pnl_style(realized)
    lines.append("")
    lines.append(f"Realized P&L: ${realized:+.2f} ({closed} closed)")

    text = Text("\n".join(lines))
    # Color the realized line
    return Panel(text, title="Trade Stats", border_style="cyan")


def build_symbol_coverage_panel(coverage):
    """Per-symbol freshness grid: one row per symbol, columns per timeframe."""
    # Pivot: symbol → {timeframe: {age_h, rows}}
    symbols = {}
    all_tfs = set()
    for r in coverage:
        sym = r["symbol"]
        tf = r["timeframe"]
        all_tfs.add(tf)
        if sym not in symbols:
            symbols[sym] = {}
        symbols[sym][tf] = r

    # Consistent timeframe column order
    tf_order = [tf for tf in ["5m", "1h", "4h", "1d"] if tf in all_tfs]

    table = Table(show_header=True, header_style="bold cyan", box=None, pad_edge=False)
    table.add_column("Symbol", style="bold")
    for tf in tf_order:
        table.add_column(f"{tf} Age", justify="right")
        table.add_column(f"{tf} Rows", justify="right", style="dim")

    for sym in sorted(symbols.keys()):
        row_vals = [sym]
        for tf in tf_order:
            info = symbols[sym].get(tf)
            if info:
                age_h = info["age_h"]
                style = freshness_style(age_h)
                if age_h is not None and age_h < 1:
                    age_display = f"{age_h * 60:.0f}m"
                elif age_h is not None:
                    age_display = f"{age_h:.1f}h"
                else:
                    age_display = "n/a"
                row_vals.append(Text(age_display, style=style))
                row_vals.append(f"{info['rows']:,}")
            else:
                row_vals.append(Text("-", style="dim"))
                row_vals.append("-")
        table.add_row(*row_vals)

    return Panel(table, title="Per-Symbol Data Coverage", border_style="green")


def build_db_stats_panel(db_stats):
    """Database table row counts and file size."""
    table = Table(show_header=True, header_style="bold cyan", box=None, pad_edge=False)
    table.add_column("Table")
    table.add_column("Rows", justify="right")

    for t in db_stats["tables"]:
        rows_str = f"{t['rows']:,}" if t["rows"] is not None else "error"
        style = "dim" if t["rows"] == 0 else ""
        table.add_row(Text(t["table"], style=style), Text(rows_str, style=style))

    db_size = db_stats.get("db_size_bytes")
    if db_size:
        if db_size >= 1_073_741_824:
            size_str = f"{db_size / 1_073_741_824:.1f} GB"
        else:
            size_str = f"{db_size / 1_048_576:.0f} MB"
        table.add_row("", "")
        table.add_row(Text("DB file size", style="bold"), size_str)

    return Panel(table, title="Database Stats", border_style="cyan")


def build_timers_panel(timers):
    table = Table(show_header=True, header_style="bold cyan", box=None, pad_edge=False)
    table.add_column("Name")
    table.add_column("Status")
    table.add_column("Last", justify="right", style="dim")
    table.add_column("Next", justify="right")

    for t in timers:
        status = t["status"]
        if status == "running":
            style = "bold green"
        elif status == "active":
            style = "green"
        elif status in ("inactive", "dead"):
            style = "dim"
        else:
            style = "red"
        kind_prefix = "" if t["kind"] == "timer" else "[WS] "
        table.add_row(
            kind_prefix + t["name"],
            Text(status, style=style),
            t.get("last_run", ""),
            t.get("next_run", ""),
        )
    return Panel(table, title="Services & Timers", border_style="cyan")


# ── Layout Builders ───────────────────────────────────────────────────────


def build_portfolio_dashboard():
    """Portfolio & trading view: portfolios, positions, trades, signals, strategies."""
    conn = get_conn()
    try:
        portfolios = query_portfolios(conn)
        trades = query_recent_trades(conn)
        signals = query_recent_signals(conn)
        strategies = query_strategy_performance(conn)
        # Note: trade_stats and risk_events queries removed as they're not displayed
    finally:
        conn.close()

    layout = Layout()
    layout.split_column(
        Layout(build_header_with_view("PORTFOLIO"), name="header", size=1),
        Layout(name="body"),
    )

    layout["body"].split_column(
        Layout(name="top"),
        Layout(name="middle"),
        Layout(name="bottom"),
    )

    # Top: portfolios only (full width)
    layout["top"].split_row(
        Layout(build_portfolio_panel(portfolios), name="portfolios"),
    )

    # Middle: positions + strategy performance
    layout["middle"].split_row(
        Layout(build_positions_panel(portfolios), name="positions", ratio=3),
        Layout(build_strategy_panel(strategies), name="strategies", ratio=2),
    )

    # Bottom: trades + signals (expanded, no risk)
    layout["bottom"].split_row(
        Layout(build_trades_panel(trades), name="trades", ratio=1),
        Layout(build_signals_panel(signals), name="signals", ratio=1),
    )

    return layout


def build_system_dashboard():
    """Data engineering & system view: feed freshness, symbol coverage, timers, DB stats."""
    conn = get_conn()
    try:
        feeds = query_data_freshness(conn)
        coverage = query_symbol_coverage(conn)
        db_stats = query_db_stats(conn)
    finally:
        conn.close()
    timers = query_timer_status()

    layout = Layout()
    layout.split_column(
        Layout(build_header_with_view("SYSTEM"), name="header", size=1),
        Layout(name="body"),
    )

    layout["body"].split_row(
        Layout(name="left", ratio=3),
        Layout(name="right", ratio=2),
    )

    # Left: feed freshness on top, symbol coverage below (gets the most space)
    layout["left"].split_column(
        Layout(build_freshness_panel(feeds), name="freshness"),
        Layout(build_symbol_coverage_panel(coverage), name="coverage", ratio=3),
    )

    # Right: timers on top, DB stats below
    layout["right"].split_column(
        Layout(build_timers_panel(timers), name="timers", ratio=3),
        Layout(build_db_stats_panel(db_stats), name="db_stats"),
    )

    return layout


def build_header_with_view(view_name):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return Text(f"  EDORAS — {view_name}    {now}", style="bold white on dark_blue")


# Keep old name as alias for backwards compatibility
def build_dashboard():
    return build_portfolio_dashboard()


def main():
    parser = argparse.ArgumentParser(description="Edoras Trading Dashboard")
    parser.add_argument("--once", action="store_true", help="Render once and exit")
    parser.add_argument("-r", "--refresh", type=int, default=30, help="Refresh interval in seconds")
    parser.add_argument(
        "--view",
        choices=["portfolio", "system"],
        default="portfolio",
        help="Dashboard view: portfolio (trading) or system (data engineering)",
    )
    parser.add_argument("--db", type=str, help="Path to crypto_data.db")
    args = parser.parse_args()

    global DB_PATH
    if args.db:
        DB_PATH = Path(args.db)

    views = ["portfolio", "system"]
    current_idx = views.index(args.view)

    def render():
        if views[current_idx] == "portfolio":
            return build_portfolio_dashboard()
        else:
            return build_system_dashboard()

    console = Console()

    if args.once:
        console.print(render())
        return

    # Live mode with Tab to switch views
    # Live mode: render and update periodically. Exit with Ctrl+C.
    try:
        with Live(render(), console=console, screen=True) as live:
            while True:
                time.sleep(args.refresh)
                live.update(render())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
