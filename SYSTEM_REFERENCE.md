# Edoras Trading System Reference

> Concise reference for the automated multi-asset trading system.
> For detailed documentation, see the `docs/` directory.
> **Last updated:** 2026-03-29

---

## Architecture

Multi-asset, multi-exchange trading system. Covers crypto (Coinbase), prediction markets (Polymarket), equities (yfinance), and cross-asset correlations with VIX-based regime detection. Alerts and reports via OpenClaw to Telegram.

**Runtime**: Python 3 on Linux (Pop!_OS), systemd user timers, SQLite (`crypto_data.db`).
**Agents**: Aleph (general-purpose) and Regi (quant-focused) via OpenClaw.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for full data flow, module reference, and asset-class profile system.

---

## Core Modules

| Layer | Module | Purpose |
|-------|--------|---------|
| Config | `config.py` | Central config: symbols, thresholds, risk params, asset-class profiles |
| Data | `crypto_data_collector.py`, `intraday_update.py` | Coinbase candle fetching + 4h aggregation |
| Data | `equity_data_collector.py` | Equity/index data via yfinance |
| Data | `providers/polymarket.py` | Polymarket REST: market discovery + price ingestion |
| Indicators | `indicator_calculator.py` | 17 standard + 16 binary indicators, profile-gated |
| Signals | `signal_trading.py` | Signal generation + execution orchestrator |
| Signals | `regime_monitor.py` | HMM-based regime detection, strategy auto-swap |
| Signals | `polymarket_signals.py` | PM probability shifts → crypto signal overlay |
| Execution | `paper_trading.py` | Paper portfolio manager (positions, trades, P&L) |
| Execution | `trading_agent.py` | LLM-driven trading sessions (morning + midday) |
| Execution | `live_executor.py` | Coinbase live execution (dry-run / paper / live modes) |
| Execution | `dex_executor.py`, `dex_trading_agent.py` | DEX execution via Bankr API (Arwen portfolio) |
| Risk | `risk_manager.py` | Stop-loss, trailing stop, take-profit, circuit breaker |
| Risk | `risk_guardian.py` | Portfolio-level drawdown, concentration, sector limits |
| Analysis | `correlation_tracker.py` | Cross-asset correlations, VIX regime classification |
| Analysis | `advanced_scorer.py` | Multi-factor position scoring |
| Reporting | `report_engine.py` | 7 PDF report types, daily Telegram delivery |
| CLI | `cli.py` | Unified query interface (snapshot, trades, signals, health, etc.) |

---

## Portfolios

| Portfolio | ID | Mode | Executor(s) | Symbols |
|-----------|-----|------|-------------|---------|
| Galadriel | 1 | paper | Signal Engine, Regi | ADA, AVAX, BTC, DOGE, UNI, XRP |
| Thranduil | 2 | live | Regi (inactive) | — |
| Elrond | 3 | tracked | Satyam, Aleph | — |
| Arwen | 4 | live (DEX) | Aleph | VVV, BNKR, WETH, USDC (Base) |

### Galadriel Strategy Routing (4h timeframe)

| Symbol | Strategy | ID |
|--------|----------|----|
| ADA-USD | MultiSignal | 4 |
| AVAX-USD | MultiSignal | 4 |
| BTC-USD | BollingerReversion | 3 |
| DOGE-USD | RegimeAware | 12 |
| UNI-USD | RegimeAware | 12 |
| XRP-USD | MultiSignal | 4 |

Routed symbols use their backtested strategy exclusively. Strategy silence = hold.
See [docs/STRATEGIES.md](docs/STRATEGIES.md) for the full 13-strategy catalog.

---

## Scheduling (systemd timers)

| Timer | Schedule | Purpose |
|-------|----------|---------|
| `coinbase-websocket` | 24/7 | Persistent WebSocket feed (18 crypto symbols) |
| `risk-guardian` | Every 30m (7AM-11PM) | Portfolio risk monitoring |
| `crypto-intraday-update` | Every 2h | 1h candle refresh + 4h aggregation + indicators |
| `crypto-signal-trading` | Every 4h (00:05-20:05 UTC) | Signal generation + trade execution |
| `polymarket-ingest` | Every 4h | Polymarket data collection |
| `dex-data-collection` | Every 2h | DEX token OHLCV via GeckoTerminal |
| `trading-agent` | 8:45 AM | LLM morning trading session |
| `midday-trading-review` | 12:30 PM | LLM midday review session |
| `edoras-daily-reports` | 5:30 PM | Generate + deliver 7 PDF reports |

See [docs/OPERATIONS.md](docs/OPERATIONS.md) for the full schedule and daily timeline.

---

## Quick Commands

```bash
cd ~/.openclaw/workspace/projects/edoras

# Portfolio state
python3 cli.py snapshot                      # Positions, P&L, cash
python3 cli.py trades --hours 24 -v          # Recent trades with reasoning
python3 cli.py signals --hours 24            # Signals: executed vs skipped
python3 cli.py health                        # Data freshness, timer status

# Analysis
python3 regime_monitor.py --detect-only      # Current regime
python3 correlation_tracker.py --report      # Cross-asset correlations

# Signal engine
set -a && source ~/.config/coinbase.env && set +a
python3 signal_trading.py --test             # Dry run

# Reports
python3 report_engine.py all                 # Generate all 7 PDF reports

# DEX (Arwen)
python3 cli.py dex balance                   # Wallet balance
python3 cli.py dex buy BNKR-BASE 50         # Buy $50 worth
```

---

## Database

SQLite: `crypto_data.db` (~115 MB), 30+ tables.

Key table groups: Core Market Data (candlesticks, indicators), Trading (trades, positions, trade_outcomes), Strategy (strategy_registry, strategy_signals_log), Portfolio (portfolios, accounts, paper_snapshots), Dimension (exchanges, securities), Intelligence (market_regime, sentiment_scores, correlations), DEX (dex_tokens, dex_transactions).

See [docs/DATABASE.md](docs/DATABASE.md) for full schema, key queries, and data extraction interface.

---

## Documentation Index

| Document | Description |
|----------|-------------|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | System architecture, data flow, module reference |
| [docs/DATABASE.md](docs/DATABASE.md) | Production database schema, key queries, data extraction |
| [docs/STRATEGIES.md](docs/STRATEGIES.md) | 13 backtested strategies with regime affinity matrix |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Systemd timers, daily timeline, shell script reference |
| [docs/TRADING_RULES.md](docs/TRADING_RULES.md) | Trading philosophy, risk rules, position sizing |
| [docs/POLYMARKET.md](docs/POLYMARKET.md) | Polymarket signal overlay integration |
| [docs/DEX.md](docs/DEX.md) | DEX trading via Bankr API (Arwen portfolio) |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Project roadmap and status |

---

## Risk Rules (Summary)

- **Stop-loss**: 10% below entry
- **Trailing stop**: activates after 5% gain, trails at 2x ATR
- **Take-profit**: scale-out at +15% / +20% / +25%
- **Circuit breaker**: 15% portfolio drawdown → liquidate all
- **Position cap**: 25% per position, 40% per sector
- **Regime adjustment**: risk-off dampens buys 0.5x, amplifies sells 1.3x

See [docs/TRADING_RULES.md](docs/TRADING_RULES.md) for the complete framework.
