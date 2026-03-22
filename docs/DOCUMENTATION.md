# Multi-Asset Trading & Portfolio Management System

## Overview

Multi-asset, multi-exchange quantitative trading system covering cryptocurrency (Coinbase), prediction markets (Polymarket), equities (yfinance), and cross-asset correlation tracking. Features exchange-agnostic real-time WebSocket ingestion, dimension-table-driven metadata, multi-portfolio management, and automated signal generation with 7 backtested strategies.

**Key Features:**
- **Multi-exchange real-time data** — Coinbase + Polymarket WebSocket feeds via exchange-agnostic base class, DEX data via GeckoTerminal REST
- **Multi-portfolio system** — 4 portfolios (Galadriel/paper, Thranduil/live, Elrond/tracked, Arwen/DEX-live) with accounts bridge table (portfolio ↔ venue M:M)
- **Multi-timeframe technical analysis** (1-hour, 4-hour, daily) with standard + binary indicator profiles
- **7 backtested strategies** — BollingerReversion, MultiSignal, ADXTrend, ScoreBased, ScoreBasedRelaxed, MACDCross
- **Strategy routing** — per-symbol strategy + timeframe from DB config, with legacy fallback
- **Prediction market integration** — Polymarket binary markets with 16 specialized indicators
- **Advanced scoring model** with 5 components: Momentum, Trend, Volatility, Volume, Risk-Adjusted
- **Risk management** — stop-loss, trailing stop (ATR-based), take-profit scale-out, circuit breaker
- **Trade journal** — outcome tracking by signal type, regime, and symbol
- **Dimension tables** — exchanges/venues (5), securities (52+), strategies (7), portfolios (4), accounts (4), traders (5) all DB-managed
- **DEX trading** — Bankr API integration for on-chain swaps (Ethereum + Base), DEX risk rules (liquidity, slippage, holders)
- **Warehouse redesign** — accounts bridge table (portfolio↔venue), portfolio_strategies (M:M), transfers, portfolio_valuations; dual-write account_id on all trade/position writes
- **Automated Telegram reporting** — signals, portfolio snapshots, optimization reports

---

