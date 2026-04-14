#!/usr/bin/env python3
"""
Research Agent — Stage 1 of the two-agent trading pipeline.

Gathers qualitative context that the quantitative signal engine cannot provide:
  - News sentiment per symbol (RSS feeds → LLM scoring)
  - Research insights from arXiv journal + vector memory
  - Historical pattern analysis (what happened last time conditions looked like this)
  - Macro context summary (VIX regime, correlations, risk events)

Produces a structured ResearchBrief that the Trading Agent (Stage 2) consumes
alongside quantitative signals to make informed trading decisions.

The key insight: the signal engine tells you WHAT the numbers say.
The research agent tells you WHY the market is moving and WHAT ELSE matters.
"""

import json
import logging
import sqlite3
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from config import DB_PATH, PORTFOLIO_SYMBOLS, TOP_CRYPTO_SYMBOLS

logger = logging.getLogger(__name__)


class ResearchBrief:
    """Structured output from the Research Agent, consumed by the Trading Agent."""

    def __init__(self):
        self.timestamp: str = datetime.now().isoformat()
        self.market_narrative: str = ""
        self.macro_context: Dict = {}
        self.symbol_briefs: Dict[str, Dict] = {}
        self.risk_flags: List[str] = []
        self.catalyst_calendar: List[str] = []
        self.research_insights: List[Dict] = []

    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp,
            "market_narrative": self.market_narrative,
            "macro_context": self.macro_context,
            "symbol_briefs": self.symbol_briefs,
            "risk_flags": self.risk_flags,
            "catalyst_calendar": self.catalyst_calendar,
            "research_insights": self.research_insights,
        }

    def to_prompt_section(self) -> str:
        """Format the research brief as a prompt section for the Trading Agent."""
        lines = []

        lines.append("## RESEARCH BRIEF (from Research Agent)")
        lines.append("")

        # Market narrative
        if self.market_narrative:
            lines.append(f"### Market Narrative")
            lines.append(self.market_narrative)
            lines.append("")

        # Macro context
        if self.macro_context:
            lines.append("### Macro Context")
            for key, val in self.macro_context.items():
                lines.append(f"  {key}: {val}")
            lines.append("")

        # Risk flags
        if self.risk_flags:
            lines.append("### Risk Flags")
            for flag in self.risk_flags:
                lines.append(f"  ⚠ {flag}")
            lines.append("")

        # Per-symbol research
        if self.symbol_briefs:
            lines.append("### Per-Symbol Research")
            for sym, brief in self.symbol_briefs.items():
                sentiment = brief.get("sentiment", {})
                score = sentiment.get("score", 0)
                conf = sentiment.get("confidence", 0)
                summary = sentiment.get("summary", "No news")

                sentiment_label = (
                    "bullish" if score > 0.2
                    else "bearish" if score < -0.2
                    else "neutral"
                )

                lines.append(f"  **{sym}**:")
                lines.append(
                    f"    Sentiment: {sentiment_label} ({score:+.2f}, conf={conf:.1f}) — {summary[:120]}"
                )

                # Historical context for this symbol
                hist = brief.get("historical_context", "")
                if hist:
                    lines.append(f"    History: {hist[:150]}")

                # Research insights relevant to this symbol
                insights = brief.get("research_notes", [])
                for note in insights[:2]:
                    lines.append(f"    Research: {note[:120]}")

                lines.append("")

        # Catalyst calendar
        if self.catalyst_calendar:
            lines.append("### Upcoming Catalysts")
            for item in self.catalyst_calendar:
                lines.append(f"  - {item}")
            lines.append("")

        # Research insights (cross-cutting)
        if self.research_insights:
            lines.append("### Research Insights (from reading)")
            for insight in self.research_insights[:3]:
                lines.append(
                    f"  [{insight.get('date', '?')}] {insight.get('content', '')[:150]}"
                )
            lines.append("")

        return "\n".join(lines)


