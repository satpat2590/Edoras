# Edoras Trading System — Master Reference

> Comprehensive reference for the automated multi-asset portfolio management system.
> Use this document for context when reasoning about any component of the system.
> **Last updated:** 2026-03-15

---

## 1. Architecture Overview

```
                          +-----------------------+
                          |   OpenClaw Gateway    |
                          |  (Aleph / Regi agents)|
                          +----------+------------+
                                     |
                          Heartbeat (15m) / Telegram
                                     |
    +--------------------------------+--------------------------------+
    |                                |                                |
    v                                v                                v
+---+--------+            +----+----------+              +-----------+---+
| Data Layer |            | Signal Layer  |              | Execution     |
| collectors |----feed--->| indicators    |---signals--->| paper_trading |
| intraday   |            | strategies    |              | live_executor |
| backfill   |            | backtester    |              | risk_manager  |
+------------+            +---------------+              +---------------+
                                                                |
                                                                v
                                                       +--------+-------+
                                                       | Reporting      |
                                                       | portfolio rpts |
                                                       | alerts, digest |
                                                       +----------------+
```

**Runtime**: Python 3 on Linux (Pop!_OS), systemd user timers, SQLite database.
**Agents**: Aleph (main, general-purpose) and Regi (quant, trading-focused) via OpenClaw.
**Database**: `crypto_data.db` (~115 MB), 30+ tables including vector search.

---

## 2. Directory Structure

