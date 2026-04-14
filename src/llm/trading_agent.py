#!/usr/bin/env python3
"""
Trading Agent — Stage 2 of the two-stage LLM trading pipeline.

Architecture:
  Stage 1 (research_agent.py):  Qualitative intelligence — sentiment, historical
      patterns, arXiv insights, macro narrative → ResearchBrief
  Stage 2 (this file):  Trading decisions — combines ResearchBrief with quantitative
      signals, scores, portfolio state, and trade journal history

The key insight: quantitative signals tell you WHAT the numbers say;
the research brief tells you WHY the market is moving. Combining both
produces higher-conviction trades than either source alone.

Self-preservation: the prompt dynamically constrains the agent based on
its own historical win rate, regime performance, and cumulative PnL.
If the agent has been losing, the rules get stricter automatically.

Execution guardrails (enforced in code, not just in the prompt):
  - Max 3 LLM trades per session
  - 10% cash reserve (never spent)
  - 3-20% allocation per trade (trend-confirmed can go to 20%)
  - 6-24h hold period (adjustable)
  - Conviction gating: LOW=never, MEDIUM=needs signal confirmation, HIGH=unconditional
  - Structured reasoning required (thesis + quant_support + research_support)

This is Argus's trading brain.
"""

import os
import sys
import json
import sqlite3
import logging
import subprocess
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    DB_PATH,
    PAPER_STATE_FILE,
    TELEGRAM_CHAT_ID,
    PORTFOLIO_SYMBOLS,
    TOP_CRYPTO_SYMBOLS,
    EQUITY_SYMBOLS,
    STOP_LOSS_PCT,
    TRAILING_STOP_ACTIVATION,
    TAKE_PROFIT_LEVELS,
    MAX_PORTFOLIO_DRAWDOWN,
    MAX_POSITION_PCT,
    MAX_SECTOR_PCT,
    get_asset_type,
    get_sector,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# LLM chain — shared multi-provider fallback (DeepSeek > Nous > Claude > GPT-4o > MLX)
# Provider configuration and API key resolution is handled by llm_chain.py.
from llm.llm_chain import LLMChain as _LLMChain


