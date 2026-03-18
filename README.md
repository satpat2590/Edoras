# Edoras

A modular, strategy-routed multi-asset trading system with regime-adaptive portfolio management.

**Paper trading first. Live execution when you're ready.**

## What It Does

- **13 backtested strategies** — score-based, MACD, ADX trend, Bollinger reversion, TSMOM (momentum), pairs trading, regime-aware (HMM + heuristic)
- **Regime detection** — classifies each symbol as bull/bear/sideways using ADX, SMA slope, MACD, RSI, and volatility scoring. Automatically swaps strategies when regime changes.
- **Strategy catalogue** — persistent record of all backtest results. Rank strategies by any metric, filter winners, build portfolio templates with Sharpe-weighted allocations.
- **Risk management** — stop-loss, trailing stops (ATR-based), take-profit scale-out, circuit breaker, position/sector limits, VIX regime filter
- **Multi-portfolio** — isolated portfolios with per-symbol strategy routing, independent state, and full trade audit trail
- **Multi-asset** — crypto (CEX via Coinbase), DEX (via Bankr API on Base/Ethereum), equities (via yfinance), prediction markets (Polymarket)
- **Real-time data** — WebSocket streaming (Coinbase, Polymarket) with 5m candle aggregation, hourly rollups, and indicator recomputation
- **PDF reports** — dark-themed backtest reports, equity curves, monthly return heatmaps, strategy comparison charts
- **Systemd scheduling** — all jobs survive laptop suspend/resume with `Persistent=true`

## Quick Start

```bash
# Clone
git clone https://github.com/satpat2590/Edoras.git
cd Edoras

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example ~/.config/edoras.env
# Edit ~/.config/edoras.env with your API keys
source ~/.config/edoras.env

# Initialize database
python3 bootstrap_db.py
python3 migration/phase1_warehouse_redesign.py

# Backfill historical data (400+ days)
python3 historical_backfill.py

# Run backtests
python3 -c "from backtest import compare_strategies; compare_strategies(['BTC-USD', 'ETH-USD'])"

# Start paper trading
python3 signal_trading.py --portfolio Galadriel
```

## Architecture

```
edoras/
├── config.py                  # Central config (symbols, thresholds, risk params)
├── signal_trading.py          # Strategy-routed signal pipeline + regime monitor
├── paper_trading.py           # Paper portfolio execution with audit trail
├── risk_manager.py            # Stop-loss, trailing stop, take-profit, circuit breaker
├── indicator_calculator.py    # 17 standard + 16 binary indicators
├── regime_monitor.py          # Bull/bear/sideways detection + strategy swapping
├── backtest/
│   ├── engine.py              # Core backtester with walk-forward support
│   ├── metrics.py             # 23 performance metrics
│   ├── strategies/            # 13 registered strategies (@register_strategy)
│   ├── compare.py             # Multi-strategy x multi-symbol comparison
│   ├── report.py              # PDF report generation
│   ├── catalogue.py           # Persistent strategy catalogue + portfolio templates
│   └── deployer.py            # Catalogue → warehouse → live route bridge
├── realtime/
│   ├── ingest/                # WebSocket clients (Coinbase, Polymarket)
│   ├── supervisor.py          # Multi-feed supervisor with auto-restart
│   └── risk/                  # Real-time risk monitoring
├── migration/                 # Database schema migrations
├── docs/                      # Architecture docs, trading philosophy
└── tests/                     # Integration and unit tests
```

## Strategies

| Strategy | Type | Best Regime | Description |
|----------|------|-------------|-------------|
| ScoreBased | Multi-factor | All | 5-component scoring (momentum/trend/vol/volume/risk) |
| ScoreBasedRelaxed | Multi-factor | All | Relaxed thresholds for more signals |
| EnhancedScoreBased | Multi-factor | All | Weighted scoring with confirmation |
| MACDCross | Momentum | Bull | MACD crossover with volume confirmation |
| ADXTrend | Trend | Bull | ADX trend strength filter + directional movement |
| BollingerReversion | Mean-reversion | Sideways | Bollinger Band mean-reversion |
| MultiSignal | Multi-factor | All | Consensus across RSI, MACD, BB, SMA |
| TSMOM | Momentum | Bull | 12-month time-series momentum, inverse-vol sizing |
| TSMOM_3M | Momentum | Bull | 3-month lookback variant |
| PairsTrading | Mean-reversion | Sideways | Z-score with Ornstein-Uhlenbeck half-life |
| PairsTrading_Aggressive | Mean-reversion | Sideways | Tighter entry/exit thresholds |
| RegimeAware | Adaptive | All | HMM regime detection → sub-strategy routing |
| RegimeAware_Heuristic | Adaptive | All | Heuristic regime detection (no HMM) |

## Environment Variables

See [`.env.example`](.env.example) for the full list. Key variables:

| Variable | Required | Description |
|----------|----------|-------------|
| `COINBASE_API_KEY` | For CEX | Coinbase Advanced Trade API key |
| `COINBASE_API_SECRET` | For CEX | EC private key (PEM format) |
| `TELEGRAM_CHAT_ID` | For alerts | Telegram chat ID for notifications |
| `OPENAI_API_KEY` | For LLM agent | GPT-4o-mini for sentiment + trading agent |
| `BANKR_API_KEY` | For DEX | Bankr API for on-chain trading |
| `DEX_WALLET_ADDRESS` | For DEX | Your EVM wallet address |

## Database

SQLite-based (`crypto_data.db`), created by `bootstrap_db.py` + migrations. Key tables:

- **candlesticks** / **indicators** — OHLCV + 17 technical indicators across timeframes
- **trades** / **positions** — full audit trail with trader_id, strategy_id, account_id
- **strategy_registry** — 13 strategies with type classification and parameters
- **strategy_catalogue** — backtest results for ranking and portfolio template generation
- **portfolio_templates** — Sharpe-weighted allocations from best strategies
- **strategy_swaps** — regime-triggered swap audit log
- **accounts** — portfolio-to-venue bridge (M:M)

## Requirements

- Python 3.10+
- SQLite 3.35+
- See `requirements.txt` for Python dependencies
- Optional: `hmmlearn` and `statsmodels` for HMM regime detection and cointegration tests

## License

MIT