```
projects/edoras/          # Main project root
  config.py                          # Central configuration (27 dependents)
  indicator_calculator.py            # Canonical indicator computation (ADX, RSI, MACD, BB, etc.)

  # ── Data Collection ──────────────────────────────────────
  crypto_data_collector.py           # Coinbase candle fetcher (all symbols, all timeframes)
  daily_data_collection.py           # Daily batch orchestrator (wraps crypto_data_collector)
  equity_data_collector.py           # yfinance equity data (SPY, QQQ, VIX, sector ETFs)
  intraday_update.py                 # Lightweight 1h candle updater for portfolio symbols
  historical_backfill.py             # Deep historical data backfill
  compute_all_indicators.py          # Batch indicator recomputation

  # ── Trading Engine ───────────────────────────────────────
  signal_trading.py                  # Signal generation + execution orchestrator
  paper_trading.py                   # Paper portfolio manager (positions, trades, P&L)
  trading_agent.py                   # Full trading session orchestrator (morning/midday)
  live_executor.py                   # Coinbase Advanced Trade API live execution
  bankr_client.py                    # Bankr DEX API client (prompt-based, EVM chains)
  dex_executor.py                    # DEX trade execution via Bankr (Arwen portfolio)
  smart_rebalancer.py                # Portfolio rebalancing logic
  paper_rebalancing.py               # Paper portfolio rebalancing (weekly)

  # ── Risk Management ─────────────────────────────────────
  risk_manager.py                    # Position-level risk (stop-loss, trailing stops)
  risk_guardian.py                   # Portfolio-level risk (drawdown, circuit breaker)
  exit_signals.py                    # Exit signal definitions

  # ── Analysis & Intelligence ──────────────────────────────
  correlation_tracker.py             # Cross-asset correlation + regime detection
  advanced_scorer.py                 # Multi-factor position scoring
  market_intelligence.py             # AI-powered market analysis (OpenAI embeddings)
  sentiment.py                       # News sentiment via RSS + LLM scoring
  vector_store.py                    # SQLite-vec vector storage for market memory
  trade_journal.py                   # Trade outcome journaling with embeddings
  strategy_tracker.py                # Strategy signal logging + outcome tracking

  # ── Backtesting ──────────────────────────────────────────
  backtester.py                      # Core backtesting engine (ScoreBased, Bollinger, MultiSignal)
  enhanced_optimizer.py              # Strategy parameter optimization

  # ── Reporting & Alerts ───────────────────────────────────
  daily_portfolio_report.py          # Daily portfolio snapshot report
  automated_portfolio_report.py      # Automated report generation
  signal_alerts.py                   # Signal-based Telegram alerts
  price_alerts.py                    # Price threshold Telegram alerts
  telegram_fmt.py                    # Telegram message formatting utilities
  quick_market_scan.py               # Ad-hoc market scanning utility
  research_reader.py                 # RSS/news research aggregator

  # ── CLI & Utilities ──────────────────────────────────────
  cli.py                             # Unified query interface (7 commands, see below)
  report_engine.py                   # PDF report generator (7 report types, see below)

  # ── Scheduling ───────────────────────────────────────────
  schedule_random_reports.py         # Random report scheduler

  # ── Shell Wrappers (called by systemd) ───────────────────
  run_daily_analysis.sh              # Daily analysis pipeline
  run_daily_data_collection.sh       # Daily data collection
  run_midday_review.sh               # Midday trading review
  run_paper_rebalancing.sh           # Weekly rebalancing
  run_paper_report.sh                # Daily paper portfolio report
  run_portfolio_report.sh            # Portfolio report
  run_research_reader.sh             # Research reader
  run_risk_guardian.sh               # Risk guardian
  run_signal_alerts.sh               # Signal alerts
  run_trading_agent.sh               # Trading agent session
  daily_report_cron.sh               # Daily report cron wrapper
  price_alerts_cron.sh               # Price alerts cron wrapper
  schedule_daily.sh                  # Daily scheduler

  # ── Configuration & Schema ──────────────────────────────
  bootstrap_db.py                    # Database bootstrapper for fresh installs
  coinbase_usd_pairs.json            # Supported Coinbase trading pairs
  schema/                            # SQL schema definitions
    enhanced_schema.sql              # Full schema (PostgreSQL syntax)
    enhanced_schema_sqlite.sql       # Full schema (SQLite syntax)
  migration/                         # Database migration scripts
    phase1_warehouse_redesign.py     # Phase 1: additive schema (accounts, portfolio_strategies, transfers, valuations)
    add_dex_tables.py                # DEX tables (dex_tokens, dex_transactions, securities extensions)
    add_traders.py                   # Trader system (traders, wallets, access)
    migrate_to_enhanced.py           # Original migration to enhanced schema

  # ── Subsystems ───────────────────────────────────────────
  realtime/                          # Real-time WebSocket processing
    config.py, main.py, supervisor.py
    ingest/                          # WebSocket ingestion (Coinbase, Polymarket)
    processing/                      # Stream processing
    execution/                       # Real-time execution
    risk/                            # Real-time risk monitoring
    storage/                         # Real-time data storage
  providers/                         # External data providers
    polymarket.py                    # Polymarket prediction market data

  # ── Organized Subdirectories ─────────────────────────────
  tests/                             # Ad-hoc test scripts
  docs/                              # Architecture docs, roadmap, strategy docs
  archive/                           # Retired one-off scripts
    one-off/                         # Debug/check/analysis utilities
    optimization/                    # Old optimization scripts
    grid-search/                     # Old grid search scripts
  logs/                              # Runtime logs (gitignored)
  backtest_results/                  # Backtesting output
  analysis_results/                  # Analysis output
```

### Workspace Root (`~/.openclaw/workspace/`)

```
  HEARTBEAT.md                       # Agent heartbeat instructions (processed every 15m)
  TRADING_ARCHITECTURE.md            # Trading system architecture for agents
  AGENTS.md, IDENTITY.md, SOUL.md    # Agent identity/personality docs
  MEMORY.md, USER.md, TOOLS.md       # Agent memory, user profile, tool docs
  BOOTSTRAP.md                       # Fresh install guide
  requirements.txt                   # Python dependencies

  utilities/                         # Shell/Python utilities
    openclaw-nvm-wrapper.sh          # Gateway NVM wrapper
    news_digest.py                   # News digest generator
    news_digest_cron.sh              # News digest cron wrapper
    emerging_trends.py               # Trend analysis
    restart-gateway.sh               # Gateway restart helper
    resume-handler.sh                # System resume handler

  monitoring/                        # Gateway health monitoring
    telegram-sync.js                 # Telegram connectivity check
    cron-wrapper.sh                  # Monitoring cron wrapper
    outage-log.jsonl                 # Outage history

  reports/                           # Generated Markdown reports (7 types, date-stamped)
    positions/                       # Daily position snapshots
    portfolio/                       # Portfolio performance summaries
    trades/                          # Trade activity logs
    signals/                         # Signal generation summaries
    market/                          # Market regime & correlation
    risk/                            # Risk exposure reports
    performance/                     # Weekly performance reviews
  news/                              # Daily news digests (date-stamped .md files)
  journal/                           # Trade journal entries
  memory/                            # Agent memory store
  skills/                            # OpenClaw skills
  moltbook/                          # Moltbook community integration

  projects/
    edoras/               # THIS SYSTEM (see above)
    company-financials/              # Equity financials collector
    data_warehouse/                  # Data warehouse planning docs
```

