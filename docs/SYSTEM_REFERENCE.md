# Edoras Trading System Reference

> Concise reference for the automated multi-asset trading system.
> For detailed documentation, see the `docs/` directory.
> **Last updated:** 2026-04-05

---

## Architecture

Multi-asset, multi-exchange trading system. Covers crypto (Coinbase CEX + Base DEX), prediction markets (Polymarket), equities (yfinance), and cross-asset correlations with VIX-based regime detection. Alerts and reports via Hermes to Telegram.

**Runtime**: Python 3.11+, installable package (`pip install -e .`), systemd user timers, SQLite (`crypto_data.db`, 53 tables).
**Package root**: `src/edoras/` — import as `from edoras.core.signal_trading import SignalTradingSystem`.
**Root shims**: Root-level `.py` files are thin shims that forward to the package; shell scripts continue to work unchanged.

---

## Package Structure

```
src/edoras/
  config.py              Central config: symbols, thresholds, risk params, scoring weights
  core/                  Trading engine
    signal_trading.py    Signal generation + execution orchestrator
    paper_trading.py     Paper portfolio manager
    risk_manager.py      Stop-loss, trailing stop, take-profit, circuit breaker
    risk_guardian.py     Portfolio-level drawdown, concentration, sector limits
    exit_overlay.py      Layer 2 exits (momentum, trend, vol, correlation contagion)
    exit_signals.py      Exit signal dataclasses
    smart_rebalancer.py  Drift-based rebalancer (category caps, quality gate, tx cost gate)
    regime_monitor.py    HMM/heuristic regime detection, strategy auto-swap
  data/
    indicator_calculator.py   17 standard + 16 binary indicators
    correlation_tracker.py    Cross-asset correlations, covariance matrix, VIX regime
    crypto_data_collector.py  Coinbase candle fetching + 4h aggregation
    equity_data_collector.py  Equity/index data via yfinance + 4h aggregation
    data_freshness_monitor.py 15-min staleness checks (93 feeds), gap detection, Telegram alerts
    daily_data_collection.py  Orchestrates full daily collection cycle
    dex_data_collector.py     DEX token OHLCV via GeckoTerminal
    historical_backfill.py    Backfill candles + indicators
    compute_all_indicators.py Bulk indicator recalculation
    intraday_update.py        Intraday 1h candle refresh
  llm/
    llm_chain.py         Shared 5-tier LLM service (DeepSeek → Nous → Claude → GPT-4o → MLX)
    llm_gatekeeper.py    Fail-open BUY signal validator (batch, 5-min cache, audit trail)
    research_agent.py    Stage 1: qualitative research (sentiment, patterns, narrative)
    trading_agent.py     Stage 2: LLM trading sessions informed by research + quant signals
    market_intelligence.py   Market context + vector similarity search
    research_reader.py   Academic paper ingestion → market intelligence
    sentiment.py         CryptoSentiment aggregator
    vector_store.py      SQLite-vec embedding store
  dex/
    dex_executor.py      DEX execution via Bankr API
    dex_trading_agent.py Arwen portfolio LLM agent
    dex_risk_rules.py    DEX position risk limits
    bankr_client.py      Bankr REST client
  scoring/
    advanced_scorer.py   Multi-factor scoring: momentum, trend, vol, volume, risk-adj, quality
    enhanced_optimizer.py PortfolioOptimizer: max-Sharpe / min-variance / risk-parity (SLSQP)
    strategy_tracker.py  Signal logging, outcome tracking, attribution
    score_trajectories.py Score history analysis
  reports/
    report_engine.py     7 PDF report types, daily Telegram delivery
    telegram_fmt.py      Telegram message formatting helpers
    trade_journal.py     Trade narrative + vector similarity
    price_alerts.py      Price threshold breach alerts
    signal_alerts.py     Signal strength alerts
    automated_portfolio_report.py  Automated portfolio PDF
  cli/
    cli.py               Unified query interface (snapshot, trades, signals, health, dex)
    dashboard.py         Live TUI dashboard (30s refresh, Rich)
  backtest/              Backtesting engine (already a proper package)
  realtime/              WebSocket feeds (ingest/, risk/)
```

---

## Core Modules (quick reference)

