# Edoras Trading System

> Last updated: 2026-04-05

## Overview
Multi-asset trading system with 10 backtested strategies, real-time data feeds, 4 portfolios (paper + live CEX + live DEX), and regime-adaptive strategy routing. Structured as an installable Python package (`src/edoras/`).

## Package Layout
```
src/edoras/
  config.py          — central config (symbols, thresholds, DB path, scoring weights)
  core/              — signal_trading, paper_trading, risk_manager, risk_guardian,
                       exit_overlay, exit_signals, smart_rebalancer, regime_monitor
  data/              — indicator_calculator, correlation_tracker, crypto_data_collector,
                       equity_data_collector, data_freshness_monitor, + 5 more collectors
  llm/               — llm_chain, llm_gatekeeper, trading_agent, market_intelligence,
                       research_reader, sentiment, vector_store
  dex/               — dex_executor, dex_trading_agent, dex_risk_rules, bankr_client
  scoring/           — advanced_scorer, enhanced_optimizer (PortfolioOptimizer), strategy_tracker
  reports/           — report_engine, telegram_fmt, trade_journal, price_alerts, signal_alerts
  cli/               — cli, dashboard
  backtest/          — engine, validation, strategies/ (10 registered)
  realtime/          — WebSocket feeds (ingest/, risk/)
```

Root-level `.py` files are shims that forward to the package. Edit `src/edoras/` modules, not the shims.

## Key Entry Points
- `src/edoras/core/signal_trading.py` — main signal generation orchestrator
- `src/edoras/llm/trading_agent.py` — LLM-powered trading agent (DeepSeek primary, 5-tier fallback via `llm_chain`)
- `src/edoras/core/paper_trading.py` — paper trading execution engine (multi-portfolio)
- `src/edoras/core/risk_manager.py` — stop-loss, trailing stop, take-profit, circuit breaker
- `src/edoras/core/risk_guardian.py` — portfolio-level drawdown, concentration, sector limits
- `src/edoras/core/exit_overlay.py` — Layer 2 exit signals (momentum, trend, volatility, correlation contagion)
- `src/edoras/data/indicator_calculator.py` — 17 standard + 16 binary indicators
- `src/edoras/llm/llm_chain.py` — shared 5-tier LLM service (caching, rate limiting, fail-open)
- `src/edoras/llm/llm_gatekeeper.py` — fail-open BUY signal validator (batch, 5-min cache)
- `src/edoras/scoring/enhanced_optimizer.py` — `PortfolioOptimizer`: max-Sharpe / min-variance / risk-parity
- `src/edoras/data/data_freshness_monitor.py` — 15-min staleness checks, 93 feeds, Telegram alerts
- `src/edoras/cli/dashboard.py` — live TUI dashboard (also: `edoras-dashboard` binary)
- `src/edoras/cli/cli.py` — unified CLI (snapshot, trades, signals, health, dex, signal-trace)
- `crypto_data.db` — SQLite, 53 tables (~276 MB)

## Signal Flow
```
Regime detection (HMM/heuristic)
  → Strategy routing (10 strategies; MultiSignal default for unrouted)
    → Data freshness gate (skip stale symbols)
      → Backtested strategy generates signal
        → Exit overlay (momentum, trend, volatility, correlation contagion, time decay)
          → Polymarket overlay (prediction-market probability shifts)
            → Risk manager circuit breaker gate
              → LLM Gatekeeper (validates BUY signals; SELL always bypasses)
                → execute_paper_trades() (regime gate, strength gate, dedup gate)
                  → DB + state persistence + Telegram alert on risk exits
```

## Strategy Registry (10 strategies)

| Strategy | Type | Best Regime |
|----------|------|-------------|
| `EnhancedScoreBased` | Mean-reversion | bull/sideways |
| `ADXTrend` | Trend-following | bull |
| `TSMOM` | Momentum + vol-sizing | bull |
| `TSMOM_3M` | Momentum (3-month) | bull/sideways |
| `BollingerReversion` | Mean-reversion | sideways/bear |
| `PairsTrading` | Stat-arb | sideways |
| `MultiSignal` | Consensus (5 sub-signals) | all (default) |
| `RegimeAware` | Adaptive (HMM-driven) | all |
| `RegimeAware_Heuristic` | Adaptive (no HMM) | all |
| `BearDefensive` | Tight mean-reversion | **bear** (not yet live — pending re-validation) |

## Exit Architecture (3 layers)
1. **Entry Strategy Exits** — the routed strategy generates SELL signals based on its own logic
2. **Exit Overlay** (`src/edoras/core/exit_overlay.py`) — runs on ALL held positions: momentum, trend, volatility, correlation contagion, time deterioration
3. **Risk Manager** — mechanical stop-loss, trailing stop, take-profit, circuit breaker

## Agents
| Agent | Role | Model |
|-------|------|-------|
| Aleph | General-purpose, DEX executor (Arwen) | DeepSeek / Gemini Flash |
| Regi | Quant specialist, strategic trading (Galadriel) | DeepSeek Reasoner |
| Research Agent | Stage 1: qualitative research (sentiment, patterns, narrative) | LLMChain (5-tier fallback) |
| Trading Agent | Stage 2: trade decisions informed by research + quant signals | LLMChain (5-tier fallback) |
| Signal Engine | Quantitative signal-driven execution | N/A (rules-based) |
| Risk Engine | Automated risk management exits | N/A (rules-based) |
| Weekly Rebalancer | Systematic weekly rebalance | N/A (rules-based) |

## LLM Trading Pipeline (Two-Stage)

```
Stage 1: Research Agent (research_agent.py)
  → Gathers: news sentiment, historical patterns, arXiv insights, macro context
  → Produces: ResearchBrief (narrative, per-symbol sentiment, risk flags, catalysts)

Stage 2: Trading Agent (trading_agent.py)
  → Inputs: ResearchBrief + quantitative signals + scores + portfolio + journal
  → Dynamic self-preservation: constrains behavior based on historical win rates
  → Outputs: BUY/SELL decisions with quant_support + research_support reasoning
  → Execution: guardrails (3 trade max, cash reserve, position limits, conviction gating)
```

Research Agent failure is non-fatal — Trading Agent proceeds with quant-only data.
Each trade must cite both quantitative and qualitative evidence in its reasoning.

## Conventions
- Database: always READ-ONLY (`?mode=ro`) for queries
- HMM convergence warnings are EXPECTED — not errors
- Signal strength 0-100 (100 = max confidence, TSMOM_3M)
- Scheduled runs via systemd timers (25+ timers)
- Never modify strategy params without backtesting
- Never execute trades without explicit user approval
- Circuit breaker auto-resets after 24h cooldown or when cash ≥ 80% of portfolio
- Edit `src/edoras/` modules; root-level `.py` files are shims for backwards compat
- Import from the package: `from edoras.core.signal_trading import SignalTradingSystem`