## System Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                     Real-Time Ingestion Layer                        │
│  ┌───────────────────┐  ┌──────────────────────┐  ┌──────────────┐  │
│  │Coinbase WebSocket  │  │Polymarket WebSocket   │  │REST Gap-Fill │  │
│  │(18 crypto symbols) │  │(20+ prediction mkts)  │  │(yfinance,    │  │
│  │                    │  │                       │  │ Coinbase,    │  │
│  │ base_websocket.py  │  │ base_websocket.py     │  │ Polymarket)  │  │
│  └────────┬───────────┘  └────────┬──────────────┘  └──────┬───────┘  │
│           └──────────┬────────────┘                         │         │
│                      ▼                                      │         │
│           5m candles → 1h rollup → 4h rollup ←──────────────┘         │
└──────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────────┐
│                     Indicator & Analytics Layer                      │
│  ┌──────────────────────────────────────────────────────────┐        │
│  │ indicator_calculator.py                                   │        │
│  │ ├── standard profile: 17 indicators (RSI, MACD, BB, ADX) │        │
│  │ └── binary profile: 16 indicators (prob momentum, bands)  │        │
│  └──────────────────────────────────────────────────────────┘        │
│  ┌─────────────┐  ┌─────────────────┐  ┌───────────────────┐        │
│  │13 Backtested│  │Portfolio Scoring │  │Risk Metrics       │        │
│  │Strategies   │  │(5-component)     │  │(Sharpe, VaR, etc) │        │
│  └─────────────┘  └─────────────────┘  └───────────────────┘        │
└──────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────────┐
│                     Multi-Portfolio Execution Layer                   │
│  ┌────────────────┐  ┌──────────────┐  ┌──────────────────────────┐  │
│  │Signal Trading   │  │Risk Manager  │  │Portfolios                │  │
│  │(strategy routed,│  │(stops, trail,│  │Galadriel (paper, active) │  │
│  │ multi-portfolio)│  │ TP, circuit) │  │Thranduil (live, pending) │  │
│  └────────────────┘  └──────────────┘  │Elrond (tracked, pending) │  │
│                                        └──────────────────────────┘  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────┐    │
│  │Trade Journal  │  │Telegram      │  │Dimension Tables          │    │
│  │(outcomes,     │  │Reporting     │  │(exchanges, securities,   │    │
│  │ analytics)    │  │              │  │ strategies, portfolios)  │    │
│  └──────────────┘  └──────────────┘  └──────────────────────────┘    │
└──────────────────────────────────────────────────────────────────────┘
```

**Data Flow:**
1. **Real-time ingestion**: WebSocket feeds (Coinbase + Polymarket) aggregate ticks into 5m candles, roll up to 1h/4h
2. **REST gap-fill**: Periodic REST polling catches any missed candles (crypto every 4h, Polymarket every 4h, equity daily)
3. **Indicators**: Profile-gated computation — standard (17) for crypto/equity, binary (16) for prediction markets
4. **Strategy routing**: Per-symbol strategy + timeframe from DB config; routed symbols use backtested strategy exclusively (no legacy fallback — silence means hold). Unrouted symbols use legacy signal logic.
5. **Signal pipeline**: Risk checks → regime adjustment → signal dedup → multi-portfolio iteration
6. **Execution**: Paper trading with DB position sync, trade journal outcome tracking
7. **Reporting**: Automated Telegram messages with analysis results, portfolio snapshots

---

## Scripts & Their Purposes

### Core Data Collection
- **`crypto_data_collector.py`** – Main data collection engine. Fetches candlestick data, calculates technical indicators, stores in SQLite.
- **`run_daily_analysis.sh`** – Wrapper script for daily data collection and analysis (runs via cron).

### Analysis & Scoring
- **`advanced_scorer.py`** – Advanced scoring model with 5 components and multi‑timeframe weighting.
- **`enhanced_optimizer.py`** – Portfolio optimizer with expanded crypto universe and risk metrics.
- **`portfolio_optimizer.py`** – Original portfolio optimizer (simpler scoring).

### Risk Analysis
- **`crypto_risk_analysis.py`** – Portfolio‑level risk analysis (volatility, concentration, VaR).
- **`get_coinbase_symbols.py`** – Fetches available USD trading pairs from Coinbase API.

### Reporting
- **`automated_portfolio_report.py`** – Generates and sends portfolio reports via Telegram.
- **`send_optimization_report.py`** – Sends optimization reports via Telegram.
- **`price_alerts.py`** – Checks for price‑based alerts (e.g., RSI extremes).
- **`signal_alerts.py`** – Checks for technical signal alerts.

### Paper Trading
- **`paper_trading.py`** – $1,000 paper trading portfolio with simulation engine.
- **`run_optimization.py`** – Runs portfolio optimization and generates reports.

### Scheduling & Automation
- **`run_daily_analysis.sh`** – Daily technical analysis wrapper.
- **`run_portfolio_report.sh`** – Portfolio report wrapper (for `at` scheduling).
- **`run_signal_alerts.sh`** – Signal alerts wrapper.
- **`schedule_daily.sh`** – Schedules random portfolio reports.
- **`daily_report_cron.sh`** – Fixed‑time portfolio report.
- **`price_alerts_cron.sh`** – Price alerts cron wrapper.

---

## Advanced Scoring Model Details

### Multi‑timeframe Weighting
- **1‑hour**: 25% weight (short‑term momentum)
- **4‑hour**: 35% weight (medium‑term trend)
- **Daily**: 40% weight (long‑term structure)

### Five Scoring Components

#### 1. Momentum (40%)
- **RSI (14)**: Overbought (>70) vs oversold (<30)
- **MACD**: Bullish (signal cross above) vs bearish (signal cross below)
- **Price vs Moving Averages**: Alignment (price > SMA20 > SMA50 > SMA200 = bullish)

#### 2. Trend (25%)
- **ADX (14)**: Trend strength (>25 = strong trend)
- **Moving Average Slopes**: Upward vs downward slope
- **Golden/Death Cross**: SMA50 > SMA200 (bullish) vs SMA50 < SMA200 (bearish)

#### 3. Volatility (15%)
- **ATR (14)**: Lower volatility = higher score (more stable)
- **Bollinger Band Width**: Narrow bands = consolidation (neutral), wide bands = high volatility (lower score)

#### 4. Volume (10%)
- **Volume Trends**: Increasing volume with price movement = confirmation
- **Volume Ratio**: Current volume vs average

#### 5. Risk‑Adjusted (10%)
- **Sharpe Ratio** (annualized): Risk‑adjusted returns
- **Maximum Drawdown**: Worst peak‑to‑trough decline
- **Value at Risk (95%, 1‑day)**: Potential loss at 95% confidence

### Score Interpretation
- **90‑100**: Exceptional – Strong bullish signals across all timeframes
- **70‑89**: Good – Favorable conditions, consider adding
- **50‑69**: Neutral – Mixed signals, hold existing positions
- **30‑49**: Poor – Consider reducing exposure
- **0‑29**: Very poor – Strong bearish signals, consider selling

---

## Paper Trading System

### Overview
- **Active portfolio**: Galadriel (id=1, paper mode)
- **Starting capital**: $1,000.00
- **Transaction cost**: 0.1% per trade (simulating realistic fees)
- **Strategy routing**: Per-symbol backtested strategies with legacy fallback
- **Signal frequency**: 4x daily (8:05 AM, 12:05 PM, 4:05 PM, 8:05 PM)
- **Rebalancing**: Weekly based on updated scores
- **Position sync**: DB positions table updated on every trade
- **Trade journal**: Every closed trade recorded with outcome analytics

### Key Features
- **Realistic trade execution**: Market prices from Coinbase API
- **Position tracking**: Quantity, average price, current value, P&L
- **Trade history**: All buys/sells with timestamps and costs
- **Performance reporting**: Daily P&L, position details, recommendations

### Usage
```bash
# Initialize portfolio with top 5 cryptos
python3 paper_trading.py --init

