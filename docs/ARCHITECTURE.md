# Trading System Architecture

Last updated: 2026-04-05 | Verified against code: 2026-04-05

## Overview

Multi-asset, multi-exchange trading analysis and execution system. Covers crypto (Coinbase CEX + Base DEX via Bankr), prediction markets (Polymarket), equities (yfinance), and cross-asset correlation tracking with VIX-based regime detection. Delivers alerts and reports via Hermes → Telegram.

Four named portfolios (Galadriel/paper, Thranduil/live, Elrond/tracked, Arwen/DEX-live), exchange-agnostic WebSocket architecture, and dimension-table-driven metadata. Portfolio-to-venue mapping via `accounts` bridge table. Warehouse redesign Phase 1-3 complete: all read/write queries route through `account_id` (Phase 4 pending: deprecate `portfolio_id` on trades/positions).

Modular backtesting engine (`src/edoras/backtest/`) supports single-symbol and multi-asset portfolio backtesting with per-asset-class fee/risk resolution, adaptive Kelly sizing, multi-timeframe context dispatch, and walk-forward validation with in-sample fitting. 10 active strategies with a `Strategy` base class supporting `generate_signals()`, `generate_signals_multi()` (pairs), `generate_signals_ctx()` (multi-TF), and `fit()` (walk-forward). DB schema links strategy_catalogue ↔ strategy_performance ↔ strategy_registry via additive migrations.

## Data Flow

```
REAL-TIME FEEDS (persistent WebSocket)             ANALYSIS                        EXECUTION
──────────────────────────────────────             ────────                        ─────────
Coinbase WS ──→ base_websocket.py ─┐
(18 crypto)     (5m candle buffer)  │
                                    ├→ 1h rollup ──→ 4h rollup
Polymarket WS ─→ base_websocket.py ─┤               │
(20+ markets)   (price_change→5m)   │               │
                                    │               ▼
                                    │    indicator_calculator.py
REST GAP-FILL                       │    ├── standard profile (17 indicators)
─────────────                       │    └── binary profile (16 indicators)
Coinbase REST ──→ intraday_update   │               │
Polymarket REST → polymarket.py ────┘               ▼
yfinance ───────→ equity_data_collector.py    signal_trading.py
RSS feeds ──────→ sentiment.py               ├── strategy routing (13 strategies)
                                             ├── risk_manager.py (stops, CB)
                                             ├── regime adjustment (VIX)
DIMENSION TABLES                             ├── multi-portfolio iteration
────────────────                             └── paper_trading.py → trade_journal.py
exchanges, securities,                                │
strategy_registry, portfolios                         ▼
                                             Hermes CLI → Telegram

LLM TRADING PIPELINE (Two-Stage)
──────────────────────────────────────────────────────────────────
Stage 1: research_agent.py                Stage 2: trading_agent.py
├── sentiment.py (RSS → LLM scoring)      ├── ResearchBrief (from Stage 1)
├── market_intelligence.py (vector sim)   ├── signal_trading.py (quant signals)
├── research_reader.py (arXiv insights)   ├── advanced_scorer.py (scores)
├── correlation_tracker.py (regime)       ├── portfolio state + journal
└── LLM narrative synthesis               └── LLM trade decisions
         │                                         │
         ▼                                         ▼
    ResearchBrief ──────────────────────→ paper_trading.py → trade_journal.py
```

## Cross-Asset Portfolio Support

Portfolios are **strategy repositories**, not asset-class silos. A single portfolio
can hold crypto, prediction markets, equities, and indices simultaneously. The
execution layer resolves per-asset-class parameters dynamically.

### Asset-Class Profile System (`config.py`)

`ASSET_CLASS_PROFILES` defines per-class defaults for: fee %, stop-loss, trailing
stop, take-profit levels, position cap, sector cap, min trade size, min hold period,
RSI thresholds, indicator profile, and decimal precision.

`get_asset_class_profile(symbol)` resolves a symbol's profile:
1. Queries `securities.asset_class` from the DB
2. Falls back to `get_asset_type()` heuristic
3. Returns the matching profile (default: crypto)

