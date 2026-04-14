#!/usr/bin/env python3
"""
Telegram-friendly formatting helpers.

Telegram doesn't render markdown tables well (code blocks are hard to read
on mobile, pipe-delimited tables render as monospace blobs). This module
provides structured formatters that produce clean, readable output using
aligned plain text, bullet lists, and emoji-accented rows.

Usage:
    from reports.telegram_fmt import fmt_table, fmt_kv, fmt_positions, fmt_market_summary
"""

from typing import Dict, List, Optional, Union


# ── Table → stacked rows ────────────────────────────────────────────────

def fmt_table(rows: List[Dict], columns: List[str], labels: Optional[Dict[str, str]] = None,
              number_emoji: bool = True) -> str:
    """
    Convert tabular data into numbered stacked rows.
    Much more readable on Telegram than pipe-delimited tables.

    Args:
        rows: list of dicts (one per row)
        columns: keys to include from each dict
        labels: optional display names for keys (e.g. {"pnl_pct": "P&L"})
        number_emoji: use numbered emojis (1️⃣ 2️⃣ ...) vs plain numbers

    Example output:
        1️⃣ BTC-USD
           Price: $84,230.00
           Value: $420.15
           P&L: +2.3%

        2️⃣ ETH-USD
           Price: $3,120.00
           Value: $312.00
           P&L: -1.1%
    """
    if not rows:
        return "No data."

    labels = labels or {}
    number_emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]

    lines = []
    for i, row in enumerate(rows):
        # Row header — first column is the title
        title_key = columns[0]
        title = row.get(title_key, "—")
        if number_emoji and i < len(number_emojis):
            lines.append(f"{number_emojis[i]} {title}")
        else:
            lines.append(f"{i + 1}. {title}")

        # Remaining columns as indented key-value pairs
        for col in columns[1:]:
            label = labels.get(col, col.replace("_", " ").title())
            val = row.get(col, "—")
            lines.append(f"   {label}: {val}")

        lines.append("")  # blank line between rows

    return "\n".join(lines).rstrip()


# ── Key-value pairs ─────────────────────────────────────────────────────

def fmt_kv(data: Dict[str, Union[str, int, float]], bullet: str = "•") -> str:
    """
    Format a dict as a clean bullet list.

    Example output:
        • Total Value: $1,234.56
        • Daily Change: +$12.34 (+1.0%)
        • Positions: 5
    """
    lines = []
    for key, val in data.items():
        lines.append(f"{bullet} {key}: {val}")
    return "\n".join(lines)


# ── Position table (most common use case) ────────────────────────────────

def fmt_positions(positions: List[Dict], show_entry: bool = True) -> str:
    """
    Format portfolio positions for Telegram.

    Each position dict should have: symbol, quantity, avg_price, current_price,
    value, pnl, pnl_pct.

    Example output:
        1️⃣ BTC-USD
           Qty: 0.0050 @ $83,200 → $84,230
           Value: $421.15 (📈 +1.2%)

        2️⃣ ETH-USD
           Qty: 0.1000 @ $3,180 → $3,120
           Value: $312.00 (📉 -1.9%)
    """
    if not positions:
        return "No open positions."

    number_emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    lines = []

    for i, pos in enumerate(positions):
        sym = pos.get("symbol", "???")
        num = number_emojis[i] if i < len(number_emojis) else f"{i + 1}."
        lines.append(f"{num} {sym}")

        qty = pos.get("quantity", 0)
        avg = pos.get("avg_price", 0)
        cur = pos.get("current_price", 0)
        if show_entry:
            lines.append(f"   Qty: {qty:.4f} @ ${avg:,.2f} → ${cur:,.2f}")
        else:
            lines.append(f"   Qty: {qty:.4f} @ ${cur:,.2f}")

        value = pos.get("value", cur * qty)
        pnl_pct = pos.get("pnl_pct", 0)
        pnl_emoji = "📈" if pnl_pct >= 0 else "📉"
        lines.append(f"   Value: ${value:,.2f} ({pnl_emoji} {pnl_pct:+.1f}%)")
        lines.append("")

    return "\n".join(lines).rstrip()


# ── Market summary (replaces markdown table in news_digest) ──────────────

def fmt_market_summary(market_data: Dict[str, Dict]) -> str:
    """
    Format market indices for Telegram.

    Input: {"SPY": {"price": 520.5, "change": 1.2}, ...}

    Example output:
        📈 SPY: $520.50 (+1.20%)
        📉 QQQ: $438.00 (-0.45%)
        📈 BTC: $84,230 (+2.10%)
    """
    if not market_data:
        return "Market data unavailable."

    lines = []
    for name, data in market_data.items():
        if not data:
            continue
        price = data.get("price", 0)
        change = data.get("change", 0)
        emoji = "📈" if change >= 0 else "📉"
        lines.append(f"{emoji} {name}: ${price:,} ({change:+.2f}%)")
    return "\n".join(lines)


# ── Signal alerts ────────────────────────────────────────────────────────

def fmt_signals(signals: List[Dict]) -> str:
    """
    Format trading signals for Telegram.

    Each signal dict: symbol, action, strength, reason.

    Example output:
        🟢 BUY ETH-USD (strength: 72)
           BollingerReversion: price below lower band, RSI oversold

        🔴 SELL DOGE-USD (strength: 58)
           MultiSignal: MACD bearish crossover
    """
    if not signals:
        return "No active signals."

    lines = []
    for sig in signals:
        sym = sig.get("symbol", "???")
        action = sig.get("action", "?").upper()
        strength = sig.get("strength", 0)
        reason = sig.get("reason", "")

        if action == "BUY":
            emoji = "🟢"
        elif action == "SELL":
            emoji = "🔴"
        else:
            emoji = "⚪"

        lines.append(f"{emoji} {action} {sym} (strength: {strength:.0f})")
        if reason:
            lines.append(f"   {reason[:120]}")
        lines.append("")

    return "\n".join(lines).rstrip()


# ── Journal / performance stats ──────────────────────────────────────────

def fmt_performance(stats: List[Dict], group_key: str = "signal_type") -> str:
    """
    Format performance stats (by signal type or regime) for Telegram.

    Example output:
        BollingerReversion — 12 trades, 58.3% win, avg +1.2%
        MultiSignal — 8 trades, 50.0% win, avg +0.4%
        MACDCross — 5 trades, 40.0% win, avg -0.3%
    """
    if not stats:
        return "No trade history yet."

    lines = []
    for s in stats:
        name = s.get(group_key, "unknown")
        n = s.get("total_trades", 0)
        wr = s.get("win_rate", 0)
        avg = s.get("avg_return_pct", 0)
        lines.append(f"{name} — {n} trades, {wr:.1f}% win, avg {avg:+.2f}%")
    return "\n".join(lines)