class ResearchAgent:
    """
    Stage 1: Gather qualitative context for the Trading Agent.

    Runs before the Trading Agent and produces a ResearchBrief containing
    everything the LLM trader needs beyond raw numbers.
    """

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._sentiment = None
        self._market_intel = None
        self._llm_chain = None

    # ── Lazy components ──────────────────────────────────────────────────

    @property
    def sentiment(self):
        if self._sentiment is None:
            from llm.sentiment import CryptoSentiment
            self._sentiment = CryptoSentiment(db_path=self.db_path)
        return self._sentiment

    @property
    def market_intel(self):
        if self._market_intel is None:
            from llm.market_intelligence import MarketIntelligence
            self._market_intel = MarketIntelligence(db_path=self.db_path)
        return self._market_intel

    @property
    def llm_chain(self):
        if self._llm_chain is None:
            from llm.llm_chain import LLMChain
            self._llm_chain = LLMChain(
                system_prompt=(
                    "You are a financial research analyst. Synthesize market data, "
                    "news sentiment, and historical patterns into concise research briefs. "
                    "Be specific about risks and catalysts. Always respond with valid JSON."
                ),
                temperature=0.3,
                max_tokens=1500,
                timeout=30,
                cache_ttl=600,  # 10 min cache — research doesn't change fast
                fallback_json={
                    "market_narrative": "Research unavailable — proceed with quantitative data only.",
                    "risk_flags": [],
                    "catalyst_calendar": [],
                },
            )
        return self._llm_chain

    # ── Data Collection ──────────────────────────────────────────────────

    def _gather_sentiment(self, symbols: List[str]) -> Dict[str, Dict]:
        """Fetch fresh sentiment for all symbols. Returns {symbol: sentiment_dict}."""
        results = {}
        for sym in symbols:
            try:
                sent = self.sentiment.get_symbol_sentiment(sym)
                results[sym] = sent
            except Exception as e:
                logger.warning(f"Sentiment fetch failed for {sym}: {e}")
                results[sym] = {
                    "score": 0.0, "confidence": 0.0,
                    "summary": "No data available", "headline_count": 0,
                }
        return results

    def _gather_recent_research(self, limit: int = 5) -> List[Dict]:
        """Pull recent research insights from the vector store."""
        try:
            return self.market_intel.get_recent(
                category="research", days_back=14, limit=limit
            )
        except Exception as e:
            logger.warning(f"Research retrieval failed: {e}")
            return []

    def _gather_historical_patterns(
        self, regime_label: str, vix: float = None, btc_spy_corr: float = None
    ) -> List[Dict]:
        """Find past market conditions similar to now."""
        try:
            current_snapshot = {
                "regime": regime_label,
                "vix": vix,
                "btc_spy_corr": btc_spy_corr,
            }
            similar = self.market_intel.find_similar_conditions(
                current_snapshot, top_k=5
            )
            return similar
        except Exception as e:
            logger.warning(f"Historical pattern search failed: {e}")
            return []

    def _gather_trade_journal_context(self) -> Dict:
        """Get trade journal performance data for the narrative."""
        try:
            from reports.trade_journal import TradeJournal
            journal = TradeJournal(db_path=self.db_path)
            return {
                "by_signal": journal.get_performance_by_signal_type(),
                "by_regime": journal.get_performance_by_regime(),
            }
        except Exception as e:
            logger.warning(f"Trade journal read failed: {e}")
            return {"by_signal": [], "by_regime": []}

    def _gather_regime_context(self) -> Dict:
        """Get current market regime data."""
        try:
            from data.correlation_tracker import CorrelationTracker
            ct = CorrelationTracker(db_path=self.db_path)
            regime, vix = ct.detect_regime()
            btc_corrs = ct.btc_equity_correlations(30)
            beta = ct.portfolio_beta_vs_btc()
            return {
                "label": regime,
                "vix": round(vix, 1) if vix else None,
                "btc_spy_corr": round(btc_corrs.get("BTC-SPY", 0) or 0, 3),
                "btc_qqq_corr": round(btc_corrs.get("BTC-QQQ", 0) or 0, 3),
                "portfolio_beta": round(beta, 2) if beta else None,
            }
        except Exception as e:
            logger.warning(f"Regime context failed: {e}")
            return {"label": "unknown", "vix": None}

    # ── Narrative Synthesis ──────────────────────────────────────────────

    def _synthesize_narrative(
        self,
        regime: Dict,
        sentiment_data: Dict[str, Dict],
        journal_data: Dict,
        historical_patterns: List[Dict],
    ) -> Dict:
        """Use LLM to synthesize all qualitative data into a market narrative."""

        # Build the synthesis prompt
        lines = []
        lines.append("Synthesize this market data into a concise research brief.")
        lines.append("")

        # Regime
        lines.append(f"## Current Regime")
        lines.append(f"  VIX: {regime.get('vix', 'N/A')} → Regime: {regime.get('label', 'unknown')}")
        lines.append(f"  BTC-SPY correlation: {regime.get('btc_spy_corr', 'N/A')}")
        lines.append(f"  Portfolio beta vs BTC: {regime.get('portfolio_beta', 'N/A')}")
        lines.append("")

        # Sentiment summary
        lines.append("## Sentiment Across Portfolio")
        bullish = []
        bearish = []
        for sym, sent in sentiment_data.items():
            score = sent.get("score", 0)
            if sent.get("headline_count", 0) == 0:
                continue
            if score > 0.2:
                bullish.append(f"{sym} ({score:+.2f}: {sent.get('summary', '')[:60]})")
            elif score < -0.2:
                bearish.append(f"{sym} ({score:+.2f}: {sent.get('summary', '')[:60]})")
        if bullish:
            lines.append(f"  Bullish: {'; '.join(bullish)}")
        if bearish:
            lines.append(f"  Bearish: {'; '.join(bearish)}")
        if not bullish and not bearish:
            lines.append("  No strong sentiment signals from news.")
        lines.append("")

        # Journal performance
        journal_by_signal = journal_data.get("by_signal", [])
        journal_by_regime = journal_data.get("by_regime", [])
        if journal_by_signal:
            lines.append("## Historical Trade Performance")
            for j in journal_by_signal:
                lines.append(
                    f"  {j['signal_type']}: {j['total_trades']} trades, "
                    f"{j.get('win_rate', 0)}% win rate, avg {j['avg_return_pct']:+.2f}%"
                )
        if journal_by_regime:
            lines.append("  By regime:")
            for j in journal_by_regime:
                lines.append(
                    f"    {j['regime']}: {j['total_trades']} trades, "
                    f"{j.get('win_rate', 0)}% win rate"
                )
        lines.append("")

        # Historical similar conditions
        if historical_patterns:
            lines.append("## Similar Past Conditions")
            for h in historical_patterns[:3]:
                lines.append(
                    f"  [{h['date']}] (sim={h['similarity']:.2f}): {h['content'][:150]}"
                )
            lines.append("")

        lines.append("## Your Task")
        lines.append("Respond with this JSON:")
        lines.append("```json")
        lines.append("{")
        lines.append('  "market_narrative": "2-4 sentence synthesis of current market state, '
                      'what is driving prices, and what to watch for",')
        lines.append('  "risk_flags": ["list of specific risks worth flagging — '
                      'regime contradictions, sentiment divergence, historical failure patterns"],')
        lines.append('  "catalyst_calendar": ["upcoming events or conditions that could move markets — '
                      'be specific if news mentions any"]')
        lines.append("}")
        lines.append("```")

        prompt = "\n".join(lines)
        result = self.llm_chain.call_with_parse(prompt)
        return result

    # ── Main Flow ────────────────────────────────────────────────────────

    def run(self, symbols: List[str] = None) -> ResearchBrief:
        """
        Execute the full research pipeline and produce a ResearchBrief.

        Args:
            symbols: List of symbols to research. Defaults to portfolio symbols.

        Returns:
            ResearchBrief ready for the Trading Agent to consume.
        """
        if symbols is None:
            symbols = list(set(PORTFOLIO_SYMBOLS + TOP_CRYPTO_SYMBOLS[:5]))

        logger.info(f"Research Agent: starting research for {len(symbols)} symbols")
        brief = ResearchBrief()

        # 1. Gather regime context
        regime = self._gather_regime_context()
        brief.macro_context = regime
        logger.info(f"Research Agent: regime={regime.get('label')}, VIX={regime.get('vix')}")

        # 2. Gather sentiment for all symbols
        sentiment_data = self._gather_sentiment(symbols)
        symbols_with_news = sum(
            1 for s in sentiment_data.values() if s.get("headline_count", 0) > 0
        )
        logger.info(
            f"Research Agent: sentiment gathered, {symbols_with_news}/{len(symbols)} had news"
        )

        # 3. Gather recent research insights
        research = self._gather_recent_research(limit=5)
        brief.research_insights = [
            {"date": r.get("date", ""), "content": r.get("content", "")[:200]}
            for r in research
        ]
        logger.info(f"Research Agent: {len(research)} research insights retrieved")

        # 4. Gather historical pattern matches
        historical = self._gather_historical_patterns(
            regime_label=regime.get("label", "unknown"),
            vix=regime.get("vix"),
            btc_spy_corr=regime.get("btc_spy_corr"),
        )
        logger.info(f"Research Agent: {len(historical)} historical pattern matches")

        # 5. Gather trade journal for narrative context
        journal_data = self._gather_trade_journal_context()

        # 6. Build per-symbol briefs
        for sym in symbols:
            sym_brief = {
                "sentiment": sentiment_data.get(sym, {}),
            }

            # Find historical context specific to this symbol
            sym_history = []
            for h in historical:
                content = h.get("content", "")
                if sym in content or sym.replace("-USD", "") in content:
                    sym_history.append(content[:150])
            if sym_history:
                sym_brief["historical_context"] = sym_history[0]

            # Find research insights relevant to this symbol
            sym_research = []
            for r in research:
                content = r.get("content", "")
                meta = r.get("metadata", {})
                # Check if research mentions this symbol's category
                if (
                    sym in content
                    or sym.replace("-USD", "") in content
                    or meta.get("is_finance", False)
                ):
                    sym_research.append(content[:120])
            sym_brief["research_notes"] = sym_research[:2]

            brief.symbol_briefs[sym] = sym_brief

        # 7. Synthesize narrative via LLM
        try:
            narrative_result = self._synthesize_narrative(
                regime, sentiment_data, journal_data, historical
            )
            brief.market_narrative = narrative_result.get("market_narrative", "")
            brief.risk_flags = narrative_result.get("risk_flags", [])
            brief.catalyst_calendar = narrative_result.get("catalyst_calendar", [])
            logger.info("Research Agent: narrative synthesized")
        except Exception as e:
            logger.warning(f"Research Agent: narrative synthesis failed: {e}")
            brief.market_narrative = (
                f"Regime: {regime.get('label', 'unknown')}, VIX: {regime.get('vix', 'N/A')}. "
                f"Sentiment: {symbols_with_news} symbols had news coverage."
            )

        logger.info("Research Agent: brief complete")
        return brief


def main():
    import argparse

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    parser = argparse.ArgumentParser(description="Research Agent — Stage 1")
    parser.add_argument("--run", action="store_true", help="Run full research pipeline")
    parser.add_argument(
        "--symbols", nargs="+", help="Override symbols to research"
    )
    parser.add_argument(
        "--brief", action="store_true", help="Print the research brief as prompt section"
    )
    args = parser.parse_args()

    if args.run or args.brief:
        agent = ResearchAgent()
        brief = agent.run(symbols=args.symbols)

        if args.brief:
            print(brief.to_prompt_section())
        else:
            print(json.dumps(brief.to_dict(), indent=2, default=str))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
