# Trading System Architecture

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

All data lives in `crypto_data.db` (SQLite):

### Core Data Tables
- `candlesticks` — OHLCV for all asset types (crypto, equity, index, prediction markets)
- `indicators` — 17 standard + 16 binary indicator columns per symbol/timeframe
- `portfolio_analysis` — signal classifications and recommendations
- `collection_log` — data fetch tracking
- `sentiment_scores` — LLM-analyzed news sentiment
- `correlations` — daily cross-asset correlation snapshots
- `market_regime` — VIX level, regime label, BTC-equity correlations

### Trading Tables
- `trades` — all trade records with portfolio_id, account_id (→ accounts), strategy_id (→ strategy_registry), trader_id (→ traders), decision_context JSON, DEX columns (tx_hash, block_number, gas_used, gas_price_gwei, slippage_bps)
- `positions` — current open/closed positions with account_id FK, synced on every trade
- `trade_outcomes` — closed trade journal entries with portfolio_id
- `paper_snapshots` — daily portfolio snapshots, UNIQUE(date, portfolio_id)
- `portfolio_valuations` — portfolio NAV time series: total_nav_usd, cost_basis, unrealized/realized PnL, fees
- `transfers` — capital movements between accounts: deposits, withdrawals, bridges
- `strategy_performance` — 166 backtest results + rolling paper/live snapshots
- `strategy_signals_log` — every signal generated, with execution and outcome tracking

### Dimension Tables
- `exchanges` (venues) — 5 venues (coinbase, yfinance, polymarket, kalshi, bankr) with fee_model, settlement_type, chain/chain_id for DEX
- `securities` — instrument catalog (52+ rows) with canonical_instrument_id (cross-venue asset grouping), decimals (on-chain tokens), chain, contract_address, is_dex
- `accounts` — bridge table: portfolio ↔ venue (4 rows). Each account has account_type (paper, api_key, wallet) and account_external_id
- `strategy_registry` — 7 strategies with strategy_type (momentum, mean_reversion, etc.) and parameters JSON
- `portfolio_strategies` — M:M junction: which strategies assigned to which portfolios
- `portfolios` — 4 portfolios (Galadriel/paper, Thranduil/live, Elrond/tracked, Arwen/DEX-live)
- `traders` — 5 traders (Aleph, Regi agents + Signal Engine, Risk Engine systems + Satyam human)
- `trader_portfolio_access` — M:M: which traders can trade which portfolios

## Scheduling (systemd user timers)

All jobs run as systemd user timers (`~/.config/systemd/user/`) with `Persistent=true`.
This ensures jobs catch up after laptop suspend/resume — cron silently skips them.

### Why systemd over cron

- `Persistent=true` runs missed jobs immediately on wake
- `journalctl --user -u <service>` for logs (no manual log rotation)
- Proper dependencies: `After=network.target`
- Timeout protection: `TimeoutSec=` kills hung jobs
- All timers visible: `systemctl --user list-timers`

### Complete schedule

#### Persistent Services (24/7)

| Service | Purpose |
|---------|---------|
| `coinbase-websocket.service` | Multi-feed supervisor: Coinbase WS (18 crypto) + Polymarket WS (20+ markets) |
| `openclaw-gateway-watchdog.timer` | Gateway health probe (every 60s) |

#### Data Collection (feeds everything downstream)

| Timer | Schedule (EDT) | Service | Purpose |
|-------|----------------|---------|---------|
| `crypto-daily-analysis` | 8:30 AM daily | `run_daily_analysis.sh` | Crypto OHLCV + indicators |
| `crypto-intraday-update` | Every 4h, 7AM-11PM | `intraday_update.py` | Crypto REST gap-fill |
| `polymarket-ingest` | Every 4h | `providers/polymarket.py` | PM market discovery + REST gap-fill |
| `equity-daily-update` | 5:00 AM daily | `equity_data_collector.py --update` | Equity + index daily data |
| `equity-full-collect` | Sunday 3 AM | `equity_data_collector.py --collect` | Weekly full equity history |
| `crypto-weekly-backfill` | Sunday 2 AM | `historical_backfill.py --days 14` | Weekly crypto gap-fill |

#### Analysis & Signals (depends on fresh data)

| Timer | Schedule (EDT) | Service | Purpose |
|-------|----------------|---------|---------|
| `correlation-snapshot` | 8:00 AM daily | `correlation_tracker.py --snapshot` | Cross-asset correlations + regime |
| `crypto-signal-alerts` | 8AM / 12PM / 4PM | `run_signal_alerts.sh` | Signal detection with risk checks |
| `crypto-signal-trading` | 8:05AM / 12:05PM / 4:05PM / 8:05PM | `signal_trading.py --check` | Multi-portfolio strategy signals |
| `crypto-price-alerts` | Every 30min, 7AM-11PM | `price_alerts_cron.sh` | Price threshold alerts |

