#!/usr/bin/env python3
"""
Edoras Report Engine — Generates PDF reports for human review.

Reports are written to ~/.openclaw/workspace/reports/<category>/
and delivered via Telegram as document attachments.

Usage:
    python3 report_engine.py positions         # Daily position report
    python3 report_engine.py portfolio         # Portfolio summary
    python3 report_engine.py trades            # Trade activity (last 24h)
    python3 report_engine.py signals           # Signal analysis (last 24h)
    python3 report_engine.py market            # Market intelligence snapshot
    python3 report_engine.py risk              # Risk exposure report
    python3 report_engine.py performance       # Weekly performance digest
    python3 report_engine.py all               # Generate all reports
"""

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import get_account_ids

from fpdf import FPDF

DB_PATH = Path(__file__).parent / "crypto_data.db"


def _account_filter(table_alias: str, portfolio_id: int) -> tuple:
    """Return (SQL fragment, params) for filtering by portfolio's accounts."""
    ids = get_account_ids(portfolio_id, db_path=str(DB_PATH))
    if ids:
        placeholders = ','.join('?' * len(ids))
        return f"{table_alias}.account_id IN ({placeholders})", ids
    return f"{table_alias}.portfolio_id = ?", [portfolio_id]
REPORTS_DIR = Path(__file__).parent.parent.parent / "reports"

# ─── Colors ──────────────────────────────────────────────────────────────────

C_BG = (15, 18, 25)           # dark background
C_CARD = (22, 27, 38)         # card/section background
C_HEADER = (99, 102, 241)     # indigo header
C_TEXT = (220, 220, 230)      # light text
C_MUTED = (140, 145, 160)     # muted/secondary text
C_GREEN = (52, 211, 153)      # profit / positive
C_RED = (248, 113, 113)       # loss / negative
C_YELLOW = (251, 191, 36)     # warning
C_WHITE = (255, 255, 255)
C_ROW_EVEN = (22, 27, 38)
C_ROW_ODD = (28, 33, 46)
C_TABLE_HEADER = (35, 40, 58)


# ─── PDF Base ────────────────────────────────────────────────────────────────

FONT_DIR = "/usr/share/fonts/truetype/dejavu"


