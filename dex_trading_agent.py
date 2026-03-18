#!/usr/bin/env python3
"""
DEX trading agent — automated trading for the Arwen portfolio.

Orchestrates:
  1. Data collection (dex_data_collector)
  2. Indicator computation (compute_all_indicators pipeline)
  3. DEX risk checks (dex_risk_rules)
  4. LLM-driven trade decisions with DEX-specific context
  5. Execution via dex_executor (Bankr API)

Does NOT modify any protected files (indicator_calculator, risk_manager,
signal_trading). Layers DEX logic on top.

Usage:
    python3 dex_trading_agent.py                # Full cycle
    python3 dex_trading_agent.py --dry-run      # Simulate without executing
    python3 dex_trading_agent.py --data-only    # Just collect data + compute indicators
"""

import argparse
import json
import logging
import os
import sqlite3
import sys
from datetime import datetime
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    DB_PATH, DEX_CONFIG, PORTFOLIO_ARWEN, TRADER_ALEPH, get_account_ids,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# DEX symbols we actively trade
DEX_TRADE_SYMBOLS = ["VVV-BASE", "BNKR-BASE"]
# DEX symbols we track for context but don't trade
DEX_CONTEXT_SYMBOLS = ["WETH-BASE", "USDC-BASE"]


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ── Phase 1: Data collection ──────────────────────────────────────────────

def update_dex_data():
    """Collect latest candles and metadata for all DEX tokens."""
    from dex_data_collector import DexDataCollector
    collector = DexDataCollector()
    collector.collect_all(backfill_days=1)


def compute_dex_indicators():
    """Run standard indicator computation for DEX symbols."""
    from compute_all_indicators import compute_all_indicators
    compute_all_indicators(DB_PATH)


# ── Phase 2: Context gathering ─────────────────────────────────────────────

def gather_dex_context() -> dict:
    """Gather all relevant context for DEX trading decisions."""
    conn = get_conn()
    ctx = {
        "timestamp": datetime.now().isoformat(),
        "portfolio_id": PORTFOLIO_ARWEN,
        "symbols": {},
        "wallet": {},
        "positions": {},
        "risk_warnings": [],
    }

    # Wallet balance
    try:
        from dex_executor import DexExecutor
        executor = DexExecutor()
        wallet = executor.get_wallet_summary()
        ctx["wallet"] = {
            "balances": wallet.get("balances", []),
            "evm_address": wallet.get("evm_address"),
        }
    except Exception as e:
        logger.warning(f"Could not fetch wallet: {e}")

    # Current positions (Phase 3: query via account_ids)
    account_ids = get_account_ids(PORTFOLIO_ARWEN, db_path=DB_PATH)
    if account_ids:
        placeholders = ','.join('?' * len(account_ids))
        positions = conn.execute(
            f"SELECT symbol, quantity, entry_price, current_price, pnl, pnl_percent "
            f"FROM positions WHERE account_id IN ({placeholders}) AND status = 'open'",
            account_ids,
        ).fetchall()
    else:
        positions = conn.execute(
            "SELECT symbol, quantity, entry_price, current_price, pnl, pnl_percent "
            "FROM positions WHERE portfolio_id = ? AND status = 'open'",
            (PORTFOLIO_ARWEN,),
        ).fetchall()
    ctx["positions"] = {p["symbol"]: dict(p) for p in positions}

    # Per-symbol context
    from dex_risk_rules import DexRiskRules
    risk = DexRiskRules()

    for symbol in DEX_TRADE_SYMBOLS:
        sym_ctx = {"symbol": symbol}

        # Latest indicators
        for tf in ["1h", "4h"]:
            row = conn.execute(
                "SELECT rsi_14, adx_14, macd_line, macd_signal, macd_histogram, "
                "sma_20, sma_50, bb_upper, bb_lower, bb_width, atr_14, volume_ratio "
                "FROM indicators WHERE symbol = ? AND timeframe = ? "
                "ORDER BY timestamp DESC LIMIT 1",
                (symbol, tf),
            ).fetchone()
            if row:
                sym_ctx[f"indicators_{tf}"] = dict(row)

        # DEX metadata
        meta = conn.execute(
            "SELECT dt.liquidity, dt.volume_24h, dt.holder_count, dt.is_verified, "
            "dt.last_updated "
            "FROM securities s JOIN dex_tokens dt ON dt.security_id = s.id "
            "WHERE s.symbol = ? AND s.is_dex = 1",
            (symbol,),
        ).fetchone()
        if meta:
            sym_ctx["dex_metadata"] = dict(meta)

        # Risk assessment
        is_safe, warnings = risk.run_all_checks(symbol, trade_usd=25)
        sym_ctx["risk_safe"] = is_safe
        sym_ctx["risk_warnings"] = warnings
        ctx["risk_warnings"].extend(warnings)

        # Recent price action
        candles = conn.execute(
            "SELECT close, volume FROM candlesticks "
            "WHERE symbol = ? AND timeframe = '1h' "
            "ORDER BY timestamp DESC LIMIT 24",
            (symbol,),
        ).fetchall()
        if candles:
            prices = [c["close"] for c in candles]
            sym_ctx["price_current"] = prices[0]
            sym_ctx["price_24h_ago"] = prices[-1] if len(prices) >= 24 else prices[-1]
            sym_ctx["price_change_24h_pct"] = (
                ((prices[0] / prices[-1]) - 1) * 100 if prices[-1] else 0
            )
            sym_ctx["avg_hourly_volume"] = (
                sum(c["volume"] for c in candles) / len(candles)
            )

        ctx["symbols"][symbol] = sym_ctx

    # ETH price (for portfolio value calculation)
    eth_row = conn.execute(
        "SELECT close FROM candlesticks WHERE symbol = 'ETH-USD' "
        "AND timeframe = '1h' ORDER BY timestamp DESC LIMIT 1",
    ).fetchone()
    ctx["eth_price"] = float(eth_row["close"]) if eth_row else 0

    conn.close()
    return ctx


