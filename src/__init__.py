"""
Edoras — multi-asset trading system.

Package layout
--------------
edoras/
  config.py          — central configuration
  core/              — trading engine (signal_trading, paper_trading, risk_*, exit_*)
  data/              — data collection and indicators
  llm/               — LLM chain, gatekeeper, trading agent, market intelligence
  dex/               — DEX execution and risk rules
  scoring/           — advanced scorer, portfolio optimizer, strategy tracker
  reports/           — report generation, telegram formatting, trade journal, alerts
  cli/               — CLI tool and live dashboard
  backtest/          — backtesting engine and strategies (already a proper package)
  realtime/          — real-time WebSocket feeds
"""

__version__ = "0.2.0"