# Generate performance report
python3 paper_trading.py --report
```

### Rebalancing Logic
1. **Weekly review**: Every Monday at 9:00 AM EDT
2. **Score update**: Re‑score all symbols in expanded universe
3. **Target allocation**: Top 5 symbols by score, equal weight
4. **Execute trades**: Sell underperformers, buy new opportunities
5. **Transaction costs**: Accounted for in P&L calculation

---

## Setup & Configuration

### Prerequisites
```bash
# Python packages
pip install coinbase-advanced-py pandas numpy sqlite3

# Environment variables (add to ~/.zshrc)
export COINBASE_API="your_api_key"
export COINBASE_SECRET="your_api_secret"
export COINBASE_API_KEY="your_org_api_key"
export COINBASE_API_SECRET="-----BEGIN EC PRIVATE KEY-----\n..."

# Node.js (for Telegram integration)
nvm install v22.12+
```

### Database Schema

The database (`crypto_data.db`) contains core data, trading, and dimension tables:

**Core data:** `candlesticks` (OHLCV for all asset types), `indicators` (17 standard + 16 binary columns), `correlations`, `market_regime`, `sentiment_scores`, `collection_log`

**Trading:** `trades` (unified, with trader_id FK and decision_context JSON for full reasoning audit), `positions` (synced on every trade), `trade_outcomes` (journal), `strategy_performance` (166 backtests), `strategy_signals_log` (every signal + outcome), `paper_snapshots` (daily portfolio values)

**Trader tables:** `traders` (5: Aleph, Regi, Signal Engine, Risk Engine, Satyam), `trader_wallets` (exchange connections), `trader_portfolio_access` (M:M roles: executor/advisor/readonly)

**Dimension tables:** `exchanges` (5: coinbase, yfinance, polymarket, kalshi, bankr), `securities` (48+ rows with indicator_profile, settlement_type, expiry), `strategy_registry` (7 strategies), `portfolios` (3: Galadriel, Thranduil, Elrond)

```sql
-- indicators table now includes both standard and binary columns
-- Standard columns (for crypto/equity): sma_20, sma_50, sma_200, ema_12, ema_26,
--   rsi_14, macd_line, macd_signal, macd_histogram, bb_upper, bb_middle, bb_lower,
--   bb_width, atr_14, volume_sma_20, volume_ratio, adx_14
-- Binary columns (for prediction markets): prob_ema_8, prob_ema_21, prob_roc_6,
--   prob_roc_12, prob_velocity, prob_acceleration, prob_volatility_14,
--   prob_volatility_6, vol_ratio, certainty, prob_band_upper, prob_band_lower,
--   prob_band_position, ema_crossover, bar_range, bar_range_ema
```

### Telegram Configuration
- **Gateway**: OpenClaw Telegram gateway running on port 18789
- **Target chat**: Operator's chat (set via TELEGRAM_CHAT_ID env var)
- **Group chat**: Set via TELEGRAM_GROUP_ID env var
- **Config**: `~/.openclaw/openclaw.json` – `"requireMention": false` for groups

---

## Scheduling & Automation

### Current Cron Schedule (UTC Times)
```bash
# Daily technical analysis (8:30 AM EDT / 12:30 UTC)
30 12 * * * /home/satyamini/.openclaw/workspace/projects/edoras/run_daily_analysis.sh