### Where Profiles Are Used

| Module | What It Resolves |
|--------|-----------------|
| `paper_trading.py` | Fee % per trade (`execute_buy`, `execute_sell`) |
| `risk_manager.py` | Stop-loss %, trailing stop %, take-profit levels, position cap per symbol |
| `signal_trading.py` | RSI thresholds, min trade amount, min hold period, max position % |
| `indicator_calculator.py` | Indicator set (`standard` vs `binary`) — dispatched by WebSocket and batch jobs |

### Supported Asset Classes

| Class | Fee | Indicator Profile | Examples |
|-------|-----|-------------------|----------|
| `crypto` | 0.1% | standard (RSI, MACD, BB, ADX) | BTC-USD, ETH-USD, DOGE-USD |
| `equity` | 0% | standard | AAPL, MSFT, SPY |
| `prediction` | 2% | binary (prob_ema, certainty, prob_roc) | PM:BITCOIN-REACH-150K, PM:FED-RATE |
| `index` | 0% | standard | SPY, QQQ, ^GSPC |

Prediction market symbols with `indicator_profile=binary` are skipped by the legacy
RSI/MACD signal generator (they require binary-specific strategies or the Polymarket
overlay pipeline).

## Module Reference

### Infrastructure

| Module | Purpose |
|--------|---------|
| `config.py` | Central config: symbols, thresholds, risk params, portfolio loading from DB |
| `indicator_calculator.py` | Standard indicators (17) + binary indicators (16), profile-gated |

### Real-Time Ingestion

| Module | Purpose |
|--------|---------|
| `realtime/ingest/base_websocket.py` | Exchange-agnostic WS base: candle buffer, flush, rollup, indicator dispatch |
| `realtime/ingest/coinbase_websocket.py` | Coinbase WS: 18 crypto symbols, ticker channel |
| `realtime/ingest/polymarket_websocket.py` | Polymarket WS: 20+ markets, price_change + resolution events |
| `realtime/supervisor.py` | Multi-feed supervisor: FEED_REGISTRY, per-feed error isolation, auto-restart |

### Data Collection

| Module | Purpose |
|--------|---------|
| `historical_backfill.py` | One-time + periodic backfill of 400+ days of Coinbase OHLCV data |
| `crypto_data_collector.py` | Daily crypto data collection and indicator calculation |
| `intraday_update.py` | Intraday crypto REST gap-fill |
| `equity_data_collector.py` | Equity + index data via yfinance with US market hours awareness |
| `dex_data_collector.py` | DEX token OHLCV + metadata via GeckoTerminal (every 2h timer) |
| `providers/polymarket.py` | Polymarket REST: market discovery (Gamma API) + price ingestion (CLOB API) |
| `sentiment.py` | RSS news sentiment via LLMChain (5-tier fallback) |

### LLM Trading Pipeline

| Module | Purpose |
|--------|---------|
| `research_agent.py` | **Stage 1**: Gathers qualitative context (sentiment, historical patterns, arXiv insights, macro regime). Produces a `ResearchBrief` with market narrative, per-symbol sentiment, risk flags, and catalyst calendar. |
| `trading_agent.py` | **Stage 2**: Combines `ResearchBrief` + quantitative signals + scores + portfolio state + trade journal. Dynamic self-preservation rules constrain behavior based on historical win rates. Outputs BUY/SELL decisions requiring both `quant_support` and `research_support` evidence. |
| `llm_chain.py` | Shared 5-tier LLM fallback (DeepSeek → Nous → Claude → GPT-4o → MLX). Per-provider rate limiting, caching, JSON parsing, static fallback guarantee. |
| `llm_gatekeeper.py` | Fail-open BUY signal validator for the signal engine path (separate from the LLM trading pipeline). Batch validation, 5-min cache, APPROVE/REJECT/MODIFY decisions. |
| `market_intelligence.py` | sqlite-vec backed vector store for market context, daily snapshots, trade rationales. Numeric similarity for historical condition matching. |
| `research_reader.py` | arXiv paper ingestion across 7 topic groups (finance, ML, complex systems). LLM-powered reflection journaling. Insights stored in vector memory. |
| `sentiment.py` | Crypto news sentiment from 4 RSS feeds (CoinDesk, Decrypt, CryptoSlate, Bitcoin.com). Per-symbol keyword matching, LLM scoring, SQLite persistence. |
| `vector_store.py` | Unified sqlite-vec backend. 3 collections: market_memory (1536d), trade_outcomes_vec (1536d), workspace_chunks (3072d). |