---

## 3. Data Flow

### Collection Pipeline
```
Coinbase API ──1h/1d candles─────> crypto_data_collector.py ──> candlesticks table
                                   └── aggregate_4h_candles()   (1h → 4h rollup at
                                       UTC boundaries 00/04/08/12/16/20)
yfinance     ──daily OHLCV──────> equity_data_collector.py ──> candlesticks table
Coinbase API ──1h portfolio──────> intraday_update.py ──────> candlesticks table
                                   └── also triggers 4h aggregation + 4h indicators
RSS feeds    ──headlines─────────> sentiment.py ────────────> sentiment_scores table
Polymarket   ──contract data─────> providers/polymarket.py ─> (external storage)
```

> **Note:** Coinbase API does not offer a native 4-hour granularity. All 4h candles
> are built by aggregating four consecutive 1h candles. Only complete 4h bars
> (exactly 4 hourly candles) are stored. This runs in both the daily collection
> and the intraday update cycle.

### Indicator Pipeline
```
candlesticks ──> indicator_calculator.calculate_all_indicators()
                 ├── SMA (20, 50, 200)
                 ├── EMA (12, 26)
                 ├── RSI (14)
                 ├── MACD (12/26/9)
                 ├── Bollinger Bands (20, 2std)
                 ├── ATR (14)
                 ├── ADX (14) — Wilder's smoothing, clipped [0,100]
                 ├── Volume ratio (vs 20-SMA)
                 └── Binary probability indicators (16 total)
             ──> indicators table
```

### Signal Pipeline
```
indicators ──> signal_trading.py
               │
               Routed symbols (backtested strategy assigned):
               │  Strategy decides exclusively — no legacy fallback.
               │  If strategy is silent, system holds.
               │  Signals carry strategy_id for trade attribution.
               │
               Unrouted symbols (legacy logic):
               ├── Mean-reversion signals (RSI extremes + MACD confirmation)
               ├── Trend-following signals (pullback/rally + SMA alignment)
               ├── Momentum breakout signals (price vs SMA + volume)
               │
               Signal Enhancement (legacy only):
               ├── Sentiment adjustment (+/- 5-10%)
               ├── ADX regime filter (30+ = confirmed trend)
               ├── Volume confirmation bonus
               ├── Multi-timeframe alignment (up to 1.3x)
               └── Market regime adjustment
           ──> BUY/SELL decisions
           ──> paper_trading.py (execution, with strategy_id on trade row)
           ──> trades table + positions table
```

### Risk Pipeline
```
positions ──> risk_manager.py
              ├── Stop-loss monitoring
              ├── Trailing stop updates
              └── Take-profit levels
          ──> risk_guardian.py
              ├── Portfolio drawdown limits
              ├── Concentration limits
              └── Circuit breaker (max daily loss)
          ──> risk_events table
```

---

## 4. Scheduling (systemd timers)

### High-Frequency (intraday)
| Timer | Schedule | Script | Purpose |
|-------|----------|--------|---------|
| `risk-guardian` | Every 30min, 7AM-11PM | `run_risk_guardian.sh` | Portfolio risk monitoring |
| `crypto-intraday-update` | Every 2h, 7AM-7PM | `intraday_update.py` | 1h candle refresh for portfolio symbols |
| `crypto-signal-trading` | 8:05, 12:05, 4:05, 8:05 | `signal_trading.py` | Signal generation + trade execution |
| `crypto-signal-alerts` | Every 4h, 7AM-11PM | `run_signal_alerts.sh` | Signal-based Telegram alerts |
| `polymarket-ingest` | Every 4h | `providers/polymarket.py` | Polymarket data collection |