| Layer | Import path | Purpose |
|-------|------------|---------|
| Config | `edoras.config` | Symbols, thresholds, risk params, scoring weights |
| Signal | `edoras.core.signal_trading` | Signal generation + execution orchestrator |
| Execution | `edoras.core.paper_trading` | Paper portfolio manager |
| Execution | `edoras.llm.trading_agent` | LLM-driven trading sessions |
| Risk | `edoras.core.risk_manager` | Stop-loss, trailing stop, take-profit, circuit breaker |
| Risk | `edoras.core.risk_guardian` | Portfolio-level drawdown + sector limits |
| Exit | `edoras.core.exit_overlay` | Layer 2 exit conditions on all held positions |
| Rebalance | `edoras.core.smart_rebalancer` | Drift-based rebalancer with category caps |
| Regime | `edoras.core.regime_monitor` | HMM/heuristic regime detection, strategy auto-swap |
| Indicators | `edoras.data.indicator_calculator` | 17 standard + 16 binary indicators |
| Correlation | `edoras.data.correlation_tracker` | Correlations, covariance matrix, VIX regime |
| Freshness | `edoras.data.data_freshness_monitor` | 15-min staleness checks, 93 feeds |
| LLM | `edoras.llm.llm_chain` | 5-tier LLM service (fail-open, cached, rate-limited) |
| Research | `edoras.llm.research_agent` | Stage 1: sentiment, patterns, narrative → ResearchBrief |
| Execution | `edoras.llm.trading_agent` | Stage 2: research + quant → trade decisions |
| Gate | `edoras.llm.llm_gatekeeper` | BUY signal validator (SELL always bypasses) |
| Scoring | `edoras.scoring.advanced_scorer` | Multi-factor position scoring (6 components) |
| Optimizer | `edoras.scoring.enhanced_optimizer` | True mean-variance portfolio optimization |
| CLI | `edoras.cli.cli` | Query interface |
| Dashboard | `edoras.cli.dashboard` | Live TUI |

---

## Portfolios

| Portfolio | ID | Mode | Executor(s) | Symbols |
|-----------|-----|------|-------------|---------|
| Galadriel | 1 | paper | Signal Engine, Regi | ETH, BTC, XRP, FET, DOGE, BNB, ADA, AVAX, GRT |
| Thranduil | 2 | live | Regi (inactive) | — |
| Elrond | 3 | tracked | Satyam, Regi | — |
| Arwen | 4 | live (DEX) | Aleph | VVV, BNKR, WETH, USDC (Base) |

### Galadriel Strategy Routing (4h timeframe)

| Symbol | Strategy |
|--------|----------|
| ADA-USD | MultiSignal |
| AVAX-USD | MultiSignal |
| BTC-USD | BollingerReversion |
| DOGE-USD | RegimeAware |
| UNI-USD | RegimeAware |
| XRP-USD | MultiSignal |

Routed symbols use their backtested strategy exclusively. Strategy silence = hold. Unrouted symbols default to MultiSignal.

---

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
| `BearDefensive` | Tight mean-reversion | bear (**not live** — failed walk-forward, needs re-validation) |

---

## Portfolio Optimizer

`PortfolioOptimizer` (in `edoras.scoring.enhanced_optimizer`) uses true mean-variance optimization via SLSQP. Three methods:

| Method | Algorithm | Use case |
|--------|-----------|----------|
| `max_sharpe` (default) | Max Sharpe SLSQP → analytical → inverse-vol | General allocation |
| `min_variance` | Min global variance SLSQP → inverse-vol | Risk-averse |
| `risk_parity` | Inverse-volatility | Simple, robust fallback |

Constraints: long-only, 30% per-position cap, 10% meme cap, 40% large-cap floor. Falls back gracefully on any solver failure.

```python
from edoras.scoring.enhanced_optimizer import PortfolioOptimizer
opt = PortfolioOptimizer()
weights = opt.get_optimal_weights(method="max_sharpe", symbols=["BTC-USD", "ETH-USD", ...])
# -> {"BTC-USD": 0.32, "ETH-USD": 0.28, ...}
```

---

## Scheduling (systemd timers)

| Timer | Schedule | Purpose |
|-------|----------|---------|
| `coinbase-websocket` | 24/7 | Persistent WebSocket feed (18 crypto symbols) |
| `risk-guardian` | Every 30m (24/7) | Portfolio risk monitoring — all hours including overnight |
| `data-freshness-monitor` | Every 15m | Staleness checks on 93 feeds, Telegram alerts on stale |
| `crypto-intraday-update` | Every 2h | 1h candle refresh + 4h aggregation + indicators |
| `crypto-signal-trading` | Every 4h (00:05-20:05 UTC) | Signal generation + trade execution |
| `polymarket-ingest` | Every 4h | Polymarket data collection |
| `dex-data-collection` | Every 2h | DEX token OHLCV via GeckoTerminal |
| `trading-agent` | 8:45 AM | LLM morning trading session |
| `midday-trading-review` | 12:30 PM | LLM midday review session |
| `edoras-daily-reports` | 5:30 PM | Generate + deliver 7 PDF reports |
| `db-backup` | Daily 03:00 UTC | Hot SQLite backup, 7-day retention |

