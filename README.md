# Edoras

Multi-asset, multi-exchange trading system with regime-adaptive strategy routing. Covers crypto (Coinbase CEX + Base DEX), prediction markets (Polymarket), equities (yfinance), and cross-asset correlation tracking with VIX-based regime detection. Paper trading first — live execution when ready.

## Architecture

```
REAL-TIME FEEDS                    ANALYSIS                      EXECUTION
─────────────                      ────────                      ─────────
Coinbase WS (18 crypto)  ─┐
                           ├─ 1h → 4h rollup ─→ indicators ─→ signal_trading.py
Polymarket WS (20+ mkts) ─┤                                   ├── strategy routing (13 strategies)
                           │                                   ├── regime_monitor.py (HMM/heuristic)
REST gap-fill (2h cycle)  ─┘                                   ├── risk_manager.py (stops, CB)
                                                               └── paper_trading.py → trades DB
yfinance (daily)  ─→ equity_data_collector.py                         │
RSS feeds ────────→ sentiment.py                                      v
                                                               OpenClaw → Telegram
```

## Quick Start

```bash
cd ~/.openclaw/workspace/projects/edoras

# Initialize database + backfill history
python3 scripts/bootstrap_db.py
python3 historical_backfill.py --days 400

# Run signal trading (dry run)
set -a && source ~/.config/coinbase.env && set +a
python3 signal_trading.py --test

# Check portfolio
python3 cli.py snapshot
```

## Key Commands

```bash
python3 cli.py snapshot              # Positions, P&L, cash
python3 cli.py trades --hours 24 -v  # Recent trades with reasoning
python3 cli.py signals --hours 24    # Signals: executed vs skipped
python3 cli.py health                # Data freshness, timer status
python3 cli.py signal-trace          # Trace signal flow through gates
python3 regime_monitor.py --detect   # Current regime per symbol
python3 report_engine.py all         # Generate 7 PDF reports
python3 cli.py dex balance           # DEX wallet balance (Arwen)
```

## Project Structure

```
edoras/
├── config.py                    # Central config: symbols, thresholds, asset-class profiles
├── indicator_calculator.py      # 17 standard + 16 binary indicators
├── signal_trading.py            # Signal generation + execution orchestrator
├── regime_monitor.py            # HMM/heuristic regime detection + strategy swap
├── paper_trading.py             # Paper portfolio manager (positions, trades, P&L)
├── risk_manager.py              # Stop-loss, trailing stop, take-profit
├── risk_guardian.py             # Portfolio-level drawdown + circuit breaker
├── trading_agent.py             # LLM-driven trading sessions
├── live_executor.py             # Coinbase live execution (dry-run/paper/live)
├── cli.py                       # Unified CLI (snapshot, trades, signals, health)
├── report_engine.py             # 7 PDF report types
│
├── dex_executor.py              # DEX execution via Bankr API
├── dex_trading_agent.py         # DEX LLM trading orchestrator
├── dex_data_collector.py        # DEX token OHLCV via GeckoTerminal
│
├── crypto_data_collector.py     # Coinbase candle fetching
├── intraday_update.py           # 1h candle refresh + 4h aggregation
├── equity_data_collector.py     # Equity/index data via yfinance
├── correlation_tracker.py       # Cross-asset correlations + VIX regime
├── providers/polymarket.py      # Polymarket data collection
│
├── backtest/
│   ├── engine.py                # Core backtester with walk-forward
│   ├── strategies/              # 13 registered strategies
│   ├── compare.py               # Multi-strategy comparison
│   ├── catalogue.py             # Persistent strategy catalogue
│   └── deployer.py              # Catalogue → live route bridge
│
├── realtime/
│   ├── ingest/                  # WebSocket clients (Coinbase, Polymarket)
│   └── supervisor.py            # Multi-feed supervisor with auto-restart
│
├── scripts/                     # Manual utilities (bootstrap, tax, reports)
├── migration/                   # Database schema migrations
├── docs/                        # Detailed documentation (see index below)
├── tests/                       # Test scripts
└── archive/                     # Deprecated files (retained 30 days)
```

## Portfolios

| Portfolio | ID | Mode | Symbols | Strategy |
|-----------|-----|------|---------|----------|
| Galadriel | 1 | paper | ADA, AVAX, BTC, DOGE, UNI, XRP | Per-symbol routing (4h) |
| Thranduil | 2 | live | — | Inactive |
| Elrond | 3 | tracked | — | Manual |
| Arwen | 4 | live (DEX) | VVV, BNKR, WETH, USDC | Base chain via Bankr |

## Strategies

13 backtested strategies across 4 types:

| Type | Strategies | Best Regime |
|------|-----------|-------------|
| Momentum | ScoreBased, ScoreBasedRelaxed, EnhancedScoreBased, MACDCross, TSMOM, TSMOM_3M | Bull |
| Trend | ADXTrend | Bull |
| Mean-reversion | BollingerReversion, PairsTrading, PairsTrading_Aggressive | Sideways |
| Adaptive | RegimeAware, RegimeAware_Heuristic, MultiSignal | All regimes |

Regime detection runs before every signal check. When regime shifts, strategies auto-swap via `regime_monitor.py`.

## Risk Management

- **Stop-loss**: 10% below entry
- **Trailing stop**: activates after 5% gain, trails at 2x ATR
- **Take-profit**: scale-out at +15% / +20% / +25%
- **Circuit breaker**: 15% portfolio drawdown → liquidate all
- **Position cap**: 25% per position, 40% per sector
- All thresholds are asset-class-aware via `config.ASSET_CLASS_PROFILES`

## Database

SQLite (`crypto_data.db`), 30+ tables. Key groups: market data (candlesticks, indicators), trading (trades, positions, trade_outcomes), strategy (strategy_registry, strategy_signals_log), portfolios, dimension tables, market intelligence, DEX.

## Documentation

| Document | Description |
|----------|-------------|
| [SYSTEM_REFERENCE.md](SYSTEM_REFERENCE.md) | Concise system overview with quick commands |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Full architecture, data flow, module reference |
| [docs/DATABASE.md](docs/DATABASE.md) | Production schema, key queries, data extraction |
| [docs/STRATEGIES.md](docs/STRATEGIES.md) | 13-strategy catalog with regime affinity matrix |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Systemd timers, daily timeline, troubleshooting |
| [docs/TRADING_RULES.md](docs/TRADING_RULES.md) | Trading philosophy, risk rules, position sizing |
| [docs/POLYMARKET.md](docs/POLYMARKET.md) | Polymarket signal overlay integration |
| [docs/DEX.md](docs/DEX.md) | DEX trading via Bankr API (Arwen portfolio) |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Project roadmap and status |

## Requirements

- Python 3.10+, SQLite 3.35+
- See `requirements.txt` for Python dependencies
- Optional: `hmmlearn` for HMM regime detection, `statsmodels` for cointegration tests

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `COINBASE_API_KEY` | For CEX | Coinbase Advanced Trade API key |
| `COINBASE_API_SECRET` | For CEX | EC private key (PEM format) |
| `TELEGRAM_CHAT_ID` | For alerts | Telegram chat ID |
| `OPENAI_API_KEY` | For LLM | GPT-4o-mini (sentiment, trading agent) |
| `BANKR_API_KEY` | For DEX | Bankr API for on-chain trading |

## License

MIT