#### Reporting (depends on analysis)

| Timer | Schedule (EDT) | Service | Purpose |
|-------|----------------|---------|---------|
| `crypto-portfolio-snapshot` | 9:00 AM daily | `daily_report_cron.sh` | Portfolio snapshot → Telegram |
| `paper-portfolio-report` | 5:00 PM daily | `run_paper_report.sh` | Paper portfolio report |
| `paper-portfolio-rebalancing` | Monday 9 AM | `run_paper_rebalancing.sh` | Weekly paper rebalancing |
| `news-digest` | 8 AM & 4 PM | `news_digest_cron.sh` | World news digests |
| `company-financials` | 7:30 AM daily | `company_financials.py --collect` | Fundamental data refresh |

#### Infrastructure

| Timer | Schedule | Service | Purpose |
|-------|----------|---------|---------|
| `crypto-random-scheduler` | 3:00 AM daily | `schedule_daily.sh` | Random intraday report scheduling |

### Aleph's trading day (EDT)

```
1:00 AM  gateway-weekly-restart (Sun) ──── preemptive memory relief
2:00 AM  crypto-weekly-backfill (Sun) ──── fill gaps in crypto OHLCV
3:00 AM  equity-full-collect (Sun) ──────── full equity re-fetch
         crypto-random-scheduler ────────── schedule random reports
5:00 AM  equity-daily-update ────────────── equity + VIX + SPY + QQQ

═══ ACTIVE TRADING HOURS (7 AM - 11 PM) ═══════════════════════

7:00 AM  ┌── risk-guardian starts (every 30 min) ──────────┐
         │   Pure rules: stops, trailing, TP, circuit       │
         │   breaker. Executes exits immediately.           │
         │   No LLM. Logs to risk_events.jsonl.             │
7:30 AM  │  company-financials                              │
8:00 AM  │  correlation-snapshot ── BTC-SPY/QQQ, VIX regime │
         │  signal-alerts + signal-trading                   │
         │  news-digest (morning)                            │
8:30 AM  │  crypto-daily-analysis ── OHLCV + indicators      │
8:45 AM  │  MORNING STRATEGIC REVIEW (full LLM, GPT-4o)     │
         │    All context: regime, scores, signals, sentiment │
         │    correlations, risk events, vector memory        │
         │    → Strategic trades, rebalancing, full report    │
9:00 AM  │  portfolio-snapshot → Telegram                    │
         │  paper-rebalancing (Mon)                          │
         │                                                   │
12:00 PM │  signal-alerts + signal-trading                   │
12:30 PM │  MIDDAY TACTICAL CHECK (light LLM, GPT-4o-mini)  │
         │    Strong signals only (strength >= 70)            │
         │    High-conviction trades only                     │
         │                                                   │
4:00 PM  │  signal-alerts + signal-trading                   │
4:30 PM  │  AFTERNOON TACTICAL CHECK (light LLM)            │
         │  news-digest (afternoon)                          │
5:00 PM  │  paper-portfolio-report                           │
         │                                                   │
11:00 PM └── risk-guardian stops ────────────────────────────┘

CONTINUOUS:
  Every 30m  risk-guardian (7AM-11PM) ──── position risk checks + auto-exits
  Every 30m  crypto-price-alerts ────────── threshold alerts
  Every 4h   crypto-intraday-update ─────── intraday candles + indicators
  Every 60s  openclaw-gateway-watchdog ──── gateway health + outage logging
```

### Trading decision hierarchy

```
DEFENSIVE (no LLM, every 30 min, 7 AM - 11 PM)
│  risk_guardian.py
│  → Stop-loss, trailing stop, take-profit, circuit breaker
│  → Executes exits immediately in paper portfolio
│  → Sends Telegram alert
│  → Logs to risk_events.jsonl + market intelligence vector store
│
STRATEGIC (full LLM, 8:45 AM daily)
│  trading_agent.py --run
│  → Full context: regime, scores, signals, sentiment,
│    correlations, risk events, historical vector memory
│  → GPT-4o analysis with structured prompt
│  → Medium + high conviction trades
│  → Smart rebalancing if recommended (drift-based)
│  → Stores snapshot + rationales in vector memory
│  → Full Telegram report
│
TACTICAL (light LLM, 12:30 PM + 4:30 PM)
│  trading_agent.py --midday
│  → Only strong signals (strength >= 70)
│  → GPT-4o-mini for speed
│  → High conviction trades only
│  → Telegram report only if trades executed
│  → Light memory storage
```

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