# Portfolio snapshot (9:00 AM EDT / 13:00 UTC)
0 13 * * * /home/satyamini/.openclaw/workspace/projects/edoras/daily_report_cron.sh

# Random report scheduler (3:00 AM EDT / 7:00 UTC)
0 7 * * * /home/satyamini/.openclaw/workspace/projects/edoras/schedule_daily.sh

# Signal alerts (8 AM, 12 PM, 4 PM EDT / 12, 16, 20 UTC)
0 12,16,20 * * * /home/satyamini/.openclaw/workspace/projects/edoras/run_signal_alerts.sh

# Price alerts every 30 min during market hours (9 AM‑7 PM EDT / 13‑23 UTC)
*/30 13-23 * * * /home/satyamini/.openclaw/workspace/projects/edoras/price_alerts_cron.sh
```

### Proposed Additions
```bash
# Weekly paper portfolio rebalancing (Monday 9:00 AM EDT / 13:00 UTC)
0 13 * * 1 /home/satyamini/.openclaw/workspace/projects/edoras/run_paper_rebalancing.sh

# Daily paper portfolio performance report (5:00 PM EDT / 21:00 UTC)
0 21 * * * /home/satyamini/.openclaw/workspace/projects/edoras/run_paper_report.sh

# End‑of‑day summary (8:00 PM EDT / 0:00 UTC next day)
0 0 * * * /home/satyamini/.openclaw/workspace/projects/edoras/run_eod_summary.sh
```

### Alert Strategy
- **Periodic checks**: Daily at market open (8:30 AM EDT)
- **Signal‑based alerts**: Real‑time when strong signals detected (RSI<30/>70, MACD crossover)
- **Portfolio alerts**: When concentration risk exceeds threshold (>80% in top 3 holdings)
- **Performance alerts**: Daily P&L report at market close (5:00 PM EDT)

### Hybrid Approach
1. **Daily morning check**: Full analysis, optimization report
2. **Intra‑day alerts**: Signal‑based notifications (no more than 2‑3 per day)
3. **Weekly rebalancing**: Paper portfolio adjustment every Monday
4. **Monthly review**: Comprehensive performance analysis and strategy adjustment

---

## Telegram Integration

### Message Types
1. **Technical Analysis Report**: Daily market overview, top/bottom performers
2. **Portfolio Snapshot**: Current holdings, values, P&L
3. **Optimization Report**: Rebalancing suggestions, risk metrics
4. **Signal Alerts**: Buy/sell opportunities with rationale
5. **Paper Trading Report**: Virtual portfolio performance
6. **System Status**: Pipeline health, errors, maintenance needs

### Message Formatting
- **No markdown tables** (Telegram limitation) – use bullet lists instead
- **Emoji indicators**: 🟢 Bullish, 🔴 Bearish, ⚪ Neutral
- **Character limit**: 4000 characters per message (Telegram limit)
- **Multi‑message**: Split long reports with continuation markers

---

## Troubleshooting

### Common Issues

#### 1. Coinbase API Authentication Failures
```
"Unable to load PEM file. MalformedFraming"
```
**Solution**: Ensure `\n` characters are properly escaped in environment variables:
```bash
export COINBASE_API_SECRET="-----BEGIN EC PRIVATE KEY-----\nMHcCAQEE...\n-----END EC PRIVATE KEY-----\n"
```

#### 2. Insufficient Historical Data
```
"Not enough data for indicators"
```
**Solution**: Run data collection for 14+ days to build sufficient history.

#### 3. Telegram Gateway Offline
```
"Failed to send Telegram message"
```
**Solution**: Check OpenClaw gateway status:
```bash
openclaw gateway status
systemctl --user restart openclaw-gateway
```

#### 4. Node.js Version Mismatch
```
"Node.js v22.12+ is required (current: v20.10.0)"
```
**Solution**: Update Node.js:
```bash
nvm install v22.22.1
nvm use v22.22.1
```

#### 5. Cron Environment Issues
```
"nvm: command not found"
```
**Solution**: Use absolute paths in wrapper scripts:
```bash
#!/bin/bash
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
nvm use v22.22.1
```

### Log Files
- **Data collection**: `daily_analysis.log`
- **Telegram errors**: OpenClaw gateway logs
- **System errors**: `~/openclaw_errors.log`
- **Paper trading**: `paper_trading.log` (to be implemented)

### Monitoring
```bash
# Check pipeline status
./monitoring/status.sh