### Daily
| Timer | Schedule | Script | Purpose |
|-------|----------|--------|---------|
| `equity-daily-update` | 5:00 AM | `equity_data_collector.py` | Equity OHLCV data |
| `crypto-price-alerts` | 7:00 AM | `price_alerts_cron.sh` | Price threshold alerts |
| `company-financials` | ~7:30 AM | `company_financials.py` | Equity fundamentals |
| `correlation-snapshot` | ~8:00 AM | `correlation_tracker.py` | Cross-asset correlations |
| `news-digest` | ~8:00 AM + 4:00 PM | `news_digest_cron.sh` | News digest generation |
| `crypto-daily-analysis` | ~8:30 AM | `run_daily_analysis.sh` | Full daily analysis pipeline |
| `trading-agent` | ~8:45 AM | `run_trading_agent.sh` | Morning trading session |
| `crypto-portfolio-snapshot` | ~9:00 AM | `daily_report_cron.sh` | Portfolio snapshot |
| `midday-trading-review` | ~12:30 PM | `run_midday_review.sh` | Midday review session |
| `paper-portfolio-report` | ~5:00 PM | `run_paper_report.sh` | End-of-day portfolio report |
| `edoras-daily-reports` | ~5:30 PM | `run_daily_reports.sh` | Generate 7 reports + deliver via Telegram |
| `research-reader` | ~9:30 PM | `run_research_reader.sh` | Research/news aggregation |

### Weekly
| Timer | Schedule | Script | Purpose |
|-------|----------|--------|---------|
| `gateway-weekly-restart` | Sun 1:00 AM | (inline systemd) | Gateway health restart |
| `crypto-weekly-backfill` | Sun 2:00 AM | `historical_backfill.py` | Deep historical backfill |
| `equity-full-collect` | Sun 3:00 AM | `equity_data_collector.py --full` | Full equity data refresh |
| `paper-portfolio-rebalancing` | Mon 9:00 AM | `run_paper_rebalancing.sh` | Weekly portfolio rebalancing |

### Always-on
| Service | Script | Purpose |
|---------|--------|---------|
| `openclaw-gateway` | `openclaw-nvm-wrapper.sh` | OpenClaw Gateway (Telegram, heartbeat) |
| `openclaw-gateway-watchdog` | (inline) | Gateway health check (every 1m) |

---

## 5. Database Schema Summary

### Core Market Data
- **candlesticks** — OHLCV data by symbol/timeframe/timestamp (primary data store)
- **indicators** — 38 technical indicator columns per symbol/timeframe/timestamp
- **ticks** — Real-time tick data (bid/ask/volume)
- **collection_log** — Data collection status tracking

### Trading
- **portfolios** — Portfolio definitions (Galadriel=paper, Thranduil=live, Elrond=tracked, Arwen=live/DEX)
- **accounts** — Bridge table: connects portfolios to venues (portfolio ↔ venue M:M). Each account represents one portfolio's presence on one venue (e.g., Galadriel-Coinbase, Arwen-Bankr-Base). Carries account_external_id (API key ID, wallet address, etc.) and account_type (paper, api_key, wallet, brokerage).
- **traders** — Who trades: agents (Aleph, Regi), systems (Signal Engine, Risk Engine), humans (Satyam)
- **trader_wallets** — How traders connect to exchanges (CEX API, DEX wallet, paper, brokerage)
- **trader_portfolio_access** — M:M mapping of which traders can trade which portfolios (executor/advisor/readonly)
- **positions** — Open/closed positions with entry price, stops, P&L. `account_id` FK populated by all write paths (paper_trading, dex_executor, real_time_risk).
- **trades** — Individual trade records with decision_context JSON, trader_id FK, `account_id` FK (→ accounts, populated by all write paths), nullable `strategy_id` FK (→ strategy_registry), and nullable DEX columns (tx_hash, block_number, gas_used, gas_price_gwei, slippage_bps).
- **trade_outcomes** — Closed trade analysis (entry/exit, outcome%, holding time)
- **paper_snapshots** — Daily portfolio value snapshots (legacy, see portfolio_valuations)
- **portfolio_valuations** — Portfolio-level NAV time series: total_nav_usd, cost_basis, unrealized/realized PnL, fees, account_count
- **transfers** — Capital movements between accounts: deposits, withdrawals, internal transfers, bridges. Essential for reconciliation when funds move between venues.
- **paper_trades_legacy** — Legacy trade records (pre-migration)