### Analysis & Scoring

| Module | Purpose |
|--------|---------|
| `advanced_scorer.py` | 5-component weighted scoring (momentum 40%, trend 25%, vol 15%, volume 10%, risk 10%) |
| `enhanced_optimizer.py` | Portfolio optimization, risk metrics, rebalancing suggestions |
| `correlation_tracker.py` | BTC-equity correlations, VIX regime detection, portfolio beta |
| `strategy_tracker.py` | Strategy performance DB, signal hit rates, backtest vs paper comparison |

### Risk Management (Priority 2)

| Module | Purpose |
|--------|---------|
| `risk_manager.py` | Stop-loss (10%), trailing stop (ATR-based after 5% gain), take-profit scale-out (15/20/25%), circuit breaker (15% drawdown, auto-reset after 24h cooldown or ≥80% cash), sector limits (40%) |
| `exit_signals.py` | Data classes for ExitSignal, CircuitBreaker, RiskViolation |

### Backtesting

| Module | Purpose |
|--------|---------|
| `backtest/engine.py` | Event-driven single-symbol backtester with walk-forward validation, per-asset-class fee/risk resolution, adaptive Kelly sizing, and multi-timeframe context dispatch |
| `backtest/portfolio_engine.py` | Multi-asset portfolio backtester: N strategies × N symbols sharing capital, per-symbol FeeModel/RiskConfig, SELLs-before-BUYs ordering, optional periodic rebalancing |
| `backtest/signals.py` | `Signal` dataclass (BUY/SELL/REDUCE/CLOSE) with confidence, target_position_pct, metadata — backward-compatible with legacy dict signals |
| `backtest/risk_config.py` | `RiskConfig` dataclass: configurable stop-loss, trailing stop, take-profit, max position per backtest run. Auto-resolves from `ASSET_CLASS_PROFILES` |
| `backtest/fee_model.py` | `FeeModel` dataclass: per-asset-class fees (crypto 0.1%, equity 0%, prediction 2%) and slippage modeling. Auto-resolves from `ASSET_CLASS_PROFILES` |
| `backtest/context.py` | `StrategyContext` for strategies needing multi-timeframe or reference data (e.g. VIX). Prevents direct DB queries inside strategies, eliminates lookahead bias |
| `backtest/catalogue.py` | Persistent strategy catalogue + performance archive, source tracking (backtest/walk_forward/holdout_gate/portfolio_backtest) |
| `backtest/deployer.py` | Strategy deployment and routing to live system, links registry to qualifying catalogue entry |
| `backtest/validation.py` | Anchored walk-forward (with in-sample fit()), cost sensitivity sweep, holdout gating |
| `backtest/portfolio_state.py` | `PortfolioState`/`PositionState` for multi-asset position tracking with legacy dict conversion |
| `backtest/portfolio_metrics.py` | `PortfolioBacktestResult` with per-symbol equity curves, correlation matrix, P&L contribution |
| `backtest/migrations/` | Additive schema migrations: cross-references between strategy_catalogue ↔ strategy_performance ↔ strategy_registry, trade_outcomes FK fix |
| `backtest/strategies/` | 10 registered strategies (13 total incl. retired). Base class supports `generate_signals()`, `generate_signals_multi()`, `generate_signals_ctx()`, and `fit()` |

### Execution