# View recent logs
tail -f daily_analysis.log

# Database health
sqlite3 crypto_data.db "SELECT COUNT(*) FROM candlesticks;"
```

---

## Future Enhancements

### Short‑term (Next 2 Weeks)
1. **Backfill historical data** (90 days) for better risk metrics
2. **Correlation analysis** for portfolio diversification
3. **Stop‑loss/take‑profit** rules for paper trading
4. **Multi‑exchange support** (Binance, Kraken)

### Medium‑term (Next Month)
1. **Machine learning model** for price prediction
2. **Sentiment analysis** integration (news, social media)
3. **Automated rebalancing** with real trading (if permissions granted)
4. **Web dashboard** for visualization

### Long‑term (Next Quarter)
1. **Multi‑asset portfolio** (stocks, bonds, crypto)
2. **Tax‑loss harvesting** automation
3. **Risk‑parity optimization**
4. **Institutional‑grade reporting**

---

## Disclaimer

This system is for **educational and research purposes only**. It is **NOT financial advice**. The paper trading simulation uses virtual money. Any real trading decisions should be made with professional financial advice. Cryptocurrency investments carry significant risk, including total loss of capital.

---

**Last Updated**: 2026-03-15
**Version**: 4.2 (Multi-asset, multi-exchange, multi-portfolio, trend-aware LLM trading, trader attribution)
**Maintainer**: Regi (Quant Trading Agent)