### Risk & Strategy
- **risk_events** — Stop-loss hits, circuit breakers, drawdown events
- **strategy_registry** — Registered trading strategies with strategy_type (momentum, mean_reversion, trend_following, multi_factor) and parameters JSON
- **portfolio_strategies** — M:M junction: which strategies are assigned to which portfolios, with allocation_pct and active/retired tracking
- **strategy_performance** — Strategy backtest/live performance metrics
- **strategy_signals_log** — Every signal generated with outcome tracking
- **portfolio_performance** — Portfolio-level performance time series

### Market Intelligence
- **sentiment_scores** — LLM-scored news sentiment per symbol
- **news_sentiment_stream** — Raw news headline stream with sentiment
- **market_regime** / **market_regime_detailed** — VIX-based regime classification
- **correlations** — Cross-asset correlation matrix snapshots
- **market_memory** — AI market analysis entries with embeddings
- **portfolio_analysis** — Multi-timeframe signal analysis per symbol

### Infrastructure
- **exchanges** — Venue definitions with fee_model (maker_taker, gas, spread, commission), settlement_type (instant, on_chain, t_plus_1), and optional chain/chain_id for DEX venues
- **securities** — Security/instrument catalog with optional canonical_instrument_id (self-FK to group same asset across venues, e.g., WETH-BASE → ETH-USD) and decimals for on-chain tokens
- **dex_tokens** — DEX token metadata (liquidity, volume_24h, holder_count, pair_address)
- **dex_transactions** — On-chain DEX transaction log (to be merged into trades in future phase)
- **system_metrics** — Operational metrics

---

## 6. Key Configuration (`config.py`)

### Portfolio Symbols (Galadriel)
```
ETH-USD, BTC-USD, XRP-USD, TROLL-USD, BONK-USD, FET-USD, AMP-USD, GRT-USD
```

### Trading Parameters
- **Signal strength threshold**: 50 (minimum for execution)
- **Position sizing**: 3-5% (weak), 5-10% (medium), 10-15% (strong)
- **ADX trending threshold**: 30
- **Minimum holding period**: 12 hours (risk exits bypass)
- **Signal dedup window**: 60 seconds
- **Max position concentration**: 25%
- **Transaction fee**: 0.1% (paper), actual fee (live)

### Strategy Routing
Each symbol is routed to a specific backtested strategy (ScoreBased, BollingerReversion, or MultiSignal) based on historical performance. Fallback: legacy signal logic.

---

## 7. Risk Management Framework

### Position-Level (`risk_manager.py`)
- **Stop-loss**: Fixed percentage from entry (configurable per symbol)
- **Trailing stop**: Ratchets up as price rises, never down
- **Take-profit**: Tiered exits at 1.5x, 2x, 3x risk

### Portfolio-Level (`risk_guardian.py`)
- **Max drawdown**: Circuit breaker at -15% from peak
- **Daily loss limit**: Pause trading if daily P&L < -5%
- **Concentration limit**: No single position > 25% of portfolio
- **Correlation check**: Warn if portfolio is over-correlated

### Safeguards — Signal Engine (`signal_trading.py`)
- **Dedup**: Same-symbol BUY blocked within 60s window
- **Min hold**: No signal-driven SELL before 12h (risk exits exempt)
- **Conviction filter**: Signals below strength 50 are dropped
- **No doubling down**: BUY skipped if position already held