| Module | Purpose |
|--------|---------|
| `paper_trading.py` | Multi-portfolio paper trading with DB position sync, entry tracking, partial sells. Dual-writes account_id. |
| `trade_journal.py` | Trade outcome recording, performance by signal type/regime, expected value |
| `paper_rebalancing.py` | Weekly rebalancing (Monday 9 AM EDT) |
| `live_executor.py` | Paper/dry-run/live modes. Safety: max $50/order, $200/day, 60s cooldown, env var kill switch |
| `bankr_client.py` | Bankr DEX API client: prompt/poll pattern, balance queries, swap execution |
| `dex_executor.py` | DEX trade execution via Bankr with safety checks + DB sync. Dual-writes account_id. |
| `dex_trading_agent.py` | DEX trading orchestrator: data → indicators → LLM decision → execution |
| `dex_risk_rules.py` | DEX-specific risk checks: liquidity, slippage, holder count, position vs pool size |

### Reporting & Alerts

| Module | Purpose |
|--------|---------|
| `automated_portfolio_report.py` | Daily portfolio snapshot → Telegram |
| `signal_alerts.py` | Signal alerts → Telegram |
| `price_alerts.py` | Price threshold alerts → Telegram |

## Risk Management Rules

### Position-Level (implemented in `risk_manager.py`)

- **Stop-loss**: 10% below entry → sell 100%
- **Trailing stop**: activates after 5% gain, trails at 2x ATR (or 5% from peak if no ATR), breakeven floor
- **Take-profit scale-out**: +15% → sell 33%, +20% → sell 33%, +25% → sell remainder

### Portfolio-Level (implemented in `risk_manager.py`)

- **Circuit breaker**: 15% portfolio drawdown from peak → liquidate all; auto-resets after 24h cooldown or when cash ≥ 80% of portfolio value
- **Position concentration**: max 25% per position
- **Sector exposure**: max 40% per sector

### Signal Pipeline (implemented in `signal_trading.py`)

Risk checks run BEFORE buy signals:
1. Portfolio drawdown check (circuit breaker)
2. Per-position stop-loss/trailing/take-profit
3. Sector and concentration violations
4. If circuit breaker active → attempt auto-reset (cooldown or cash ratio), then suppress buys if still active
5. Regime adjustment: risk-off dampens buys 0.5x, amplifies sells 1.3x

## Asset Universe

### Crypto (Coinbase — WS + REST)
Portfolio: ETH, BTC, XRP, TROLL, BONK, FET, AMP, GRT
Extended: + BNB, SOL, ADA, AVAX, DOGE, DOT, LINK, SHIB, LTC, UNI

### Prediction Markets (Polymarket — WS + REST, no auth)
20+ binary markets: Fed rates, geopolitics, sports, elections, commodities
Symbols: PM:* prefix (e.g. PM:THERE-BE-NO-CHANGE-IN, PM:IRAN-CLOSE-THE-STRAIT-OF)
Auto-discovered from Gamma API, registered in securities table

### Equities (yfinance — REST only)
Watchlist: AAPL, MSFT, GOOGL, AMZN, META, NVDA, TSLA, JPM, JNJ, V

### Indices / Macro (yfinance)
SPY (S&P 500), QQQ (NASDAQ-100), ^VIX

## Database Schema

See [docs/DATABASE.md](DATABASE.md) for full schema reference, table classifications, and key queries.

All data lives in `crypto_data.db` (SQLite). Key table groups: Core Market Data (candlesticks, indicators), Trading (trades, positions, trade_outcomes), Strategy (strategy_registry, strategy_catalogue, strategy_signals_log), Portfolio (portfolios, accounts, paper_snapshots), Dimension (exchanges, securities, traders), Intelligence (market_regime, sentiment_scores, correlations), DEX (dex_tokens, dex_transactions).

## Scheduling

See [docs/OPERATIONS.md](OPERATIONS.md) for full timer schedule, daily trading timeline, and troubleshooting.