# ── Phase 3: LLM decision ─────────────────────────────────────────────────

def build_dex_prompt(ctx: dict) -> str:
    """Build the LLM prompt for DEX trading decisions."""
    wallet_str = ""
    for b in ctx.get("wallet", {}).get("balances", []):
        wallet_str += f"  {b['token']} on {b['chain']}: {b['amount']:.6g} (${b['usd_value']:.2f})\n"

    positions_str = ""
    for sym, pos in ctx.get("positions", {}).items():
        pnl = pos.get("pnl", 0) or 0
        positions_str += f"  {sym}: qty={pos['quantity']:.6g} entry=${pos['entry_price']:.4f} pnl=${pnl:+.2f}\n"

    symbols_str = ""
    for sym, data in ctx.get("symbols", {}).items():
        symbols_str += f"\n### {sym}\n"
        if data.get("price_current"):
            symbols_str += f"Price: ${data['price_current']:.4f} ({data.get('price_change_24h_pct', 0):+.1f}% 24h)\n"
        if data.get("avg_hourly_volume"):
            symbols_str += f"Avg hourly volume: ${data['avg_hourly_volume']:.0f}\n"

        for tf in ["1h", "4h"]:
            ind = data.get(f"indicators_{tf}")
            if ind:
                symbols_str += (
                    f"{tf}: RSI={ind['rsi_14']:.1f} ADX={ind['adx_14']:.1f} "
                    f"MACD={'↑' if (ind.get('macd_histogram') or 0) > 0 else '↓'} "
                    f"BB_width={ind.get('bb_width', 0):.3f} "
                    f"Vol_ratio={ind.get('volume_ratio', 0):.2f}\n"
                )

        meta = data.get("dex_metadata", {})
        if meta:
            symbols_str += (
                f"DEX: liquidity=${meta.get('liquidity', 0):.0f} "
                f"vol24h=${meta.get('volume_24h', 0):.0f} "
                f"holders={meta.get('holder_count', '?')}\n"
            )

        if data.get("risk_warnings"):
            symbols_str += f"RISKS: {'; '.join(data['risk_warnings'])}\n"

    prompt = f"""You are Aleph, managing the Arwen DEX portfolio on Base chain via Bankr.

## Wallet
{wallet_str or "  (no balance data)"}
ETH price: ${ctx.get('eth_price', 0):.2f}

## Current Positions
{positions_str or "  (none)"}

## DEX Token Analysis
{symbols_str}

## DEX Safety Rules
- Minimum pool liquidity: ${DEX_CONFIG['min_liquidity_usd']:,.0f}
- Maximum slippage: {DEX_CONFIG['max_slippage_percent']}%
- Maximum position: {DEX_CONFIG['max_position_size_percent']}% of portfolio
- Maximum single order: ${DEX_CONFIG['max_single_order_usd']:.0f}

## Task
Analyze the DEX tokens above and decide on trading actions for the Arwen portfolio.
Consider: technical indicators, liquidity depth, volume trends, and risk warnings.

Respond with a JSON object:
{{
    "assessment": "brief market view (1-2 sentences)",
    "trades": [
        {{
            "action": "BUY" or "SELL",
            "symbol": "VVV-BASE",
            "amount_usd": 25,
            "conviction": "high" or "medium" or "low",
            "reasoning": {{
                "thesis": "why this trade",
                "supporting": ["point1", "point2"],
                "contradicting": ["risk1"],
                "risk_note": "key risk"
            }}
        }}
    ]
}}

If no trades are warranted, return empty trades array with your assessment.
Only trade symbols that pass risk checks. Never force a trade.
"""
    return prompt