class TradingAgent:
    """
    Strategic trading agent (Stage 2 of two-agent pipeline).

    Combines quantitative signals with qualitative research from the
    Research Agent to make informed trading decisions. The research brief
    provides the "why" behind market moves; the signal engine provides
    the "what" from the numbers.
    """

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._init_components()

    def _init_components(self):
        """Lazy-initialize all sub-components."""
        self._portfolio = None
        self._risk_manager = None
        self._scorer = None
        self._signal_system = None
        self._correlation_tracker = None
        self._market_intel = None
        self._smart_rebalancer = None
        self._research_agent = None

        # Shared LLM chain (30s timeout for trading agent's deep reasoning context)
        self._llm_chain = _LLMChain(
            system_prompt=(
                "You are Argus, a systematic portfolio manager who combines quantitative signals "
                "with qualitative research to make high-conviction trading decisions. "
                "You learn from every trade outcome and refuse to repeat losing patterns. "
                "Always respond with valid JSON. Be analytical, precise, and self-critical."
            ),
            timeout=30,
            cache_ttl=0,  # no caching for trading agent — each run has fresh context
            fallback_json={
                "market_assessment": "LLM services temporarily unavailable.",
                "risk_level": "moderate",
                "trades": [],
                "rebalance_recommended": False,
                "hold_rationale": "Maintaining positions — LLM unavailable on this cycle.",
                "watchlist": [],
            },
        )

    @property
    def research_agent(self):
        if self._research_agent is None:
            from llm.research_agent import ResearchAgent
            self._research_agent = ResearchAgent(db_path=self.db_path)
        return self._research_agent

    @property
    def portfolio(self):
        if self._portfolio is None:
            from core.paper_trading import PaperTradingPortfolio

            self._portfolio = PaperTradingPortfolio(db_path=self.db_path)
            self._portfolio._current_trader_id = 2  # Regi (quant agent)
        return self._portfolio

    @property
    def risk_manager(self):
        if self._risk_manager is None:
            from core.risk_manager import RiskManager

            self._risk_manager = RiskManager(db_path=self.db_path)
        return self._risk_manager

    @property
    def scorer(self):
        if self._scorer is None:
            from scoring.advanced_scorer import AdvancedScoringModel

            self._scorer = AdvancedScoringModel(db_path=self.db_path)
        return self._scorer

    @property
    def signal_system(self):
        if self._signal_system is None:
            from core.signal_trading import SignalTradingSystem
            from config import get_active_portfolios

            # Use Galadriel's config (portfolio_id=1) so routing is respected
            portfolios = get_active_portfolios(db_path=self.db_path)
            galadriel = next((p for p in portfolios if p["id"] == 1), None)
            self._signal_system = SignalTradingSystem(
                db_path=self.db_path,
                test_mode=True,
                portfolio_config=galadriel,
            )
        return self._signal_system

    @property
    def correlation_tracker(self):
        if self._correlation_tracker is None:
            from data.correlation_tracker import CorrelationTracker

            self._correlation_tracker = CorrelationTracker(db_path=self.db_path)
        return self._correlation_tracker

    @property
    def market_intel(self):
        if self._market_intel is None:
            from llm.market_intelligence import MarketIntelligence

            self._market_intel = MarketIntelligence(db_path=self.db_path)
        return self._market_intel

    @property
    def smart_rebalancer(self):
        if self._smart_rebalancer is None:
            from core.smart_rebalancer import SmartRebalancer

            self._smart_rebalancer = SmartRebalancer(db_path=self.db_path)
        return self._smart_rebalancer

    # ── Trend Classification ─────────────────────────────────────────────

    def classify_trend(self, symbol: str, timeframe: str = "1h") -> Dict:
        """Classify the trend regime for a symbol using SMA alignment + ADX.

        Returns a dict with:
          - trend: 'uptrend' | 'downtrend' | 'ranging'
          - strength: 'strong' | 'moderate' | 'weak'
          - details: human-readable summary
          - sma_alignment: which SMAs are stacked and how
          - adx: current ADX value
        """
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT c.close, i.sma_20, i.sma_50, i.sma_200, i.adx_14, "
                "i.macd_line, i.macd_signal, i.ema_12, i.ema_26 "
                "FROM candlesticks c "
                "JOIN indicators i ON c.symbol=i.symbol AND c.timeframe=i.timeframe AND c.timestamp=i.timestamp "
                "WHERE c.symbol=? AND c.timeframe=? "
                "ORDER BY c.timestamp DESC LIMIT 1",
                (symbol, timeframe),
            )
            row = cur.fetchone()
        finally:
            conn.close()

        if not row:
            return {
                "trend": "unknown",
                "strength": "unknown",
                "details": "no data",
                "adx": None,
            }

        price, sma20, sma50, sma200, adx, macd_line, macd_signal, ema12, ema26 = row

        # Convert safely
        vals = {}
        for name, val in zip(
            [
                "price",
                "sma20",
                "sma50",
                "sma200",
                "adx",
                "macd_line",
                "macd_signal",
                "ema12",
                "ema26",
            ],
            [price, sma20, sma50, sma200, adx, macd_line, macd_signal, ema12, ema26],
        ):
            try:
                vals[name] = float(val) if val is not None else None
            except (ValueError, TypeError):
                vals[name] = None

        p = vals["price"]
        s20, s50, s200 = vals["sma20"], vals["sma50"], vals["sma200"]
        adx_val = vals["adx"]

        # SMA alignment scoring
        alignment = []
        if p and s20:
            alignment.append("price>SMA20" if p > s20 else "price<SMA20")
        if s20 and s50:
            alignment.append("SMA20>SMA50" if s20 > s50 else "SMA20<SMA50")
        if s50 and s200:
            alignment.append("SMA50>SMA200" if s50 > s200 else "SMA50<SMA200")

        # Count bullish/bearish signals
        bull_count = sum(1 for a in alignment if ">" in a)
        bear_count = sum(1 for a in alignment if "<" in a)

        # MACD direction adds context
        macd_bullish = (
            vals["macd_line"] is not None
            and vals["macd_signal"] is not None
            and vals["macd_line"] > vals["macd_signal"]
        )

        # Classify trend
        if bull_count >= 2 and (macd_bullish or bull_count == 3):
            trend = "uptrend"
        elif bear_count >= 2 and (not macd_bullish or bear_count == 3):
            trend = "downtrend"
        else:
            trend = "ranging"

        # Classify strength via ADX
        if adx_val is not None:
            if adx_val > 35:
                strength = "strong"
            elif adx_val > 20:
                strength = "moderate"
            else:
                strength = "weak"
        else:
            strength = "unknown"

        # Build details
        parts = [f"trend={trend}", f"strength={strength}"]
        if adx_val is not None:
            parts.append(f"ADX={adx_val:.1f}")
        parts.append(" ".join(alignment))
        if vals["macd_line"] is not None:
            parts.append(f"MACD {'bullish' if macd_bullish else 'bearish'}")

        return {
            "trend": trend,
            "strength": strength,
            "adx": round(adx_val, 1) if adx_val else None,
            "sma_alignment": alignment,
            "macd_bullish": macd_bullish,
            "details": " | ".join(parts),
        }

    # ── Data Collection ──────────────────────────────────────────────────

    def gather_context(self) -> Dict:
        """Collect all available market data for the trading decision."""
        ctx = {
            "timestamp": datetime.now().isoformat(),
            "date": datetime.now().strftime("%Y-%m-%d"),
        }

        # 1. Portfolio state
        try:
            portfolio_value = self.portfolio.get_portfolio_value()
            ctx["portfolio"] = {
                "value": round(portfolio_value, 2),
                "cash": round(self.portfolio.capital, 2),
                "positions": {},
            }
            for sym, pos in self.portfolio.positions.items():
                price = self.portfolio.get_current_price(sym)
                value = price * pos["quantity"]
                pnl = self.portfolio.get_position_pnl(sym)
                ctx["portfolio"]["positions"][sym] = {
                    "quantity": round(pos["quantity"], 6),
                    "avg_price": round(pos["avg_price"], 4),
                    "current_price": round(price, 4),
                    "value": round(value, 2),
                    "pnl_pct": round(pnl.get("unrealized_pct", 0), 2),
                    "sector": get_sector(sym),
                }
        except Exception as e:
            logger.warning(f"Portfolio context failed: {e}")

        # 2. Market regime
        try:
            regime, vix = self.correlation_tracker.detect_regime()
            btc_corrs = self.correlation_tracker.btc_equity_correlations(30)
            beta = self.correlation_tracker.portfolio_beta_vs_btc()
            ctx["regime"] = {
                "label": regime,
                "vix": round(vix, 1) if vix else None,
                "btc_spy_corr": round(btc_corrs.get("BTC-SPY", 0) or 0, 3),
                "btc_qqq_corr": round(btc_corrs.get("BTC-QQQ", 0) or 0, 3),
                "portfolio_beta": round(beta, 2) if beta else None,
            }
        except Exception as e:
            logger.warning(f"Regime context failed: {e}")

        # 3. Scoring model results
        try:
            scores = {}
            all_symbols = list(set(PORTFOLIO_SYMBOLS + TOP_CRYPTO_SYMBOLS[:8]))
            for sym in all_symbols:
                score_data = self.scorer.calculate_total_score(sym)
                scores[sym] = score_data
            ctx["scores"] = scores
        except Exception as e:
            logger.warning(f"Scoring context failed: {e}")

        # 3b. Per-symbol trend classification
        try:
            trend_map = {}
            all_symbols = list(set(PORTFOLIO_SYMBOLS + TOP_CRYPTO_SYMBOLS[:8]))
            for sym in all_symbols:
                trend_map[sym] = self.classify_trend(sym, "1h")
            ctx["trends"] = trend_map
        except Exception as e:
            logger.warning(f"Trend classification failed: {e}")

        # 4. Technical signals
        try:
            result = self.signal_system.check_all_symbols()
            if isinstance(result, tuple):
                signals, risk_exits, risk_report = result
            else:
                signals, risk_exits, risk_report = result, [], None
            ctx["signals"] = signals
            ctx["risk_exits"] = [
                {"symbol": e.symbol, "type": e.exit_type, "reason": e.reason}
                for e in risk_exits
            ]
            ctx["risk_report"] = risk_report
        except Exception as e:
            logger.warning(f"Signal context failed: {e}")
            ctx["signals"] = []
            ctx["risk_exits"] = []

        # 5. Risk manager state
        try:
            ctx["risk_state"] = {
                "circuit_breaker_active": self.risk_manager.circuit_breaker_active,
                "portfolio_peak": self.risk_manager.portfolio_peak,
                "tracked_positions": len(self.risk_manager.entry_prices),
                "entry_prices": {
                    k: round(v, 4) for k, v in self.risk_manager.entry_prices.items()
                },
            }
        except Exception as e:
            logger.warning(f"Risk state context failed: {e}")

        # 6. Recent sentiment (kept for backward compat; research agent is primary source)
        try:
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()
            cutoff = int((datetime.now() - timedelta(hours=24)).timestamp())
            cur.execute(
                "SELECT symbol, score, confidence, summary FROM sentiment_scores "
                "WHERE timestamp >= ? ORDER BY timestamp DESC LIMIT 10",
                (cutoff,),
            )
            ctx["sentiment"] = [
                {"symbol": r[0], "score": r[1], "confidence": r[2], "summary": r[3]}
                for r in cur.fetchall()
            ]
            conn.close()
        except Exception as e:
            logger.warning(f"Sentiment context failed: {e}")

        # 7. Recent risk guardian events (exits that happened between reviews)
        try:
            from core.risk_guardian import RiskGuardian

            recent_events = RiskGuardian.get_recent_events(hours=12)
            ctx["recent_risk_events"] = recent_events
            if recent_events:
                logger.info(f"Found {len(recent_events)} recent risk events")
        except Exception as e:
            logger.warning(f"Risk events context failed: {e}")
            ctx["recent_risk_events"] = []

        # 8. Trade journal performance + similar past trades
        try:
            from reports.trade_journal import TradeJournal

            journal = TradeJournal(db_path=self.db_path)
            ctx["journal_by_signal"] = journal.get_performance_by_signal_type()
            ctx["journal_by_regime"] = journal.get_performance_by_regime()

            # Find similar past trades for each active signal (rich query)
            similar_trades = []
            regime_label = ctx.get("regime", {}).get("label")
            scores = ctx.get("scores", {})
            for sig in ctx.get("signals", [])[:3]:  # top 3 signals only
                sym = sig.get("symbol", "")
                sym_score = scores.get(sym, {})
                vol_label = None
                if sym_score.get("volatility") is not None:
                    vol_label = (
                        "high"
                        if sym_score["volatility"] > 60
                        else "low"
                        if sym_score["volatility"] < 40
                        else "moderate"
                    )
                similar = journal.find_similar_trades(
                    sym,
                    signal_type=sig.get("signal_type")
                    or sig.get("reason", "").split()[0]
                    if sig.get("reason")
                    else None,
                    market_regime=regime_label,
                    k=2,
                    current_price=sig.get("price"),
                    signal_strength=sig.get("strength"),
                    volatility=vol_label,
                    action=sig.get("action"),
                )
                for t in similar:
                    similar_trades.append(t)
            ctx["similar_past_trades"] = similar_trades[:5]
        except Exception as e:
            logger.warning(f"Trade journal context failed: {e}")
            ctx["journal_by_signal"] = []
            ctx["journal_by_regime"] = []
            ctx["similar_past_trades"] = []

        # 9. Historical context from market intelligence
        try:
            current_snapshot = {
                "regime": ctx.get("regime", {}).get("label", "unknown"),
                "vix": ctx.get("regime", {}).get("vix"),
                "btc_spy_corr": ctx.get("regime", {}).get("btc_spy_corr"),
                "signals": ctx.get("signals", []),
            }
            similar = self.market_intel.find_similar_conditions(
                current_snapshot, top_k=3
            )
            ctx["historical_context"] = [
                {
                    "date": s["date"],
                    "content": s["content"][:300],
                    "similarity": round(s["similarity"], 3),
                }
                for s in similar
            ]
        except Exception as e:
            logger.warning(f"Historical context failed: {e}")
            ctx["historical_context"] = []

        return ctx

    # ── LLM Analysis ─────────────────────────────────────────────────────

    def _build_analysis_prompt(self, ctx: Dict) -> str:
        """Build a structured prompt for the LLM trading analysis.

        Two-stage architecture: the Research Agent (Stage 1) has already
        produced a qualitative research brief. This prompt combines that
        brief with quantitative data so the LLM can make informed trades.
        """
        lines = []

        # ── Identity & philosophy ────────────────────────────────────
        lines.append(
            "You are Argus, a systematic portfolio manager who combines quantitative "
            "signals with qualitative research to make high-conviction trading decisions."
        )
        lines.append(
            "You have TWO sources of intelligence: (1) a Research Agent that provides "
            "news sentiment, market narrative, risk flags, and historical patterns; "
            "(2) a Signal Engine that provides quantitative scores, trend data, and "
            "backtested trading signals."
        )
        lines.append(
            "Your edge comes from COMBINING these — a quantitative signal confirmed "
            "by positive sentiment and no risk flags is worth more than either alone."
        )
        lines.append("")

        # ── Self-preservation rules (enforced) ───────────────────────
        lines.append("SELF-PRESERVATION RULES (non-negotiable):")

        # Dynamically build rules from trade journal
        journal_signals = ctx.get("journal_by_signal", [])
        journal_regimes = ctx.get("journal_by_regime", [])

        llm_stats = next(
            (j for j in journal_signals if j.get("signal_type") == "llm"), None
        )
        current_regime = ctx.get("regime", {}).get("label", "unknown")
        regime_stats = next(
            (j for j in journal_regimes if j.get("regime") == current_regime), None
        )

        if llm_stats and llm_stats.get("win_rate", 100) < 30:
            lines.append(
                f"- YOUR (llm) signal type has {llm_stats['win_rate']}% win rate "
                f"over {llm_stats['total_trades']} trades. You MUST NOT trade unless you "
                f"can articulate what is DIFFERENT this time. Default to HOLD."
            )
        if regime_stats and regime_stats.get("win_rate", 100) < 40:
            lines.append(
                f"- Current regime '{current_regime}' has {regime_stats['win_rate']}% win rate. "
                f"Maximum allocation per trade: 5%. Prefer HOLD over weak setups."
            )
        if llm_stats and llm_stats.get("total_pnl_usd", 0) < -10:
            lines.append(
                f"- Your cumulative PnL is ${llm_stats['total_pnl_usd']:+.2f}. "
                f"You are in a drawdown. Size conservatively and require HIGH conviction."
            )

        lines.append(
            "- If you cannot find a trade where BOTH research sentiment AND quantitative "
            "signals align, the correct answer is HOLD with a clear rationale."
        )
        lines.append(
            "- Proposing a trade that contradicts the research brief requires explicit "
            "acknowledgment of the contradiction and why you are overriding it."
        )
        lines.append("")

        # ── Hard rules ───────────────────────────────────────────────
        lines.append(
            "HARD RULES (enforced by execution engine — violations are blocked):"
        )
        lines.append(
            f"- Stop-loss: {STOP_LOSS_PCT:.0%} below entry (automatic, cannot be overridden)"
        )
        lines.append(
            f"- Circuit breaker: {MAX_PORTFOLIO_DRAWDOWN:.0%} portfolio drawdown (liquidates all)"
        )
        lines.append(
            f"- Max position size: {MAX_POSITION_PCT:.0%} of portfolio per symbol"
        )
        lines.append(f"- Max sector exposure: {MAX_SECTOR_PCT:.0%}")
        lines.append("- Max 3 trades per session")
        lines.append("- Cash reserve: 10% of portfolio value (never spent)")
        lines.append("- Min trade size: $10")
        lines.append("")

        # ── Adjustable bounds ────────────────────────────────────────
        lines.append("ADJUSTABLE BOUNDS (you can request values within these ranges):")
        lines.append(
            "- allocation_pct: 0.03 to 0.20 (default 0.10). Higher requires high "
            "conviction + trend confirmation + research support."
        )
        lines.append(
            "- hold_hours_override: 6 to 24 (default 12). Shorter requires explanation."
        )
        lines.append(
            "- sell_pct: 0.25 to 1.0. You decide how much of a position to exit."
        )
        lines.append("")

        # ── Conviction rules ─────────────────────────────────────────
        lines.append("CONVICTION RULES:")
        lines.append("- LOW conviction: NEVER executed")
        lines.append(
            "- MEDIUM conviction: requires signal engine confirmation (symbol must appear in ACTIVE SIGNALS)"
        )
        lines.append("- HIGH conviction: executes unconditionally")
        lines.append(
            "- To reach HIGH conviction, you should have: (a) quantitative signal "
            "support, (b) non-negative research sentiment, (c) no contradicting risk flags."
        )
        lines.append("- Prefer fewer high-conviction trades over many weak ones.")
        lines.append("")

        # ── RESEARCH BRIEF (Stage 1 output) ──────────────────────────
        research_brief = ctx.get("_research_brief")
        if research_brief:
            lines.append(research_brief.to_prompt_section())
        else:
            lines.append("## RESEARCH BRIEF")
            lines.append("  (Research Agent did not produce a brief — proceed with quantitative data only.)")
            lines.append("")

        # ── Quantitative data sections ───────────────────────────────

        # Portfolio state
        portfolio = ctx.get("portfolio", {})
        lines.append(
            f"## PORTFOLIO (Value: ${portfolio.get('value', 0):.2f}, Cash: ${portfolio.get('cash', 0):.2f})"
        )
        for sym, pos in portfolio.get("positions", {}).items():
            lines.append(
                f"  {sym}: ${pos['value']:.2f} ({pos['pnl_pct']:+.1f}%) [sector: {pos['sector']}]"
            )
        lines.append("")

        # Market regime
        regime = ctx.get("regime", {})
        lines.append("## MARKET REGIME")
        lines.append(
            f"  VIX: {regime.get('vix', 'N/A')} -> Regime: {regime.get('label', 'unknown')}"
        )
        lines.append(f"  BTC-SPY correlation: {regime.get('btc_spy_corr', 'N/A')}")
        lines.append(f"  Portfolio beta vs BTC: {regime.get('portfolio_beta', 'N/A')}")
        lines.append("")

        # Per-symbol trend classification
        trends = ctx.get("trends", {})
        if trends:
            lines.append("## TREND REGIME (per symbol)")
            for sym, t in sorted(trends.items()):
                lines.append(
                    f"  {sym}: {t['trend']} ({t['strength']}) -- {t['details']}"
                )
            lines.append("")

        # Scoring model top
        scores = ctx.get("scores", {})
        if scores:
            sorted_scores = sorted(
                scores.items(), key=lambda x: x[1].get("total_score", 0), reverse=True
            )
            lines.append("## TOP SCORES")
            for sym, sc in sorted_scores[:8]:
                lines.append(
                    f"  {sym}: {sc.get('total_score', 0):.1f} "
                    f"(M:{sc.get('momentum', 0):.0f} T:{sc.get('trend', 0):.0f} "
                    f"V:{sc.get('volatility', 0):.0f} R:{sc.get('risk_adjusted', 0):.0f})"
                )
            lines.append("")

        # Active signals
        signals = ctx.get("signals", [])
        if signals:
            lines.append("## ACTIVE SIGNALS (from signal engine)")
            for sig in signals:
                lines.append(
                    f"  {sig.get('symbol', '?')}: {sig.get('action', '?')} "
                    f"(strength {sig.get('strength', 0):.0f}) -- "
                    f"{sig.get('reason', '')[:120]}"
                )
            lines.append("")

        # Risk exits
        risk_exits = ctx.get("risk_exits", [])
        if risk_exits:
            lines.append("## RISK EXITS TRIGGERED")
            for ex in risk_exits:
                lines.append(
                    f"  {ex['symbol']}: {ex['type']} -- {ex['reason'][:100]}"
                )
            lines.append("")

        # Risk state
        risk_state = ctx.get("risk_state", {})
        if risk_state.get("circuit_breaker_active"):
            lines.append("## !! CIRCUIT BREAKER ACTIVE -- NO NEW BUYS !!")
            lines.append("")

        # Recent risk events
        risk_events = ctx.get("recent_risk_events", [])
        if risk_events:
            lines.append("## RECENT RISK EVENTS (since last review)")
            for event in risk_events[:5]:
                lines.append(
                    f"  [{event.get('timestamp', '?')}] Portfolio: "
                    f"${event.get('portfolio_value', 0):.2f}"
                )
                for ex in event.get("exits", []):
                    lines.append(
                        f"    {ex['type']} {ex['symbol']}: "
                        f"{ex.get('reason', '')[:100]}"
                    )
                if event.get("circuit_breaker"):
                    lines.append("    !! CIRCUIT BREAKER ACTIVATED")
            lines.append("")

        # Trade journal performance
        if journal_signals:
            lines.append("## TRADE JOURNAL -- SIGNAL PERFORMANCE")
            for j in journal_signals:
                wr = j.get("win_rate", 0)
                lines.append(
                    f"  {j['signal_type']}: {j['total_trades']} trades, "
                    f"{wr}% win rate, avg {j['avg_return_pct']:+.2f}%, "
                    f"PnL ${j['total_pnl_usd']:+.4f}"
                )
            lines.append("")
        if journal_regimes:
            lines.append("## TRADE JOURNAL -- REGIME PERFORMANCE")
            for j in journal_regimes:
                wr = j.get("win_rate", 0)
                lines.append(
                    f"  {j['regime']}: {j['total_trades']} trades, "
                    f"{wr}% win rate, avg {j['avg_return_pct']:+.2f}%"
                )
            lines.append("")

        # Similar past trades
        similar_trades = ctx.get("similar_past_trades", [])
        if similar_trades:
            lines.append("## SIMILAR PAST TRADES (from journal)")
            for t in similar_trades:
                lines.append(
                    f"  {t.get('symbol', '?')}: {t.get('signal_type', '?')} signal, "
                    f"outcome {t.get('outcome_pct', 0):+.2f}%, "
                    f"held {t.get('holding_hours', 0):.0f}h, "
                    f"exit={t.get('exit_reason', '?')}, "
                    f"regime={t.get('market_regime', '?')}"
                )
            lines.append("")

        # Historical similar conditions
        hist = ctx.get("historical_context", [])
        if hist:
            lines.append("## SIMILAR HISTORICAL CONDITIONS")
            for h in hist:
                lines.append(
                    f"  [{h['date']}] (similarity {h['similarity']:.2f}): "
                    f"{h['content'][:150]}"
                )
            lines.append("")

        # ── Response schema ──────────────────────────────────────────
        lines.append("## YOUR TASK")
        lines.append(
            "Analyze BOTH the research brief AND the quantitative data above. "
            "Provide trade recommendations only where research and signals converge."
        )
        lines.append(
            "Every trade must justify itself with BOTH quantitative AND qualitative reasoning."
        )
        lines.append(
            "If research flags a risk for a symbol, you must address it in your reasoning."
        )
        lines.append(
            "If no trades meet your conviction threshold, HOLD and explain why."
        )
        lines.append("")
        lines.append("Respond in this exact JSON format:")
        lines.append("```json")
        lines.append("{")
        lines.append(
            '  "market_assessment": "1-2 sentence summary incorporating both research narrative and quantitative state",'
        )
        lines.append('  "risk_level": "low|moderate|elevated|high",')
        lines.append('  "trades": [')
        lines.append("    {")
        lines.append('      "symbol": "BTC-USD",')
        lines.append('      "action": "BUY|SELL",')
        lines.append('      "amount_pct": 0.10,')
        lines.append('      "conviction": "high|medium|low",')
        lines.append('      "sell_pct": 0.5,')
        lines.append('      "hold_hours_override": 12,')
        lines.append('      "reasoning": {')
        lines.append(
            '        "thesis": "1-2 sentence core thesis combining quant signal + research insight",'
        )
        lines.append(
            '        "trend_regime": "uptrend|downtrend|ranging",'
        )
        lines.append(
            '        "quant_support": ["quantitative indicators supporting this trade"],'
        )
        lines.append(
            '        "research_support": ["qualitative factors from research brief supporting this trade"],'
        )
        lines.append(
            '        "contradicting": ["factors arguing against — from either source"],'
        )
        lines.append(
            '        "regime_consideration": "how VIX/macro regime affects this trade",'
        )
        lines.append(
            '        "similar_past_outcome": "what happened in similar conditions (or null)",'
        )
        lines.append(
            '        "risk_note": "what could go wrong and how risk is managed"'
        )
        lines.append("      }")
        lines.append("    }")
        lines.append("  ],")
        lines.append('  "rebalance_recommended": false,')
        lines.append(
            '  "hold_rationale": "if no trades, explain why holding is the right call '
            '— reference both research and quantitative reasons",'
        )
        lines.append('  "watchlist": ["symbols to monitor closely and why"]')
        lines.append("}")
        lines.append("```")
        lines.append("")
        lines.append(
            "IMPORTANT: The 'reasoning' object is REQUIRED for every trade. "
            "Trades without it are rejected."
        )
        lines.append(
            "IMPORTANT: 'quant_support' and 'research_support' replace the old "
            "'supporting' field — you MUST cite evidence from both sources."
        )

        return "\n".join(lines)

    def _call_llm(self, prompt: str) -> Optional[str]:
        """Call LLM via the shared LLMChain (5-tier fallback, guaranteed non-None)."""
        return self._llm_chain.call(prompt)

    def _parse_llm_response(self, response: str) -> Optional[Dict]:
        """Parse JSON from LLM response via the shared LLMChain parser."""
        return _LLMChain._parse_json(response)

    # ── Trade Execution ──────────────────────────────────────────────────

    def _build_decision_context(
        self, trade: Dict, ctx: Dict, guardrail_notes: List[str] = None
    ) -> str:
        """Build a rich JSON decision_context for storage in the trades table.

        This is the audit trail: what the LLM saw, what it decided, and what
        the guardrails modified before execution.
        """
        reasoning = trade.get("reasoning", {})
        # Accept legacy flat 'rationale' string as fallback
        if not reasoning and trade.get("rationale"):
            reasoning = {"thesis": trade["rationale"]}

        # Capture research brief metadata if available
        research_brief = ctx.get("_research_brief")
        research_summary = None
        if research_brief:
            sym = trade.get("symbol", "")
            sym_brief = research_brief.symbol_briefs.get(sym, {})
            research_summary = {
                "market_narrative": research_brief.market_narrative[:200],
                "risk_flags": research_brief.risk_flags[:3],
                "symbol_sentiment": sym_brief.get("sentiment", {}).get("score"),
            }

        context = {
            "signal_type": "llm",
            "conviction": trade.get("conviction", "unknown"),
            "market_regime": ctx.get("regime", {}).get("label"),
            "vix": ctx.get("regime", {}).get("vix"),
            "market_assessment": ctx.get("_analysis", {}).get("market_assessment"),
            "reasoning": reasoning,
            "research_context": research_summary,
            "trend_regime": reasoning.get("trend_regime")
            or ctx.get("trends", {}).get(trade.get("symbol"), {}).get("trend"),
            "requested_allocation_pct": trade.get("amount_pct"),
            "requested_hold_hours": trade.get("hold_hours_override"),
            "requested_sell_pct": trade.get("sell_pct"),
            "guardrail_adjustments": guardrail_notes or [],
        }
        return json.dumps(context, default=str)

    def execute_trades(self, analysis: Dict, ctx: Dict) -> List[Dict]:
        """Execute trades recommended by the LLM analysis.

        Guardrails:
        - Max 3 LLM trades per session (prevents portfolio churn)
        - 10% cash reserve (never spend last 10% on LLM trades)
        - Adjustable hold period: 6-24h (LLM can request, default 12h)
        - Adjustable allocation: 3-20% per LLM buy (default capped at 15%)
        - Only high-conviction trades execute; medium requires signal confirmation
        - Structured reasoning is required for every trade

        Every trade stores a rich decision_context JSON in the trades table.
        """
        executed = []
        portfolio_value = ctx.get("portfolio", {}).get("value", 0)

        # Stash analysis in ctx for _build_decision_context
        ctx["_analysis"] = analysis

        if not portfolio_value or portfolio_value <= 0:
            return executed

        # Check circuit breaker
        if ctx.get("risk_state", {}).get("circuit_breaker_active"):
            logger.warning("Circuit breaker active -- no trades executed")
            return executed

        # Execute risk exits FIRST (mandatory, bypass all limits)
        for exit_sig in ctx.get("risk_exits", []):
            sym = exit_sig["symbol"]
            if sym in self.portfolio.positions:
                logger.info(f"Executing risk exit: {exit_sig['type']} {sym}")
                risk_ctx = json.dumps(
                    {
                        "signal_type": "risk_exit",
                        "exit_type": exit_sig["type"],
                        "reason": exit_sig["reason"],
                        "market_regime": ctx.get("regime", {}).get("label"),
                    }
                )
                self.portfolio.set_trade_context(sym, exit_reason=exit_sig["type"])
                self.portfolio._pending_decision_context = risk_ctx
                self.portfolio.execute_sell_all(sym)
                self.risk_manager.remove_position(sym)
                executed.append(
                    {
                        "symbol": sym,
                        "side": "SELL",
                        "amount": "all",
                        "reason": exit_sig["reason"],
                        "type": "risk_exit",
                    }
                )

        # ── Guardrail constants ──────────────────────────────────────
        MAX_LLM_TRADES = 3
        CASH_RESERVE_PCT = 0.10
        MIN_ALLOC_PCT = 0.03
        MAX_ALLOC_PCT = 0.20  # raised from 0.15 — high conviction + trend can go higher
        DEFAULT_ALLOC_CAP = 0.15  # default cap if no trend justification
        MIN_HOLD_HOURS = 6  # absolute floor (LLM can request 6-24)
        DEFAULT_HOLD_HOURS = 12
        MAX_HOLD_HOURS = 24

        trades = analysis.get("trades", [])
        llm_trade_count = 0

        # Build set of signal-confirmed symbols for medium-conviction gating
        signal_symbols = set()
        for sig in ctx.get("signals", []):
            if sig.get("strength", 0) >= 50:
                signal_symbols.add(sig.get("symbol"))

        # Dedup: track (symbol, action) pairs already acted on this session
        acted_this_session = set()

        for trade in trades:
            if llm_trade_count >= MAX_LLM_TRADES:
                logger.info(
                    f"LLM trade limit reached ({MAX_LLM_TRADES}) -- skipping remaining"
                )
                break

            sym = trade.get("symbol")
            action = trade.get("action", "").upper()
            conviction = trade.get("conviction", "low")
            reasoning = trade.get("reasoning", {})
            guardrail_notes = []

            if not sym or not action:
                continue

            # ── Validate structured reasoning ────────────────────────
            if not reasoning or not isinstance(reasoning, dict):
                # Accept legacy flat rationale as fallback
                if trade.get("rationale") and isinstance(trade["rationale"], str):
                    reasoning = {"thesis": trade["rationale"]}
                    trade["reasoning"] = reasoning
                    guardrail_notes.append("legacy_rationale_format")
                    logger.info(f"LLM trade {sym}: using legacy rationale format")
                else:
                    logger.warning(f"LLM skip: {action} {sym} -- no reasoning provided")
                    continue

            if not reasoning.get("thesis"):
                logger.warning(f"LLM skip: {action} {sym} -- reasoning has no thesis")
                continue

            # Normalize: accept both old 'supporting' and new 'quant_support'/'research_support'
            if "supporting" in reasoning and "quant_support" not in reasoning:
                reasoning["quant_support"] = reasoning.pop("supporting")
            if "research_support" not in reasoning:
                reasoning["research_support"] = []

            # Duplicate prevention
            dedup_key = (sym, action)
            if dedup_key in acted_this_session:
                logger.info(f"LLM skip: duplicate {action} {sym} in same session")
                continue

            # Conviction gating
            if conviction == "low":
                logger.info(f"LLM skip: low-conviction {action} {sym}")
                continue
            if conviction == "medium" and sym not in signal_symbols:
                logger.info(
                    f"LLM skip: medium-conviction {action} {sym} (no signal confirmation)"
                )
                continue

            # ── Resolve adjustable allocation ────────────────────────
            requested_pct = trade.get("amount_pct", 0.10)
            # Get trend info for this symbol
            sym_trend = ctx.get("trends", {}).get(sym, {})
            trend_label = sym_trend.get("trend", "unknown")
            trend_strength = sym_trend.get("strength", "unknown")

            # Allow up to 20% only if: high conviction + confirmed trend + strong ADX
            alloc_cap = DEFAULT_ALLOC_CAP
            if (
                conviction == "high"
                and trend_label in ("uptrend", "downtrend")
                and trend_strength in ("strong", "moderate")
            ):
                alloc_cap = MAX_ALLOC_PCT
                guardrail_notes.append(
                    f"alloc_cap_raised_to_{MAX_ALLOC_PCT:.0%}_trend_confirmed"
                )

            amount_pct = max(MIN_ALLOC_PCT, min(requested_pct, alloc_cap))
            if amount_pct != requested_pct:
                guardrail_notes.append(
                    f"allocation_clamped_{requested_pct:.2f}_to_{amount_pct:.2f}"
                )
            amount_usd = portfolio_value * amount_pct

            # ── Resolve adjustable hold period ───────────────────────
            requested_hold = trade.get("hold_hours_override", DEFAULT_HOLD_HOURS)
            if requested_hold is None:
                requested_hold = DEFAULT_HOLD_HOURS
            hold_hours = max(MIN_HOLD_HOURS, min(float(requested_hold), MAX_HOLD_HOURS))
            if hold_hours != DEFAULT_HOLD_HOURS:
                guardrail_notes.append(f"hold_period_set_to_{hold_hours:.0f}h")

            # Set rich trade context for journal + DB
            regime_label = ctx.get("regime", {}).get("label")
            self.portfolio.set_trade_context(
                sym,
                signal_type="llm",
                signal_strength=None,
                exit_reason="llm_signal",
                market_regime=regime_label,
            )
            # Store the full decision context for the DB INSERT
            self.portfolio._pending_decision_context = self._build_decision_context(
                trade, ctx, guardrail_notes
            )

            if action == "BUY":
                # Position concentration check
                current_pos_value = 0
                if sym in self.portfolio.positions:
                    price = self.portfolio.get_current_price(sym)
                    current_pos_value = (
                        price * self.portfolio.positions[sym]["quantity"]
                    )
                new_pct = (current_pos_value + amount_usd) / portfolio_value
                if new_pct > MAX_POSITION_PCT:
                    logger.warning(
                        f"LLM skip: {sym} buy would exceed {MAX_POSITION_PCT:.0%} position limit"
                    )
                    continue

                # Cash reserve
                cash_floor = portfolio_value * CASH_RESERVE_PCT
                available_cash = self.portfolio.capital - cash_floor
                if amount_usd > available_cash:
                    guardrail_notes.append(
                        f"amount_reduced_cash_reserve_{amount_usd:.2f}_to_{available_cash:.2f}"
                    )
                    amount_usd = available_cash
                if amount_usd < 10:
                    logger.info(
                        f"LLM skip: {sym} buy -- insufficient cash after reserve"
                    )
                    continue

                success = self.portfolio.execute_buy(sym, amount_usd)
                if success:
                    self.risk_manager.record_entry(
                        sym, self.portfolio.get_current_price(sym)
                    )
                    executed.append(
                        {
                            "symbol": sym,
                            "side": "BUY",
                            "amount": round(amount_usd, 2),
                            "conviction": conviction,
                            "reasoning": reasoning,
                            "guardrail_adjustments": guardrail_notes,
                        }
                    )
                    llm_trade_count += 1
                    acted_this_session.add(dedup_key)

            elif action == "SELL":
                if sym not in self.portfolio.positions:
                    continue

                # Adjustable hold period
                entry_date_str = self.portfolio.entry_prices.get(f"{sym}_date")
                if entry_date_str:
                    try:
                        entry_dt = datetime.fromisoformat(entry_date_str)
                        held_hours = (datetime.now() - entry_dt).total_seconds() / 3600
                        if held_hours < hold_hours:
                            logger.info(
                                f"LLM skip: {sym} sell -- held {held_hours:.1f}h < {hold_hours:.0f}h minimum"
                            )
                            continue
                    except Exception:
                        pass

                qty = self.portfolio.positions[sym]["quantity"]
                # LLM can specify sell_pct (0.25-1.0), fallback to conviction-based
                sell_pct = trade.get("sell_pct")
                if sell_pct is not None:
                    sell_frac = max(0.25, min(float(sell_pct), 1.0))
                    if sell_frac != sell_pct:
                        guardrail_notes.append(
                            f"sell_pct_clamped_{sell_pct}_to_{sell_frac}"
                        )
                else:
                    sell_frac = 1.0 if conviction == "high" else 0.5
                sell_qty = qty * sell_frac

                success = self.portfolio.execute_sell(sym, sell_qty)
                if success:
                    if sym not in self.portfolio.positions:
                        self.risk_manager.remove_position(sym)
                    executed.append(
                        {
                            "symbol": sym,
                            "side": "SELL",
                            "amount": round(sell_qty, 6),
                            "conviction": conviction,
                            "reasoning": reasoning,
                            "guardrail_adjustments": guardrail_notes,
                        }
                    )
                    llm_trade_count += 1
                    acted_this_session.add(dedup_key)

        return executed

    # ── Main Decision Loop ───────────────────────────────────────────────

    def run_daily_review(self) -> Dict:
        """
        Run the full daily trading review (two-stage pipeline):
        1. Run Research Agent (Stage 1) — qualitative context
        2. Gather quantitative context
        3. Get LLM trading analysis (Stage 2) — combining both
        4. Execute recommended trades
        5. Handle smart rebalancing
        6. Store analysis in market intelligence
        7. Report to Telegram
        """
        logger.info("=== Starting daily trading review (two-stage pipeline) ===")

        # Stage 1: Research Agent — qualitative intelligence
        research_brief = None
        try:
            logger.info("Stage 1: Running Research Agent...")
            research_brief = self.research_agent.run()
            logger.info(
                f"Stage 1 complete: narrative={len(research_brief.market_narrative)} chars, "
                f"risk_flags={len(research_brief.risk_flags)}, "
                f"symbols={len(research_brief.symbol_briefs)}"
            )
        except Exception as e:
            logger.warning(f"Research Agent failed — proceeding with quant-only: {e}")

        # Stage 2: Gather quantitative context
        ctx = self.gather_context()
        ctx["_research_brief"] = research_brief
        logger.info(
            f"Context gathered: {len(ctx.get('signals', []))} signals, "
            f"regime={ctx.get('regime', {}).get('label', '?')}"
        )

        # Stage 2: Get LLM trading analysis (informed by research)
        prompt = self._build_analysis_prompt(ctx)
        raw_response = self._call_llm(prompt)
        analysis = self._parse_llm_response(raw_response)

        if not analysis:
            logger.warning(
                "LLM analysis failed — falling back to rule-based signals only"
            )
            analysis = {
                "market_assessment": "LLM unavailable — using rule-based signals",
                "risk_level": "unknown",
                "trades": [],
                "rebalance_recommended": False,
                "hold_rationale": "LLM analysis unavailable",
                "watchlist": [],
            }

        logger.info(f"LLM analysis: {analysis.get('market_assessment', 'N/A')}")
        logger.info(f"Recommended trades: {len(analysis.get('trades', []))}")

        # 3. Execute trades
        executed = self.execute_trades(analysis, ctx)
        logger.info(f"Executed: {len(executed)} trades")

        # 4. Smart rebalancing (if recommended or drift-triggered)
        rebalance_result = None
        if analysis.get("rebalance_recommended", False):
            try:
                scores = {
                    sym: data.get("total_score", 50)
                    for sym, data in ctx.get("scores", {}).items()
                }
                rebalance_result = self.smart_rebalancer.execute_rebalance(
                    self.portfolio, scores
                )
                logger.info(f"Rebalance: {rebalance_result.get('reason', 'N/A')}")
            except Exception as e:
                logger.warning(f"Rebalancing failed: {e}")

        # 5. Store in market intelligence
        try:
            snapshot = {
                "date": ctx["date"],
                "regime": ctx.get("regime", {}).get("label", "unknown"),
                "vix": ctx.get("regime", {}).get("vix"),
                "btc_spy_corr": ctx.get("regime", {}).get("btc_spy_corr"),
                "portfolio_value": ctx.get("portfolio", {}).get("value", 0),
                "signals": ctx.get("signals", []),
                "trades_executed": executed,
                "analysis": analysis.get("market_assessment", ""),
                "risk_level": analysis.get("risk_level", "unknown"),
            }
            self.market_intel.store_daily_snapshot(snapshot)

            # Store individual trade rationales (structured reasoning when available)
            for trade in executed:
                reasoning = trade.get("reasoning", {})
                if isinstance(reasoning, dict) and reasoning.get("thesis"):
                    rationale_text = reasoning["thesis"]
                    supporting = reasoning.get("supporting", [])
                    if supporting:
                        rationale_text += (
                            f" Supporting: {', '.join(str(s) for s in supporting[:5])}."
                        )
                    contradicting = reasoning.get("contradicting", [])
                    if contradicting:
                        rationale_text += f" Contradicting: {', '.join(str(s) for s in contradicting[:5])}."
                    if reasoning.get("risk_note"):
                        rationale_text += f" Risk: {reasoning['risk_note']}"
                else:
                    rationale_text = trade.get("reason", "No rationale provided")
                self.market_intel.store_trade_rationale(
                    trade["symbol"],
                    trade["side"],
                    rationale_text,
                    ctx["date"],
                )
        except Exception as e:
            logger.warning(f"Market intelligence storage failed: {e}")

        # 6. Build report
        result = {
            "timestamp": ctx["timestamp"],
            "context": ctx,
            "analysis": analysis,
            "executed_trades": executed,
            "rebalance": rebalance_result,
        }

        return result

    def format_telegram_report(self, result: Dict) -> str:
        """Format the daily review as a Telegram message."""
        lines = []
        analysis = result.get("analysis", {})
        executed = result.get("executed_trades", [])
        ctx = result.get("context", {})
        regime = ctx.get("regime", {})

        mode = result.get("mode", "morning")
        title = (
            "**Morning Strategic Review**"
            if mode == "morning"
            else "**Midday Tactical Check**"
        )
        lines.append(title)
        lines.append(f"{datetime.now().strftime('%Y-%m-%d %H:%M')}")
        lines.append("")

        # Market assessment
        lines.append(f"**Market:** {analysis.get('market_assessment', 'N/A')}")
        lines.append(f"**Risk Level:** {analysis.get('risk_level', 'N/A')}")
        regime_emoji = {"risk-on": "🟢", "neutral": "🟡", "risk-off": "🔴"}.get(
            regime.get("label", ""), "❓"
        )
        lines.append(
            f"**Regime:** {regime_emoji} {regime.get('label', '?')} (VIX {regime.get('vix', '?')})"
        )
        lines.append("")

        # Portfolio
        portfolio = ctx.get("portfolio", {})
        lines.append(
            f"**Portfolio:** ${portfolio.get('value', 0):.2f} (Cash: ${portfolio.get('cash', 0):.2f})"
        )
        lines.append("")

        # Executed trades
        if executed:
            lines.append(f"**Trades Executed: {len(executed)}**")
            for t in executed:
                emoji = "🟢" if t["side"] == "BUY" else "🔴"
                amount = t.get("amount", "all")
                if isinstance(amount, (int, float)):
                    amount = f"${amount:.2f}"
                lines.append(
                    f"{emoji} {t['side']} {t['symbol']} ({amount}) [{t.get('conviction', '?')}]"
                )
                # Show structured reasoning if available
                reasoning = t.get("reasoning", {})
                if isinstance(reasoning, dict) and reasoning.get("thesis"):
                    lines.append(f"   {reasoning['thesis'][:120]}")
                    quant = reasoning.get("quant_support", reasoning.get("supporting", []))
                    if quant:
                        lines.append(
                            f"   Quant: {', '.join(str(s) for s in quant[:3])}"
                        )
                    research = reasoning.get("research_support", [])
                    if research:
                        lines.append(
                            f"   Research: {', '.join(str(s) for s in research[:3])}"
                        )
                    contradicting = reasoning.get("contradicting", [])
                    if contradicting:
                        lines.append(
                            f"   Against: {', '.join(str(s) for s in contradicting[:3])}"
                        )
                elif t.get("reason"):
                    lines.append(f"   {t['reason'][:80]}")
                # Show guardrail adjustments if any
                adjustments = t.get("guardrail_adjustments", [])
                if adjustments:
                    lines.append(f"   [guardrails: {', '.join(adjustments[:3])}]")
        else:
            hold_reason = analysis.get("hold_rationale", "No compelling opportunities")
            lines.append(f"**No trades.** {hold_reason}")
        lines.append("")

        # Rebalance
        rebalance = result.get("rebalance")
        if rebalance and rebalance.get("rebalanced"):
            lines.append(
                f"**Rebalanced:** {rebalance['trades_completed']} trades (fees ${rebalance.get('total_fees', 0):.4f})"
            )
            lines.append("")

        # Watchlist
        watchlist = analysis.get("watchlist", [])
        if watchlist:
            lines.append("**Watchlist:**")
            for item in watchlist[:5]:
                lines.append(f"  {item}")
            lines.append("")

        # Truncate for Telegram
        report = "\n".join(lines)
        if len(report) > 3900:
            report = report[:3900] + "\n..."
        return report

    def send_telegram(self, message: str) -> bool:
        """Send report to Telegram via Bot API."""
        try:
            import urllib.request
            import urllib.parse

            token = os.getenv(
                "TELEGRAM_BOT_TOKEN", "8724014451:AAGpisAWj86i8qmkOtfb4mCBSpiPfZd0ROI"
            )
            chat_id = os.getenv("TELEGRAM_CHAT_ID", "1806720995")
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            data = urllib.parse.urlencode(
                {"chat_id": chat_id, "text": message}
            ).encode()
            req = urllib.request.Request(url, data=data, method="POST")
            resp = urllib.request.urlopen(req, timeout=30)
            return resp.status == 200
        except Exception as e:
            logger.error(f"Telegram send failed: {e}")
            return False

    # ── Midday Tactical Review ────────────────────────────────────────────

    def run_midday_review(self) -> Dict:
        """
        Lighter tactical review for midday runs (12:30 PM, 4:30 PM).
        Only acts on high-conviction signals (strength > 70).
        Uses gpt-4o-mini for speed. No rebalancing.
        """
        logger.info("=== Starting midday tactical review ===")

        ctx = self.gather_context()

        # Filter to only strong signals
        strong_signals = [
            s for s in ctx.get("signals", []) if s.get("strength", 0) >= 70
        ]
        ctx["signals"] = strong_signals

        if (
            not strong_signals
            and not ctx.get("risk_exits")
            and not ctx.get("recent_risk_events")
        ):
            logger.info("Midday: no strong signals or risk events — skipping LLM")
            return {
                "timestamp": ctx["timestamp"],
                "context": ctx,
                "analysis": {
                    "market_assessment": "Quiet midday — no strong signals",
                    "trades": [],
                    "risk_level": "low",
                },
                "executed_trades": [],
                "rebalance": None,
                "mode": "midday",
            }

        # Build a shorter prompt
        prompt = self._build_midday_prompt(ctx)
        raw_response = self._call_llm_fast(prompt)
        analysis = self._parse_llm_response(raw_response)

        if not analysis:
            analysis = {
                "market_assessment": "LLM unavailable",
                "trades": [],
                "risk_level": "unknown",
            }

        # Only execute high-conviction trades
        if analysis.get("trades"):
            analysis["trades"] = [
                t for t in analysis["trades"] if t.get("conviction") == "high"
            ]

        executed = self.execute_trades(analysis, ctx)

        # Store in memory (lighter)
        try:
            self.market_intel.store(
                f"Midday review: {analysis.get('market_assessment', '')}. Trades: {len(executed)}.",
                "daily_snapshot",
                metadata={"mode": "midday", "trades": len(executed)},
            )
        except Exception:
            pass

        result = {
            "timestamp": ctx["timestamp"],
            "context": ctx,
            "analysis": analysis,
            "executed_trades": executed,
            "rebalance": None,
            "mode": "midday",
        }
        return result

    def _build_midday_prompt(self, ctx: Dict) -> str:
        """Shorter prompt for midday tactical checks."""
        lines = []
        lines.append(
            "You are Argus, a financial data analyst and portfolio manager. This is a MIDDAY tactical check."
        )
        lines.append("Only recommend HIGH-CONVICTION trades. Be selective.")
        lines.append("")

        # Portfolio summary (compact)
        portfolio = ctx.get("portfolio", {})
        lines.append(
            f"Portfolio: ${portfolio.get('value', 0):.2f} | Cash: ${portfolio.get('cash', 0):.2f}"
        )
        positions = portfolio.get("positions", {})
        if positions:
            pos_str = ", ".join(
                f"{s}: ${p['value']:.0f} ({p['pnl_pct']:+.1f}%)"
                for s, p in positions.items()
            )
            lines.append(f"Positions: {pos_str}")
        lines.append("")

        # Regime (compact)
        regime = ctx.get("regime", {})
        lines.append(
            f"Regime: {regime.get('label', '?')} (VIX {regime.get('vix', '?')}) | BTC-SPY: {regime.get('btc_spy_corr', '?')}"
        )
        lines.append("")

        # Only strong signals
        signals = ctx.get("signals", [])
        if signals:
            lines.append("STRONG SIGNALS (strength >= 70):")
            for sig in signals:
                lines.append(
                    f"  {sig.get('symbol')}: {sig.get('action')} strength={sig.get('strength', 0):.0f} | {sig.get('reason', '')[:80]}"
                )
            lines.append("")

        # Recent risk events
        risk_events = ctx.get("recent_risk_events", [])
        if risk_events:
            lines.append("RECENT RISK EVENTS:")
            for event in risk_events[:3]:
                for ex in event.get("exits", []):
                    lines.append(
                        f"  {ex['type']} {ex['symbol']}: {ex.get('reason', '')[:80]}"
                    )
            lines.append("")

        lines.append(
            'Respond in JSON: {"market_assessment": "...", "risk_level": "...", "trades": [{"symbol": "...", "action": "BUY|SELL", "amount_pct": 0.05, "conviction": "high", "rationale": "..."}], "watchlist": ["..."]}'
        )

        return "\n".join(lines)

    def _call_llm_fast(self, prompt: str) -> Optional[str]:
        """Use gpt-4o-mini for faster midday analysis."""
        OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
        if OPENAI_API_KEY:
            try:
                import openai

                client = openai.OpenAI(api_key=OPENAI_API_KEY)
                resp = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {
                            "role": "system",
                            "content": "You are a financial data analyst. Respond with valid JSON only.",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.1,
                    max_tokens=800,
                )
                return resp.choices[0].message.content.strip()
            except Exception as e:
                logger.warning(f"Fast LLM failed: {e}")
        return self._call_llm(prompt)  # fallback to full model

    # ── Entry Points ─────────────────────────────────────────────────────

    def run(self, send_report: bool = True, mode: str = "morning") -> Dict:
        """Run the trading agent in the specified mode."""
        if mode == "midday":
            result = self.run_midday_review()
        else:
            result = self.run_daily_review()

        if send_report:
            result_mode = result.get("mode", "morning")
            report = self.format_telegram_report(result)
            # Skip empty midday reports
            if result_mode == "midday" and not result.get("executed_trades"):
                logger.info("Midday: no trades, skipping Telegram")
                return result
            logger.info(f"Report ({len(report)} chars):\n{report}")
            if self.send_telegram(report):
                logger.info("Report sent to Telegram")
            else:
                logger.warning("Failed to send Telegram report")

        return result


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Trading Agent")
    parser.add_argument(
        "--run", action="store_true", help="Run morning strategic review"
    )
    parser.add_argument(
        "--midday", action="store_true", help="Run midday tactical review"
    )
    parser.add_argument(
        "--no-telegram", action="store_true", help="Skip Telegram report"
    )
    parser.add_argument(
        "--context-only", action="store_true", help="Just gather and print context"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Analyze but don't execute trades"
    )
    args = parser.parse_args()

    agent = TradingAgent()

    if args.context_only:
        ctx = agent.gather_context()
        print(json.dumps(ctx, indent=2, default=str))
    elif args.dry_run:
        # Run Stage 1: Research Agent
        print("=== STAGE 1: RESEARCH AGENT ===")
        try:
            research_brief = agent.research_agent.run()
            print(research_brief.to_prompt_section())
        except Exception as e:
            print(f"Research Agent failed: {e}")
            research_brief = None

        # Run Stage 2: Trading Agent (with research)
        ctx = agent.gather_context()
        ctx["_research_brief"] = research_brief
        prompt = agent._build_analysis_prompt(ctx)
        print("\n=== STAGE 2: TRADING AGENT PROMPT ===")
        print(prompt)
        print("\n=== LLM ANALYSIS ===")
        response = agent._call_llm(prompt)
        analysis = agent._parse_llm_response(response)
        print(json.dumps(analysis, indent=2))
    elif args.midday:
        result = agent.run(send_report=not args.no_telegram, mode="midday")
        print(
            json.dumps(
                {
                    "mode": "midday",
                    "analysis": result.get("analysis"),
                    "trades": result.get("executed_trades"),
                },
                indent=2,
                default=str,
            )
        )
    elif args.run:
        result = agent.run(send_report=not args.no_telegram, mode="morning")
        print(
            json.dumps(
                {
                    "mode": "morning",
                    "analysis": result.get("analysis"),
                    "trades": result.get("executed_trades"),
                    "rebalance": result.get("rebalance"),
                },
                indent=2,
                default=str,
            )
        )
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