### Safeguards — LLM Trading Agent (`trading_agent.py`)

**Hard rules (non-negotiable):**
- **Max 3 trades per session** — prevents portfolio churn
- **Session dedup** — same (symbol, action) pair blocked if already acted on in this session
- **10% cash reserve** — never spends below floor on LLM trades
- **Stop-loss (10%)**, **circuit breaker (15% drawdown)**, **position cap (25%)**, **sector cap (40%)** — inherited from risk_manager
- **Min trade size**: $10
- **Conviction gating**: LOW always blocked; MEDIUM requires signal engine confirmation; HIGH executes unconditionally

**Adjustable bounds (LLM can request within ranges):**
- **Allocation**: 3-20% per trade (default cap 15%; raised to 20% only with high conviction + confirmed trend)
- **Hold period**: 6-24h (default 12h; LLM must justify deviations in structured reasoning)
- **Sell sizing**: 25-100% (LLM specifies sell_pct; falls back to conviction-based: high=100%, medium=50%)

**Trend-aware features:**
- Per-symbol trend classification (uptrend/downtrend/ranging + strong/moderate/weak) from SMA alignment + ADX
- Trend data fed to LLM prompt so it can make regime-informed decisions
- Allocation cap raised to 20% only when trend is confirmed (uptrend/downtrend with ADX > 20)

**Structured reasoning (required for all LLM trades):**
- Every trade must include a `reasoning` object with: thesis, trend_regime, supporting signals, contradicting signals, regime_consideration, similar_past_outcome, risk_note
- Trades without reasoning are rejected by the execution engine
- Full reasoning stored in `decision_context` column of trades table (JSON)
- Guardrail adjustments (clamped allocation, modified hold period) also recorded in decision_context

---

## 8. Edoras CLI (`cli.py`)

Unified query interface for routine portfolio checks. Agents should use `cli.py` for
standard queries and raw SQL for deep/custom analysis.

```bash
python3 cli.py snapshot              # Current portfolio: positions, P&L, cash, totals
python3 cli.py trades [--hours 24] [-v]  # Recent trades with source attribution (-v for reasoning)
python3 cli.py signals [--hours 24]  # Strategy signals: executed vs skipped, strength, ADX, RSI
python3 cli.py outcomes [--days 7]   # Closed trade P&L: win rate, net USD, holding time
python3 cli.py pnl [--days 30]       # Daily portfolio value series from snapshots
python3 cli.py indicators SYMBOL     # Latest indicators across 1h/4h/1d timeframes
python3 cli.py health                # Data freshness, position count, timer status
```

---

## 9. Report Engine (`report_engine.py`)

Generates PDF reports to `~/.openclaw/workspace/reports/`.
Reports are dark-themed PDFs with color-coded P&L, stat cards, and formatted tables.
Delivered daily via Telegram at 5:30 PM. Agents should use `cli.py` for data queries, not reports.

```bash
python3 report_engine.py all                # Generate all 7 reports
python3 report_engine.py positions          # Current position detail with P&L
python3 report_engine.py portfolio          # Portfolio summary with allocation breakdown
python3 report_engine.py trades             # Last 24h trade activity with closed P&L
python3 report_engine.py signals            # Signal generation summary (executed/skipped)
python3 report_engine.py market             # Market regime, correlations, sentiment
python3 report_engine.py risk               # Risk exposure: stops, concentration, flags
python3 report_engine.py performance        # Weekly performance review (win rate, returns)
```

**Output structure:**
```
~/.openclaw/workspace/reports/
├── positions/2026-03-14.pdf
├── portfolio/2026-03-14.pdf
├── trades/2026-03-14.pdf
├── signals/2026-03-14.pdf
├── market/2026-03-14.pdf
├── risk/2026-03-14.pdf
└── performance/weekly-2026-W11.pdf
```

---

## 10. Dependency Graph