class EdorasPDF(FPDF):
    """Custom PDF with Edoras branding and dark theme."""

    def __init__(self, title="Edoras Report"):
        super().__init__()
        self.report_title = title
        self.set_auto_page_break(auto=True, margin=15)
        self.add_font("DejaVu", "", f"{FONT_DIR}/DejaVuSans.ttf")
        self.add_font("DejaVu", "B", f"{FONT_DIR}/DejaVuSans-Bold.ttf")
        self.add_font("DejaVu", "I", f"{FONT_DIR}/DejaVuSans-Oblique.ttf")

    def header(self):
        self.set_fill_color(*C_BG)
        self.rect(0, 0, 210, 297, "F")
        self.set_font("DejaVu", "B", 16)
        self.set_text_color(*C_HEADER)
        self.cell(0, 10, self.report_title, new_x="LMARGIN", new_y="NEXT")
        self.set_font("DejaVu", "", 8)
        self.set_text_color(*C_MUTED)
        self.cell(0, 5, f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                  new_x="LMARGIN", new_y="NEXT")
        self.ln(4)

    def footer(self):
        self.set_y(-12)
        self.set_font("DejaVu", "I", 7)
        self.set_text_color(*C_MUTED)
        self.cell(0, 10, f"Edoras Trading System  |  Page {self.page_no()}", align="C")

    def section_title(self, title):
        self.ln(3)
        self.set_font("DejaVu", "B", 11)
        self.set_text_color(*C_HEADER)
        self.cell(0, 7, title, new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(*C_HEADER)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(3)

    def kv_line(self, label, value, value_color=None):
        self.set_font("DejaVu", "B", 9)
        self.set_text_color(*C_MUTED)
        self.cell(55, 5, label, new_x="END")
        self.set_font("DejaVu", "", 9)
        self.set_text_color(*(value_color or C_TEXT))
        self.cell(0, 5, str(value), new_x="LMARGIN", new_y="NEXT")

    def pnl_color(self, val):
        if val > 0:
            return C_GREEN
        elif val < 0:
            return C_RED
        return C_TEXT

    def _fit_text(self, text, max_width):
        """Truncate text with ellipsis if it exceeds the cell width."""
        if self.get_string_width(text) <= max_width - 1:
            return text
        while len(text) > 1 and self.get_string_width(text + "…") > max_width - 1:
            text = text[:-1]
        return text + "…"

    def table(self, headers, rows, col_widths=None, col_aligns=None):
        """Draw a formatted table with alternating row colors."""
        if not col_widths:
            usable = 190
            col_widths = [usable / len(headers)] * len(headers)
        if not col_aligns:
            col_aligns = ["L"] * len(headers)

        # Header row
        self.set_font("DejaVu", "B", 7)
        self.set_fill_color(*C_TABLE_HEADER)
        self.set_text_color(*C_MUTED)
        for i, h in enumerate(headers):
            self.cell(col_widths[i], 6, self._fit_text(h, col_widths[i]),
                      border=0, fill=True, align=col_aligns[i], new_x="END")
        self.ln()

        # Data rows
        self.set_font("DejaVu", "", 7)
        for row_idx, row in enumerate(rows):
            if self.get_y() > 270:
                self.add_page()
            bg = C_ROW_EVEN if row_idx % 2 == 0 else C_ROW_ODD
            self.set_fill_color(*bg)
            for i, cell_data in enumerate(row):
                text = str(cell_data["text"]) if isinstance(cell_data, dict) else str(cell_data)
                color = cell_data.get("color", C_TEXT) if isinstance(cell_data, dict) else C_TEXT
                self.set_text_color(*color)
                self.cell(col_widths[i], 5.5, self._fit_text(text, col_widths[i]),
                          border=0, fill=True, align=col_aligns[i], new_x="END")
            self.ln()

    def stat_cards(self, cards):
        """Render a row of stat cards. cards = [(label, value, color), ...]"""
        card_w = 190 / len(cards) if cards else 190
        y_start = self.get_y()
        for label, value, color in cards:
            self.set_fill_color(*C_CARD)
            x = self.get_x()
            self.rect(x, y_start, card_w - 2, 16, "F")
            self.set_xy(x + 2, y_start + 1)
            self.set_font("DejaVu", "", 7)
            self.set_text_color(*C_MUTED)
            self.cell(card_w - 6, 4, label)
            self.set_xy(x + 2, y_start + 6)
            self.set_font("DejaVu", "B", 11)
            self.set_text_color(*color)
            self.cell(card_w - 6, 8, str(value))
            self.set_xy(x + card_w - 2, y_start)
        self.set_y(y_start + 19)

    def no_data(self, msg="No data available."):
        self.set_font("DejaVu", "I", 9)
        self.set_text_color(*C_MUTED)
        self.cell(0, 8, msg, new_x="LMARGIN", new_y="NEXT")


# ─── Database ────────────────────────────────────────────────────────────────

def get_conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _save_pdf(pdf, category, filename):
    out_dir = REPORTS_DIR / category
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / filename
    pdf.output(str(path))
    print(f"  -> {path}")
    return path


def _today():
    return datetime.now().strftime("%Y-%m-%d")


def _trade_source(decision_context):
    if not decision_context:
        return "unknown"
    try:
        ctx = json.loads(decision_context)
        sig_type = ctx.get("signal_type")
        exit_reason = ctx.get("exit_reason")
        if exit_reason == "llm_signal" or sig_type == "llm":
            return "llm_agent"
        if sig_type:
            return f"signal/{sig_type}"
        return "unknown"
    except Exception:
        return "unknown"


def _fmt_price(val):
    if val is None:
        return "-"
    if abs(val) >= 1:
        return f"${val:,.2f}"
    return f"${val:.6g}"


def _fmt_pct(val):
    if val is None:
        return "-"
    return f"{val:+.2f}%"


# ═════════════════════════════════════════════════════════════════════════════
# POSITION REPORT
# ═════════════════════════════════════════════════════════════════════════════

def report_positions():
    conn = get_conn()
    c = conn.cursor()
    date = _today()

    c.execute("SELECT id, name, mode, initial_capital FROM portfolios WHERE is_active = 1")
    portfolios = c.fetchall()

    pdf = EdorasPDF(f"Position Report — {date}")
    pdf.add_page()

    for pf in portfolios:
        pid = pf["id"]
        # Phase 3: query via accounts
        filt, params = _account_filter("p", pid)
        c.execute(f"""SELECT p.symbol, p.quantity, p.entry_price, p.current_price,
                            p.pnl, p.pnl_percent, p.entry_time
                     FROM positions p WHERE {filt} AND p.status = 'open'
                     ORDER BY ABS(p.quantity * p.current_price) DESC""", params)
        positions = c.fetchall()

        filt_t, params_t = _account_filter("t", pid)
        c.execute(f"SELECT t.cash_after FROM trades t WHERE {filt_t} ORDER BY t.id DESC LIMIT 1", params_t)
        row = c.fetchone()
        cash = row["cash_after"] if row else pf["initial_capital"]

        pdf.section_title(f"{pf['name']} ({pf['mode'].title()} Portfolio)")

        if not positions:
            pdf.no_data("No open positions.")
            continue

        total_invested = sum(p["quantity"] * (p["current_price"] or p["entry_price"]) for p in positions)
        total_pnl = sum(p["pnl"] or 0 for p in positions)
        total_value = cash + total_invested
        total_return = ((total_value / pf["initial_capital"]) - 1) * 100

        pdf.stat_cards([
            ("Total Value", f"${total_value:,.2f}", C_WHITE),
            ("Cash", f"${cash:,.2f}", C_TEXT),
            ("Open P&L", f"${total_pnl:+,.2f}", pdf.pnl_color(total_pnl)),
            ("All-Time", _fmt_pct(total_return), pdf.pnl_color(total_return)),
        ])

        headers = ["Symbol", "Qty", "Entry", "Current", "Value", "P&L", "P&L %", "Since"]
        widths = [24, 24, 24, 24, 22, 22, 20, 30]
        aligns = ["L", "R", "R", "R", "R", "R", "R", "L"]
        rows = []
        for p in positions:
            val = p["quantity"] * (p["current_price"] or p["entry_price"])
            pnl = p["pnl"] or 0
            pnl_pct = p["pnl_percent"] or 0
            held = (p["entry_time"] or "-")[:10]
            rows.append([
                p["symbol"],
                f"{p['quantity']:.6g}",
                _fmt_price(p["entry_price"]),
                _fmt_price(p["current_price"]),
                f"${val:.2f}",
                {"text": f"${pnl:+.2f}", "color": pdf.pnl_color(pnl)},
                {"text": _fmt_pct(pnl_pct), "color": pdf.pnl_color(pnl_pct)},
                held,
            ])
        pdf.table(headers, rows, widths, aligns)

    conn.close()
    return _save_pdf(pdf, "positions", f"{date}.pdf")


# ═════════════════════════════════════════════════════════════════════════════
# PORTFOLIO REPORT
# ═════════════════════════════════════════════════════════════════════════════

def report_portfolio():
    conn = get_conn()
    c = conn.cursor()
    date = _today()

    # Phase 3: paper_snapshots still uses portfolio_id (not in trades/positions scope)
    c.execute("""SELECT date, portfolio_value, cash, num_positions
                 FROM paper_snapshots WHERE portfolio_id = 1
                 ORDER BY date DESC LIMIT 14""")
    snapshots = list(reversed(c.fetchall()))

    # Phase 3: positions and trades via accounts
    filt_p, params_p = _account_filter("p", 1)
    c.execute(f"""SELECT p.symbol, p.quantity, p.current_price
                 FROM positions p WHERE {filt_p} AND p.status = 'open'""", params_p)
    positions = c.fetchall()

    filt_t, params_t = _account_filter("t", 1)
    c.execute(f"SELECT t.cash_after FROM trades t WHERE {filt_t} ORDER BY t.id DESC LIMIT 1", params_t)
    row = c.fetchone()
    cash = row["cash_after"] if row else 1000

    total_invested = sum(p["quantity"] * (p["current_price"] or 0) for p in positions)
    total_value = cash + total_invested
    all_time = ((total_value / 1000) - 1) * 100

    pdf = EdorasPDF(f"Portfolio Summary — {date}")
    pdf.add_page()

    pdf.section_title("Current State")
    pdf.stat_cards([
        ("Total Value", f"${total_value:,.2f}", C_WHITE),
        ("Cash", f"${cash:,.2f} ({cash/total_value*100:.1f}%)", C_TEXT),
        ("Invested", f"${total_invested:,.2f}", C_TEXT),
        ("All-Time", _fmt_pct(all_time), pdf.pnl_color(all_time)),
    ])

    # Allocation table
    pdf.section_title("Allocation Breakdown")
    if positions:
        headers = ["Asset", "Value", "Allocation"]
        widths = [70, 60, 60]
        aligns = ["L", "R", "R"]
        rows = []
        for p in sorted(positions, key=lambda x: x["quantity"] * (x["current_price"] or 0), reverse=True):
            val = p["quantity"] * (p["current_price"] or 0)
            pct = (val / total_value * 100) if total_value > 0 else 0
            rows.append([p["symbol"], f"${val:.2f}", f"{pct:.1f}%"])
        rows.append([{"text": "Cash", "color": C_MUTED}, f"${cash:.2f}", f"{cash/total_value*100:.1f}%"])
        pdf.table(headers, rows, widths, aligns)
    else:
        pdf.no_data()

    # Value history
    pdf.section_title("Value History (14-day)")
    if snapshots:
        headers = ["Date", "Value", "Cash", "Positions", "Change"]
        widths = [38, 38, 38, 30, 46]
        aligns = ["L", "R", "R", "C", "R"]
        rows = []
        prev = None
        for s in snapshots:
            chg = s["portfolio_value"] - prev if prev else 0
            chg_str = f"${chg:+.2f}" if prev else "-"
            prev = s["portfolio_value"]
            rows.append([
                s["date"],
                f"${s['portfolio_value']:.2f}",
                f"${s['cash']:.2f}",
                str(s["num_positions"]),
                {"text": chg_str, "color": pdf.pnl_color(chg)} if prev else chg_str,
            ])
        pdf.table(headers, rows, widths, aligns)
    else:
        pdf.no_data()

    conn.close()
    return _save_pdf(pdf, "portfolio", f"{date}.pdf")


# ═════════════════════════════════════════════════════════════════════════════
# TRADE REPORT
# ═════════════════════════════════════════════════════════════════════════════

def report_trades():
    conn = get_conn()
    c = conn.cursor()
    date = _today()
    cutoff = (datetime.now() - timedelta(hours=24)).isoformat()

    c.execute("""SELECT id, symbol, side, quantity, price, amount_usd, fee,
                        decision_context, created_at
                 FROM trades WHERE created_at > ? ORDER BY created_at""", (cutoff,))
    trades = c.fetchall()

    c.execute("""SELECT symbol, entry_date, exit_date, entry_price, exit_price,
                        outcome_pct, outcome_usd, holding_hours, signal_type, exit_reason
                 FROM trade_outcomes WHERE exit_date > ? ORDER BY exit_date""", (cutoff,))
    outcomes = c.fetchall()

    buys = [t for t in trades if t["side"] == "BUY"]
    sells = [t for t in trades if t["side"] == "SELL"]
    total_bought = sum(t["amount_usd"] for t in buys)
    total_sold = sum(t["amount_usd"] for t in sells)
    total_fees = sum(t["fee"] for t in trades)
    net_flow = total_sold - total_bought

    sources = {}
    for t in trades:
        src = _trade_source(t["decision_context"])
        sources[src] = sources.get(src, 0) + 1

    pdf = EdorasPDF(f"Trade Activity — {date}")
    pdf.add_page()

    pdf.section_title("Summary")
    pdf.stat_cards([
        ("Trades", f"{len(trades)} ({len(buys)}B / {len(sells)}S)", C_WHITE),
        ("Volume", f"${total_bought + total_sold:,.2f}", C_TEXT),
        ("Fees", f"${total_fees:.4f}", C_MUTED),
        ("Net Flow", f"${net_flow:+,.2f}", pdf.pnl_color(net_flow)),
    ])

    if sources:
        pdf.set_font("DejaVu", "", 8)
        pdf.set_text_color(*C_MUTED)
        src_str = "Sources: " + ", ".join(f"{k}: {v}" for k, v in sorted(sources.items()))
        pdf.cell(0, 5, src_str, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

    # Trades table
    pdf.section_title("Trades")
    if trades:
        headers = ["Time", "Side", "Symbol", "Qty", "Price", "USD", "Source"]
        widths = [22, 14, 28, 28, 28, 24, 46]
        aligns = ["L", "C", "L", "R", "R", "R", "L"]
        rows = []
        for t in trades:
            src = _trade_source(t["decision_context"])
            side_color = C_GREEN if t["side"] == "BUY" else C_RED
            rows.append([
                t["created_at"][11:19],
                {"text": t["side"], "color": side_color},
                t["symbol"],
                f"{t['quantity']:.6g}",
                _fmt_price(t["price"]),
                f"${t['amount_usd']:.2f}",
                src,
            ])
        pdf.table(headers, rows, widths, aligns)
    else:
        pdf.no_data("No trades in last 24 hours.")

    # Closed positions
    pdf.section_title("Closed Positions")
    if outcomes:
        wins = sum(1 for o in outcomes if (o["outcome_pct"] or 0) > 0)
        net = sum(o["outcome_usd"] or 0 for o in outcomes)
        pdf.stat_cards([
            ("Win Rate", f"{wins}/{len(outcomes)} ({100*wins/len(outcomes):.0f}%)",
             C_GREEN if wins > len(outcomes)/2 else C_RED),
            ("Net P&L", f"${net:+,.2f}", pdf.pnl_color(net)),
        ])

        headers = ["Symbol", "Entry", "Exit", "P&L %", "P&L $", "Held", "Signal", "Exit"]
        widths = [24, 22, 22, 20, 22, 18, 30, 32]
        aligns = ["L", "R", "R", "R", "R", "R", "L", "L"]
        rows = []
        for o in outcomes:
            pnl_pct = o["outcome_pct"] or 0
            pnl_usd = o["outcome_usd"] or 0
            rows.append([
                o["symbol"],
                _fmt_price(o["entry_price"]),
                _fmt_price(o["exit_price"]),
                {"text": _fmt_pct(pnl_pct), "color": pdf.pnl_color(pnl_pct)},
                {"text": f"${pnl_usd:+.2f}", "color": pdf.pnl_color(pnl_usd)},
                f"{o['holding_hours']:.1f}h",
                o["signal_type"] or "-",
                o["exit_reason"] or "-",
            ])
        pdf.table(headers, rows, widths, aligns)
    else:
        pdf.no_data("No positions closed in last 24 hours.")

    conn.close()
    return _save_pdf(pdf, "trades", f"{date}.pdf")


# ═════════════════════════════════════════════════════════════════════════════
# SIGNAL REPORT
# ═════════════════════════════════════════════════════════════════════════════

def report_signals():
    conn = get_conn()
    c = conn.cursor()
    date = _today()
    cutoff = (datetime.now() - timedelta(hours=24)).isoformat()

    c.execute("""SELECT strategy_name, symbol, action, strength, reason,
                        was_executed, outcome_pct, adx, rsi, signal_time
                 FROM strategy_signals_log WHERE signal_time > ?
                 ORDER BY signal_time""", (cutoff,))
    signals = c.fetchall()

    total = len(signals)
    executed = sum(1 for s in signals if s["was_executed"])
    buy_signals = [s for s in signals if s["action"] == "BUY"]
    sell_signals = [s for s in signals if s["action"] == "SELL"]

    pdf = EdorasPDF(f"Signal Analysis — {date}")
    pdf.add_page()

    pdf.section_title("Summary")
    if total > 0:
        pdf.stat_cards([
            ("Total Signals", str(total), C_WHITE),
            ("BUY / SELL", f"{len(buy_signals)} / {len(sell_signals)}", C_TEXT),
            ("Executed", f"{executed} ({100*executed/total:.0f}%)", C_GREEN),
            ("Skipped", str(total - executed), C_MUTED),
        ])
    else:
        pdf.no_data("No signals generated in last 24 hours.")
        conn.close()
        return _save_pdf(pdf, "signals", f"{date}.pdf")

    # By strategy
    strat_counts = {}
    for s in signals:
        name = s["strategy_name"]
        if name not in strat_counts:
            strat_counts[name] = {"total": 0, "executed": 0, "strengths": []}
        strat_counts[name]["total"] += 1
        strat_counts[name]["strengths"].append(s["strength"])
        if s["was_executed"]:
            strat_counts[name]["executed"] += 1

    pdf.section_title("By Strategy")
    headers = ["Strategy", "Signals", "Executed", "Avg Strength"]
    widths = [60, 40, 40, 50]
    aligns = ["L", "C", "C", "C"]
    rows = []
    for name, data in sorted(strat_counts.items()):
        avg = sum(data["strengths"]) / len(data["strengths"])
        rows.append([name, str(data["total"]), str(data["executed"]), f"{avg:.1f}"])
    pdf.table(headers, rows, widths, aligns)

    # All signals
    pdf.section_title("All Signals")
    headers = ["Time", "Action", "Symbol", "Strength", "ADX", "RSI", "Strategy", "Exec"]
    widths = [18, 14, 24, 22, 18, 18, 46, 18]
    aligns = ["L", "C", "L", "R", "R", "R", "L", "C"]
    rows = []
    for s in signals:
        action_color = C_GREEN if s["action"] == "BUY" else C_RED
        adx = f"{s['adx']:.0f}" if s["adx"] else "-"
        rsi = f"{s['rsi']:.0f}" if s["rsi"] else "-"
        ex = "Yes" if s["was_executed"] else "-"
        rows.append([
            s["signal_time"][11:16],
            {"text": s["action"], "color": action_color},
            s["symbol"],
            f"{s['strength']:.1f}",
            adx, rsi,
            s["strategy_name"],
            {"text": ex, "color": C_GREEN if s["was_executed"] else C_MUTED},
        ])
    pdf.table(headers, rows, widths, aligns)

    conn.close()
    return _save_pdf(pdf, "signals", f"{date}.pdf")


# ═════════════════════════════════════════════════════════════════════════════
# MARKET INTELLIGENCE REPORT
# ═════════════════════════════════════════════════════════════════════════════

def report_market():
    conn = get_conn()
    c = conn.cursor()
    date = _today()

    c.execute("SELECT * FROM market_regime_detailed ORDER BY timestamp DESC LIMIT 1")
    regime = c.fetchone()

    c.execute("""SELECT symbol_a, symbol_b, correlation FROM correlations
                 WHERE date = (SELECT MAX(date) FROM correlations)
                 ORDER BY ABS(correlation) DESC LIMIT 10""")
    corrs = c.fetchall()

    c.execute("""SELECT symbol, score, confidence, summary
                 FROM sentiment_scores
                 WHERE timestamp > ? ORDER BY timestamp DESC LIMIT 10""",
              (int(time.time()) - 86400,))
    sentiments = c.fetchall()

    c.execute("""SELECT i.symbol, i.sma_20, i.sma_50, i.sma_200, i.rsi_14, i.adx_14,
                        c.close as last_price
                 FROM indicators i
                 JOIN candlesticks c ON c.symbol = i.symbol AND c.timeframe = i.timeframe
                    AND c.timestamp = i.timestamp
                 WHERE i.timeframe = '1d'
                   AND i.symbol IN ('BTC-USD','ETH-USD','SOL-USD','XRP-USD')
                 AND i.timestamp = (SELECT MAX(timestamp) FROM indicators
                                    WHERE symbol = i.symbol AND timeframe = '1d')""")
    levels = c.fetchall()

    pdf = EdorasPDF(f"Market Intelligence — {date}")
    pdf.add_page()

    # Regime
    pdf.section_title("Market Regime")
    if regime:
        regime_name = regime["regime"] or "Unknown"
        vix = f"{regime['vix_value']:.1f}" if regime["vix_value"] else "N/A"
        regime_color = C_GREEN if "low" in regime_name.lower() else C_RED if "high" in regime_name.lower() else C_YELLOW
        cards = [("Regime", regime_name, regime_color), ("VIX", vix, C_TEXT)]
        if regime["btc_spy_corr"]:
            cards.append(("BTC-SPY Corr", f"{regime['btc_spy_corr']:.3f}", C_TEXT))
        if regime["btc_qqq_corr"]:
            cards.append(("BTC-QQQ Corr", f"{regime['btc_qqq_corr']:.3f}", C_TEXT))
        pdf.stat_cards(cards)
    else:
        pdf.no_data("No regime data available.")

    # Key levels
    pdf.section_title("Key Price Levels (Daily)")
    if levels:
        headers = ["Symbol", "Price", "SMA-20", "SMA-50", "SMA-200", "RSI", "ADX", "Trend"]
        widths = [22, 26, 26, 26, 26, 18, 18, 28]
        aligns = ["L", "R", "R", "R", "R", "R", "R", "C"]
        rows = []
        for lv in levels:
            price = lv["last_price"] or 0
            sma50 = lv["sma_50"] or 0
            trend = "Above" if price > sma50 else "Below" if sma50 > 0 else "-"
            trend_color = C_GREEN if trend == "Above" else C_RED if trend == "Below" else C_MUTED
            rows.append([
                lv["symbol"],
                f"${price:,.2f}",
                f"${lv['sma_20'] or 0:,.2f}",
                f"${sma50:,.2f}",
                f"${lv['sma_200'] or 0:,.2f}",
                f"{lv['rsi_14'] or 0:.1f}",
                f"{lv['adx_14'] or 0:.1f}",
                {"text": trend, "color": trend_color},
            ])
        pdf.table(headers, rows, widths, aligns)
    else:
        pdf.no_data()

    # Correlations
    pdf.section_title("Top Correlations")
    if corrs:
        headers = ["Pair", "Correlation"]
        widths = [120, 70]
        aligns = ["L", "R"]
        rows = []
        for co in corrs:
            corr_val = co["correlation"]
            rows.append([
                f"{co['symbol_a']}  /  {co['symbol_b']}",
                {"text": f"{corr_val:+.3f}", "color": C_GREEN if corr_val > 0 else C_RED},
            ])
        pdf.table(headers, rows, widths, aligns)
    else:
        pdf.no_data()

    # Sentiment
    pdf.section_title("Recent Sentiment")
    if sentiments:
        headers = ["Symbol", "Score", "Confidence", "Summary"]
        widths = [28, 22, 28, 112]
        aligns = ["L", "R", "R", "L"]
        rows = []
        for s in sentiments:
            score = s["score"] or 0
            rows.append([
                s["symbol"],
                {"text": f"{score:+.2f}", "color": pdf.pnl_color(score)},
                f"{(s['confidence'] or 0):.0%}",
                (s["summary"] or "")[:65],
            ])
        pdf.table(headers, rows, widths, aligns)
    else:
        pdf.no_data()

    conn.close()
    return _save_pdf(pdf, "market", f"{date}.pdf")


# ═════════════════════════════════════════════════════════════════════════════
# RISK REPORT
# ═════════════════════════════════════════════════════════════════════════════

def report_risk():
    conn = get_conn()
    c = conn.cursor()
    date = _today()

    # Phase 3: positions via accounts
    filt_p, params_p = _account_filter("p", 1)
    c.execute(f"""SELECT p.symbol, p.quantity, p.entry_price, p.current_price,
                        p.pnl_percent, p.stop_loss_price, p.trailing_stop_price, p.entry_time
                 FROM positions p WHERE {filt_p} AND p.status = 'open'
                 ORDER BY p.pnl_percent ASC""", params_p)
    positions = c.fetchall()

    c.execute("""SELECT symbol, event_type, trigger_price, current_price,
                        action_taken, reason, created_at
                 FROM risk_events
                 WHERE created_at > datetime('now', '-7 days')
                 ORDER BY created_at DESC LIMIT 15""")
    events = c.fetchall()

    risk_state = {}
    risk_file = Path(__file__).parent / "risk_state.json"
    if risk_file.exists():
        try:
            risk_state = json.loads(risk_file.read_text())
        except Exception:
            pass

    filt_t, params_t = _account_filter("t", 1)
    c.execute(f"SELECT t.cash_after FROM trades t WHERE {filt_t} ORDER BY t.id DESC LIMIT 1", params_t)
    row = c.fetchone()
    cash = row["cash_after"] if row else 1000
    total_value = cash + sum(p["quantity"] * (p["current_price"] or p["entry_price"]) for p in positions)
    cash_pct = (cash / total_value * 100) if total_value > 0 else 100
    max_pos = max((p["quantity"] * (p["current_price"] or p["entry_price"]) / total_value * 100
                   for p in positions), default=0)

    cb_active = risk_state.get("circuit_breaker_active", False)

    pdf = EdorasPDF(f"Risk Exposure — {date}")
    pdf.add_page()

    pdf.section_title("Portfolio Risk Summary")
    cash_color = C_RED if cash_pct < 5 else C_YELLOW if cash_pct < 10 else C_GREEN
    cb_color = C_RED if cb_active else C_GREEN
    pdf.stat_cards([
        ("Total Value", f"${total_value:,.2f}", C_WHITE),
        ("Cash Reserve", f"${cash:,.2f} ({cash_pct:.1f}%)", cash_color),
        ("Open Positions", str(len(positions)), C_TEXT),
        ("Circuit Breaker", "ACTIVE" if cb_active else "Inactive", cb_color),
    ])

    # Position risk detail
    pdf.section_title("Position Risk Detail")
    if positions:
        headers = ["Symbol", "Entry", "Current", "P&L %", "Weight", "Stop", "Dist to Stop"]
        widths = [24, 26, 26, 22, 22, 34, 36]
        aligns = ["L", "R", "R", "R", "R", "R", "R"]
        rows = []
        for p in positions:
            val = p["quantity"] * (p["current_price"] or p["entry_price"])
            weight = (val / total_value * 100) if total_value > 0 else 0
            pnl_pct = p["pnl_percent"] or 0
            stop = p["stop_loss_price"] or p["trailing_stop_price"]
            if stop and p["current_price"]:
                dist = ((p["current_price"] - stop) / p["current_price"]) * 100
                dist_str = f"{dist:.1f}%"
            else:
                dist_str = "-"
            stop_str = _fmt_price(stop) if stop else "None"
            weight_color = C_RED if weight > 25 else C_YELLOW if weight > 20 else C_TEXT
            rows.append([
                p["symbol"],
                _fmt_price(p["entry_price"]),
                _fmt_price(p["current_price"]),
                {"text": _fmt_pct(pnl_pct), "color": pdf.pnl_color(pnl_pct)},
                {"text": f"{weight:.1f}%", "color": weight_color},
                stop_str,
                dist_str,
            ])
        pdf.table(headers, rows, widths, aligns)
    else:
        pdf.no_data("No open positions.")

    # Risk events
    pdf.section_title("Recent Risk Events (7 days)")
    if events:
        headers = ["Time", "Symbol", "Event", "Action", "Reason"]
        widths = [34, 26, 34, 34, 62]
        aligns = ["L", "L", "L", "L", "L"]
        rows = []
        for e in events:
            rows.append([
                e["created_at"][:16],
                e["symbol"],
                e["event_type"],
                e["action_taken"] or "-",
                (e["reason"] or "-")[:40],
            ])
        pdf.table(headers, rows, widths, aligns)
    else:
        pdf.no_data("No risk events in period.")

    # Risk flags
    flags = []
    if cash_pct < 5:
        flags.append(("LOW CASH", f"Cash at {cash_pct:.1f}% — below 5% threshold", C_RED))
    if max_pos > 25:
        flags.append(("CONCENTRATION", f"Largest position at {max_pos:.1f}% — exceeds 25%", C_RED))
    if cb_active:
        flags.append(("CIRCUIT BREAKER", "Trading paused due to drawdown", C_RED))

    pdf.section_title("Risk Flags")
    if flags:
        for name, desc, color in flags:
            pdf.set_font("DejaVu", "B", 9)
            pdf.set_text_color(*color)
            pdf.cell(40, 6, name, new_x="END")
            pdf.set_font("DejaVu", "", 9)
            pdf.set_text_color(*C_TEXT)
            pdf.cell(0, 6, desc, new_x="LMARGIN", new_y="NEXT")
    else:
        pdf.set_font("DejaVu", "", 9)
        pdf.set_text_color(*C_GREEN)
        pdf.cell(0, 6, "No active risk flags.", new_x="LMARGIN", new_y="NEXT")

    conn.close()
    return _save_pdf(pdf, "risk", f"{date}.pdf")


# ═════════════════════════════════════════════════════════════════════════════
# PERFORMANCE REPORT (WEEKLY)
# ═════════════════════════════════════════════════════════════════════════════

def report_performance():
    conn = get_conn()
    c = conn.cursor()
    today = datetime.now()
    week_num = today.isocalendar()[1]
    year = today.isocalendar()[0]
    week_start = (today - timedelta(days=today.weekday())).strftime("%Y-%m-%d")
    cutoff_7d = (today - timedelta(days=7)).isoformat()
    cutoff_30d = (today - timedelta(days=30)).isoformat()

    c.execute("""SELECT symbol, outcome_pct, outcome_usd, holding_hours,
                        signal_type, exit_reason
                 FROM trade_outcomes WHERE exit_date > ?
                 ORDER BY exit_date""", (cutoff_7d,))
    outcomes_7d = c.fetchall()

    c.execute("""SELECT outcome_pct, outcome_usd, signal_type
                 FROM trade_outcomes WHERE exit_date > ?""", (cutoff_30d,))
    outcomes_30d = c.fetchall()

    c.execute("""SELECT signal_type,
                        COUNT(*) as cnt,
                        SUM(CASE WHEN outcome_pct > 0 THEN 1 ELSE 0 END) as wins,
                        AVG(outcome_pct) as avg_pct,
                        SUM(outcome_usd) as total_usd
                 FROM trade_outcomes WHERE exit_date > ?
                 GROUP BY signal_type""", (cutoff_30d,))
    by_signal = c.fetchall()

    # Phase 3: paper_snapshots still uses portfolio_id (not in trades/positions scope)
    c.execute("""SELECT date, portfolio_value FROM paper_snapshots
                 WHERE portfolio_id = 1 AND date >= ? ORDER BY date""", (cutoff_7d[:10],))
    snapshots = c.fetchall()

    total_7d = len(outcomes_7d)
    wins_7d = sum(1 for o in outcomes_7d if (o["outcome_pct"] or 0) > 0)
    net_7d = sum(o["outcome_usd"] or 0 for o in outcomes_7d)
    avg_hold_7d = sum(o["holding_hours"] or 0 for o in outcomes_7d) / total_7d if total_7d else 0

    total_30d = len(outcomes_30d)
    wins_30d = sum(1 for o in outcomes_30d if (o["outcome_pct"] or 0) > 0)
    net_30d = sum(o["outcome_usd"] or 0 for o in outcomes_30d)

    pdf = EdorasPDF(f"Performance — Week {week_num}, {year}")
    pdf.add_page()

    # Weekly summary
    pdf.section_title(f"Weekly Summary (7 days from {week_start})")
    if total_7d > 0:
        wr_7d = 100 * wins_7d / total_7d
        wr_color = C_GREEN if wr_7d >= 50 else C_RED
        pdf.stat_cards([
            ("Closed Trades", str(total_7d), C_WHITE),
            ("Win Rate", f"{wins_7d}/{total_7d} ({wr_7d:.0f}%)", wr_color),
            ("Net P&L", f"${net_7d:+,.2f}", pdf.pnl_color(net_7d)),
            ("Avg Hold", f"{avg_hold_7d:.1f}h", C_TEXT),
        ])
    else:
        pdf.no_data("No closed trades this week.")

    # 30-day summary
    pdf.section_title("30-Day Summary")
    if total_30d > 0:
        wr_30d = 100 * wins_30d / total_30d
        wr_color = C_GREEN if wr_30d >= 50 else C_RED
        pdf.stat_cards([
            ("Closed Trades", str(total_30d), C_WHITE),
            ("Win Rate", f"{wins_30d}/{total_30d} ({wr_30d:.0f}%)", wr_color),
            ("Net P&L", f"${net_30d:+,.2f}", pdf.pnl_color(net_30d)),
        ])
    else:
        pdf.no_data("No closed trades in last 30 days.")

    # By signal type
    pdf.section_title("Performance by Signal Type (30 days)")
    if by_signal:
        headers = ["Signal Type", "Trades", "Win Rate", "Avg Return", "Net P&L"]
        widths = [50, 30, 35, 35, 40]
        aligns = ["L", "C", "C", "R", "R"]
        rows = []
        for s in by_signal:
            wr = f"{100*s['wins']/s['cnt']:.0f}%" if s["cnt"] else "-"
            rows.append([
                s["signal_type"] or "unknown",
                str(s["cnt"]),
                wr,
                {"text": _fmt_pct(s["avg_pct"]), "color": pdf.pnl_color(s["avg_pct"] or 0)},
                {"text": f"${s['total_usd']:+.2f}", "color": pdf.pnl_color(s["total_usd"] or 0)},
            ])
        pdf.table(headers, rows, widths, aligns)
    else:
        pdf.no_data()

    # This week's trades
    pdf.section_title("This Week's Trades")
    if outcomes_7d:
        headers = ["Symbol", "P&L %", "P&L $", "Held", "Signal", "Exit"]
        widths = [30, 28, 28, 24, 40, 40]
        aligns = ["L", "R", "R", "R", "L", "L"]
        rows = []
        for o in outcomes_7d:
            pnl_pct = o["outcome_pct"] or 0
            pnl_usd = o["outcome_usd"] or 0
            rows.append([
                o["symbol"],
                {"text": _fmt_pct(pnl_pct), "color": pdf.pnl_color(pnl_pct)},
                {"text": f"${pnl_usd:+.2f}", "color": pdf.pnl_color(pnl_usd)},
                f"{o['holding_hours']:.1f}h",
                o["signal_type"] or "-",
                o["exit_reason"] or "-",
            ])
        pdf.table(headers, rows, widths, aligns)
    else:
        pdf.no_data("No closed trades this week.")

    # Portfolio value
    pdf.section_title("Portfolio Value (7 days)")
    if snapshots:
        headers = ["Date", "Portfolio Value"]
        widths = [80, 110]
        aligns = ["L", "R"]
        rows = [[s["date"], f"${s['portfolio_value']:,.2f}"] for s in snapshots]
        pdf.table(headers, rows, widths, aligns)
    else:
        pdf.no_data()

    conn.close()
    fname = f"weekly-{year}-W{week_num:02d}.pdf"
    return _save_pdf(pdf, "performance", fname)


# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════

COMMANDS = {
    "positions": report_positions,
    "portfolio": report_portfolio,
    "trades": report_trades,
    "signals": report_signals,
    "market": report_market,
    "risk": report_risk,
    "performance": report_performance,
}


def main():
    parser = argparse.ArgumentParser(
        prog="edoras-reports",
        description="Edoras Report Engine — Generate PDF reports for human review",
    )
    parser.add_argument("report", choices=list(COMMANDS.keys()) + ["all"],
                        help="Report type to generate")
    args = parser.parse_args()

    if args.report == "all":
        print("Generating all reports...")
        for name, fn in COMMANDS.items():
            print(f"\n  [{name}]")
            try:
                fn()
            except Exception as e:
                print(f"  ERROR: {e}")
    else:
        COMMANDS[args.report]()


if __name__ == "__main__":
    main()