Key timers: `crypto-signal-trading` (every 4h), `crypto-intraday-update` (every 2h), `risk-guardian` (every 30m, 7AM-11PM), `trading-agent` (daily 8:45AM), `coinbase-websocket` (24/7 persistent).

## Setup & Operations

### First-time setup
```bash
cd ~/edoras

# 1. Backfill 400+ days of crypto history (~2 min)
python3 historical_backfill.py --days 400

# 2. Collect equity + index data (~3 min)
python3 equity_data_collector.py --collect

# 3. Save first correlation snapshot
python3 correlation_tracker.py --snapshot

# 4. Validate data coverage
python3 historical_backfill.py --validate
python3 equity_data_collector.py --validate
```

### Running backtests
```python
from edoras.backtest import run_backtest, run_portfolio_backtest, STRATEGY_REGISTRY
from edoras.backtest import Backtester, PortfolioBacktester, RiskConfig, FeeModel

# Single-symbol backtest (auto-resolves asset-class fees and risk params)
result = run_backtest("EnhancedScoreBased", "BTC-USD", timeframe="1d",
                      start_date="2025-06-01", end_date="2026-03-01")

# Single-symbol with custom risk/fee params
bt = Backtester(
    risk_config=RiskConfig(stop_loss_pct=0.05, trailing_stop_pct=0.03),
    fee_model=FeeModel(fee_pct=0.002, slippage_bps=5.0),
    sizing_mode="kelly",  # adaptive Kelly sizing with running win/loss stats
)
result = bt.run(STRATEGY_REGISTRY["TSMOM"](), "BTC-USD", "1d", "2025-06-01", "2026-03-01")

# Multi-asset portfolio backtest
result = run_portfolio_backtest([
    {"strategy": "TSMOM", "symbol": "BTC-USD", "weight": 0.4, "timeframe": "1d"},
    {"strategy": "BollingerReversion", "symbol": "ETH-USD", "weight": 0.3, "timeframe": "1d"},
    {"strategy": "MultiSignal", "symbol": "LINK-USD", "weight": 0.3, "timeframe": "1d"},
], start_date="2025-06-01", end_date="2026-03-01")
# result.metrics — portfolio Sharpe, drawdown, etc.
# result.per_symbol_metrics — per-symbol breakdown
# result.correlation_matrix — return correlations

# Walk-forward validation with strategy fitting
from edoras.backtest import anchored_walk_forward
wf = anchored_walk_forward(STRATEGY_REGISTRY["TSMOM"](), "BTC-USD",
                           start_date="2024-06-01", end_date="2026-03-01",
                           oos_months=3, n_folds=4)
```

### Live execution (when ready)
```bash
# Dry run (validates, logs, no real orders)
python3 live_executor.py --mode dry-run --buy BTC-USD --amount 25

# Paper mode (simulated fills at DB price)
python3 live_executor.py --mode paper --buy BTC-USD --amount 25

# Live mode (requires LIVE_TRADING_ENABLED=true)
LIVE_TRADING_ENABLED=true python3 live_executor.py --mode live --buy BTC-USD --amount 25

# Reconcile actual vs expected positions
LIVE_TRADING_ENABLED=true python3 live_executor.py --mode live --reconcile
```

### Reports
```bash
python3 correlation_tracker.py --report     # Cross-asset correlations
python3 correlation_tracker.py --matrix     # Correlation matrix
python3 correlation_tracker.py --regime     # Current VIX regime
python3 historical_backfill.py --validate   # Data coverage
python3 equity_data_collector.py --validate # Equity coverage
```

## Data Quality Requirements

| Metric | Minimum Data Points | Current Status |
|--------|--------------------|----|
| SMA-200 | 200 daily candles | After backfill: YES |
| Sharpe ratio | 30 daily candles | After backfill: YES |
| VaR (95%) | 60 daily candles | After backfill: YES |
| Max drawdown | 30 daily candles | After backfill: YES |
| Rolling correlation | 30 daily candles | After equity collect: YES |

Metrics return `None`/50.0 (neutral) when data is insufficient, rather than producing misleading values.
