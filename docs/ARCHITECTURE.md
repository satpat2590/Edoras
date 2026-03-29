# Trading System Architecture

Last updated: 2026-03-29 | Verified against code: 2026-03-29

## Overview

Multi-asset, multi-exchange trading analysis and execution system. Covers crypto (Coinbase), prediction markets (Polymarket), equities (yfinance), and cross-asset correlation tracking with VIX-based regime detection. Delivers alerts and reports via OpenClaw → Telegram.

Four named portfolios (Galadriel/paper, Thranduil/live, Elrond/tracked, Arwen/DEX-live), exchange-agnostic WebSocket architecture, and dimension-table-driven metadata. Portfolio-to-venue mapping via `accounts` bridge table. Warehouse redesign Phase 1-3 complete: all read/write queries route through `account_id` (Phase 4 pending: deprecate `portfolio_id` on trades/positions).

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
RSS feeds ──────→ sentiment.py               ├── strategy routing (7 strategies)
                                             ├── risk_manager.py (stops, CB)
                                             ├── regime adjustment (VIX)
DIMENSION TABLES                             ├── multi-portfolio iteration
────────────────                             └── paper_trading.py → trade_journal.py
exchanges, securities,                                │
strategy_registry, portfolios                         ▼
                                             OpenClaw CLI → Telegram
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
| `sentiment.py` | RSS news sentiment via GPT-4o-mini |

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
| `risk_manager.py` | Stop-loss (10%), trailing stop (ATR-based after 5% gain), take-profit scale-out (15/20/25%), circuit breaker (15% drawdown), sector limits (40%) |
| `exit_signals.py` | Data classes for ExitSignal, CircuitBreaker, RiskViolation |

### Backtesting

| Module | Purpose |
|--------|---------|
| `backtester.py` | Event-driven backtester, 7 strategies (BollingerReversion, MultiSignal, ADXTrend, ScoreBased, ScoreBasedRelaxed, MACDCross, ScoreBasedStrategy), 166 backtests complete |

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

- **Circuit breaker**: 15% portfolio drawdown from peak → liquidate all
- **Position concentration**: max 25% per position
- **Sector exposure**: max 40% per sector

### Signal Pipeline (implemented in `signal_trading.py`)

Risk checks run BEFORE buy signals:
1. Portfolio drawdown check (circuit breaker)
2. Per-position stop-loss/trailing/take-profit
3. Sector and concentration violations
4. If circuit breaker active → all buy signals suppressed
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
cd ~/.openclaw/workspace/projects/edoras

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
```bash
# Single-symbol backtest with risk management
python3 backtester.py --symbol BTC-USD --start 2025-04-01 --end 2026-03-01

# Compare with/without risk management
python3 backtester.py --symbol BTC-USD --start 2025-04-01 --end 2026-03-01 --no-risk

# Walk-forward validation
python3 backtester.py --symbol BTC-USD --walk-forward

# MACD crossover baseline
python3 backtester.py --symbol ETH-USD --strategy macd
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