```
config.py (27 dependents — touch with extreme care)
  │
  ├── indicator_calculator.py (8 dependents)
  │     └── Used by: backtester, signal_trading, intraday_update, equity_data_collector, ...
  │
  ├── backtester.py (imported by signal_trading for strategy routing)
  │
  ├── signal_trading.py (main signal engine)
  │     ├── imports: config, indicator_calculator, backtester, paper_trading,
  │     │            risk_manager, correlation_tracker, strategy_tracker
  │     └── imported by: trading_agent
  │
  ├── paper_trading.py (execution layer)
  │     ├── imports: config, enhanced_optimizer, telegram_fmt, trade_journal
  │     └── imported by: signal_trading, risk_guardian, trading_agent
  │
  ├── risk_manager.py + risk_guardian.py (risk layer)
  │     └── imports: config, exit_signals, market_intelligence, paper_trading
  │
  └── trading_agent.py (top-level orchestrator)
        └── imports: nearly everything
```

---

## 11. Environment & Credentials

### Required Environment Variables
- `COINBASE_API_KEY` — Coinbase Advanced Trade API key
- `COINBASE_API_SECRET` — Coinbase API secret (EC private key)
- `OPENAI_API_KEY` — OpenAI API key (embeddings, sentiment)
- `DEEPSEEK_API_KEY` — DeepSeek API key (agent LLM)

### Configuration Files
- `~/.config/coinbase.env` — Coinbase credentials (sourced by systemd)
- `~/.openclaw/openclaw.json` — OpenClaw gateway configuration
- `~/.openclaw/agents/main/agent/` — Aleph agent config
- `~/.openclaw/agents/quant/agent/` — Regi agent config

---

## 12. Operational Procedures

### Daily Operations
System is fully automated. Use the CLI for routine checks:
```bash
python3 cli.py health                       # Quick system status
python3 cli.py snapshot                     # Portfolio state
python3 cli.py trades --hours 12            # Recent trades
python3 cli.py outcomes --days 3            # Recent closed trade P&L
systemctl --user list-timers --all          # Timer status
journalctl --user -u crypto-signal-trading  # Trading logs (deep dive)
```

### Adding a New Symbol
1. Add to `PORTFOLIO_SYMBOLS` in `config.py` and `intraday_update.py`
2. Run `historical_backfill.py --symbol NEW-USD` for history
3. Run `compute_all_indicators.py` to populate indicators
4. The symbol will be picked up by the next signal trading run

### Fresh Install (Mac Mini or new machine)
See `BOOTSTRAP.md` in workspace root. Key steps:
1. Clone repo, create venv, install requirements.txt
2. Set up `.env` with API credentials
3. Run `bootstrap_db.py` to create database
4. Run `historical_backfill.py` for data
5. Install OpenClaw and configure agents
6. Set up systemd timers

### Indicator Recomputation
If indicators look wrong:
```bash
cd projects/edoras
python3 compute_all_indicators.py           # Recompute all
python3 -c "from indicator_calculator import calculate_all_indicators; print('OK')"
```

---

## 13. Key Algorithms

### ADX (Average Directional Index) — `indicator_calculator.py`
Uses Wilder's smoothing (EMA with alpha=1/14):
1. Compute +DM/-DM from high/low deltas (mutually exclusive)
2. Smooth ATR, +DI, -DI with Wilder's EWM; ATR floored at 1e-12 (prevents microcap division-by-zero)
3. DI values clipped to >= 0 (prevents floating-point negative leakage)
4. DX = 100 * |+DI - -DI| / (+DI + -DI), clipped to [0, 100] before smoothing
5. ADX = Wilder's smooth of DX, clipped to [0, 100]

### Signal Scoring — `signal_trading.py`
Base signals (mean-reversion, trend, momentum) generate raw strength 40-80.
Enhancement pipeline multiplies/adds:
- Sentiment: +/-5-10 points
- ADX > 30: signal preserved; ADX < 30: signal dampened
- Volume > 1.5x: +5 points
- Multi-timeframe alignment: up to 1.3x multiplier
- Final: clipped to [0, 100], must exceed 50 for execution

### Position Sizing — `signal_trading.py`
```
strength < 50:  skip (no trade)
strength 50-65: 3-5% of portfolio
strength 65-80: 5-10% of portfolio
strength 80+:   10-15% of portfolio
```