def call_llm(prompt: str) -> dict:
    """Call LLM for trading decision. Returns parsed JSON response."""
    import openai

    client = openai.OpenAI(
        api_key=os.getenv("OPENAI_API_KEY"),
        base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
    )

    try:
        response = client.chat.completions.create(
            model=os.getenv("TRADING_MODEL", "gpt-4o-mini"),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=1000,
        )
        text = response.choices[0].message.content.strip()

        # Extract JSON from response
        import re
        json_match = re.search(r'\{.*\}', text, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
        return {"assessment": text, "trades": []}

    except Exception as e:
        logger.error(f"LLM call failed: {e}")
        return {"assessment": f"LLM error: {e}", "trades": []}


# ── Phase 4: Execution ─────────────────────────────────────────────────────

def execute_dex_trades(trades: list, dry_run: bool = False) -> list:
    """Execute trade decisions via DexExecutor."""
    from dex_executor import DexExecutor
    from dex_risk_rules import DexRiskRules

    executor = DexExecutor(dry_run=dry_run)
    risk = DexRiskRules()
    results = []

    for trade in trades:
        symbol = trade.get("symbol", "")
        action = trade.get("action", "").upper()
        amount_usd = trade.get("amount_usd", 0)
        conviction = trade.get("conviction", "low")
        reasoning = trade.get("reasoning", {})

        # Final risk check
        if action == "BUY":
            is_safe, warnings = risk.run_all_checks(symbol, trade_usd=amount_usd)
            if not is_safe:
                logger.warning(f"[dex] Trade blocked by risk: {warnings}")
                results.append({"symbol": symbol, "action": action,
                                "blocked": True, "warnings": warnings})
                continue

        # Build decision context
        decision_ctx = json.dumps({
            "signal_type": "dex_llm",
            "conviction": conviction,
            "reasoning": reasoning,
        })

        if action == "BUY":
            result = executor.execute_buy(
                symbol, amount_usd,
                reason=reasoning.get("thesis", ""),
            )
        elif action == "SELL":
            sell_pct = trade.get("sell_pct", 1.0)
            result = executor.execute_sell(
                symbol, sell_pct=sell_pct,
                reason=reasoning.get("thesis", ""),
            )
        else:
            logger.warning(f"Unknown action: {action}")
            continue

        results.append(result)
        if result.get("success"):
            logger.info(f"[dex] {action} {symbol}: {result}")
        else:
            logger.error(f"[dex] {action} {symbol} failed: {result.get('error')}")

    return results


# ── Main orchestrator ──────────────────────────────────────────────────────

def run_dex_cycle(dry_run: bool = False, data_only: bool = False):
    """Run a complete DEX trading cycle."""
    logger.info("=" * 60)
    logger.info("DEX Trading Agent — Arwen Portfolio")
    logger.info("=" * 60)

    # Phase 1: Data
    logger.info("\n[Phase 1] Collecting DEX data...")
    try:
        update_dex_data()
    except Exception as e:
        logger.error(f"Data collection failed: {e}")
        # Continue anyway — we may have recent enough data

    logger.info("\n[Phase 1b] Computing indicators...")
    try:
        compute_dex_indicators()
    except Exception as e:
        logger.error(f"Indicator computation failed: {e}")

    if data_only:
        logger.info("\n=== Data-only mode — stopping here ===")
        return

    # Phase 2: Context
    logger.info("\n[Phase 2] Gathering context...")
    ctx = gather_dex_context()

    # Log summary
    total_wallet = sum(b.get("usd_value", 0) for b in ctx.get("wallet", {}).get("balances", []))
    logger.info(f"  Wallet: ${total_wallet:.2f}")
    logger.info(f"  Positions: {len(ctx.get('positions', {}))}")
    logger.info(f"  Symbols analyzed: {len(ctx.get('symbols', {}))}")
    if ctx.get("risk_warnings"):
        logger.info(f"  Risk warnings: {ctx['risk_warnings']}")

    # Phase 3: LLM decision
    logger.info("\n[Phase 3] Consulting LLM...")
    prompt = build_dex_prompt(ctx)
    decision = call_llm(prompt)

    logger.info(f"  Assessment: {decision.get('assessment', 'none')}")
    trades = decision.get("trades", [])
    logger.info(f"  Trade proposals: {len(trades)}")

    if not trades:
        logger.info("\n=== No trades proposed — cycle complete ===")
        return

    # Phase 4: Execute
    mode_str = "DRY-RUN" if dry_run else "LIVE"
    logger.info(f"\n[Phase 4] Executing trades ({mode_str})...")
    results = execute_dex_trades(trades, dry_run=dry_run)

    for r in results:
        if r.get("blocked"):
            logger.info(f"  BLOCKED: {r['symbol']} — {r.get('warnings', [])}")
        elif r.get("success"):
            logger.info(f"  OK: {r.get('action', '?')} {r.get('symbol', '?')} "
                         f"qty={r.get('quantity', 0):.6g}")
        else:
            logger.info(f"  FAIL: {r.get('error', 'unknown')}")

    logger.info("\n=== Cycle complete ===")


def main():
    parser = argparse.ArgumentParser(description="DEX Trading Agent (Arwen)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Simulate trades without executing")
    parser.add_argument("--data-only", action="store_true",
                        help="Only collect data and compute indicators")
    args = parser.parse_args()

    run_dex_cycle(dry_run=args.dry_run, data_only=args.data_only)


if __name__ == "__main__":
    main()