---

## Quick Commands

```bash
cd ~/edoras

# Portfolio state
python3 cli.py snapshot                      # Positions, P&L, cash
python3 cli.py trades --hours 24 -v          # Recent trades with reasoning
python3 cli.py signals --hours 24            # Signals: executed vs skipped
python3 cli.py health                        # Data freshness, timer status

# Analysis
python3 -c "from edoras.core.regime_monitor import check_and_swap; ..."
python3 -c "from edoras.data.correlation_tracker import CorrelationTracker; ct = CorrelationTracker(); print(ct.generate_report())"

# Signal engine
set -a && source ~/.config/coinbase.env && set +a
python3 signal_trading.py --test             # Dry run (uses shim → edoras.core.signal_trading)
python3 -m edoras.core.signal_trading --test # Direct package invocation

# Optimizer
python3 -m edoras.scoring.enhanced_optimizer --weights --method max_sharpe

# Data freshness
python3 data_freshness_monitor.py --report --gaps --no-alert

# Reports
python3 report_engine.py all                 # Generate all 7 PDF reports

# DEX (Arwen)
python3 cli.py dex balance                   # Wallet balance
python3 cli.py dex buy BNKR-BASE 50         # Buy $50 worth

# Dashboard
edoras-dashboard                             # Live TUI (30s refresh)
edoras-dashboard --once                      # Single render
```

---

## Database

SQLite: `crypto_data.db` (~276 MB), 53 tables.

Key table groups: Core Market Data (`candlesticks`, `indicators`), Trading (`trades`, `positions`, `trade_outcomes`), Strategy (`strategy_registry`, `strategy_signals_log`), Portfolio (`portfolios`, `accounts`, `paper_snapshots`), Dimension (`exchanges`, `securities`), Intelligence (`market_regime`, `sentiment_scores`, `correlations`), DEX (`dex_tokens`, `dex_transactions`).

---

## Risk Rules (Summary)

- **Stop-loss**: 10% below entry
- **Trailing stop**: activates after 5% gain, trails at 2x ATR
- **Take-profit**: scale-out at +15% / +20% / +25%
- **Circuit breaker**: 15% portfolio drawdown → liquidate all, auto-resets after 24h cooldown or when cash ≥ 80% of portfolio
- **Position cap**: 25% per position, 40% per sector
- **Category caps**: 10% meme coins combined, 15% small-cap combined; 40% large-cap floor (BTC+ETH+BNB+SOL)
- **Regime adjustment**: risk-off dampens buys 0.5x, amplifies sells 1.3x
- **Transaction cost gate**: rebalance trades skipped if estimated fee > 50% of drift correction benefit
- **Quality gate**: symbols below tier quality threshold excluded from rebalance universe

---

## Scoring Model Weights

| Component | Weight | Purpose |
|-----------|--------|---------|
| Momentum | 25% | RSI, MACD, price vs MAs |
| Trend | 15% | ADX, MA slopes, golden/death cross |
| Risk-Adjusted | 25% | Sharpe ratio, drawdown, VaR |
| Volatility | 15% | ATR, BB width |
| Volume | 10% | Volume trends, vol-price confirmation |
| Quality | 10% | Market cap tier + 30-day Sharpe + liquidity |

Weights live in `edoras.config.SCORER_WEIGHTS` — configurable without touching scorer code.

---

## Documentation Index

| Document | Description |
|----------|-------------|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | System architecture, data flow, module reference |
| [docs/DATABASE.md](docs/DATABASE.md) | Production database schema, key queries, data extraction |
| [docs/STRATEGIES.md](docs/STRATEGIES.md) | 10 backtested strategies with regime affinity matrix |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Systemd timers, daily timeline, shell script reference |
| [docs/TRADING_RULES.md](docs/TRADING_RULES.md) | Trading philosophy, risk rules, position sizing |
| [docs/POLYMARKET.md](docs/POLYMARKET.md) | Polymarket signal overlay integration |
| [docs/DEX.md](docs/DEX.md) | DEX trading via Bankr API (Arwen portfolio) |
| [improvements/edoras_improvement_plan.md](improvements/edoras_improvement_plan.md) | Implementation log for all improvement phases |
