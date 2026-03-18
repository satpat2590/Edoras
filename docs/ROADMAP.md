# Trading System Roadmap — Path to Live Trading

_Living document. Updated as phases are completed._
_Last updated: 2026-03-16_

## Current State

- 25 systemd timers (all Persistent=true) + 1 persistent multi-feed WebSocket service
- 52+ symbols: 18 crypto (Coinbase), 4 DEX tokens (GeckoTerminal), 20+ prediction markets (Polymarket), 10 equity (yfinance), 3 index
- 250K+ candles, 400 days crypto history, 7 days DEX history, 730 days equity
- Exchange-agnostic WebSocket architecture: Coinbase + Polymarket feeds via multi-feed supervisor
- REST gap-fill: crypto (2h), DEX (2h), Polymarket (4h), equity (daily)
- Dual indicator profiles: standard (17 indicators) + binary (16 indicators for prediction markets)
- 7 backtested strategies, 166 backtest results, per-symbol strategy routing
- 4 named portfolios: Galadriel (paper/active), Thranduil (live/pending), Elrond (tracked/pending), Arwen (DEX-live/active)
- Warehouse redesign Phase 1-3 complete: accounts bridge table, dual-write account_id, all read queries migrated to account-based filtering
- Dimension tables: exchanges (5 venues), securities (52+), strategy_registry (7), portfolios (4), accounts (4), traders (5)
- Risk guardian every 30 min (stops, trailing, TP, circuit breaker)
- DEX risk rules: liquidity, slippage, holder count, position vs pool
- Trade journal with outcome tracking (trade_outcomes table)
- Two agents: Aleph (general-purpose, DEX executor) and Regi (quant specialist, @reginaldonaldobot)
- Paper trading 4x daily with DB position sync

---

## Phase 1 — Validate the Edge ✅ COMPLETE

**Goal:** Prove at least one strategy has positive expected value with corrected ADX data.

**Gate criteria:** ≥1 strategy with Sharpe > 0.5, win rate > 40%, profit factor > 1.2 across ≥3 symbols.

### Tasks
- [x] Run backtests across all 18 crypto symbols × 7 strategies (126 tests)
- [x] Run backtests on 1d timeframe (primary) and 4h timeframe (secondary, 40 more tests)
- [x] Identify best-performing strategy per symbol category
- [x] Tune parameters — added 4 new strategies (ADXTrend, BollingerReversion, MultiSignal, ScoreBasedRelaxed)
- [x] Document results in `backtest_results/phase1_report.md`
- [x] Walk-forward validation (limited by low trade counts; full-period results used instead)
- [x] Wire winning strategies into signal_trading.py for paper trading
- [x] Configure per-symbol strategy routing (ETH→Bollinger 4h, DOGE/ADA→MultiSignal 1d)
- [x] Store 166 backtest results in strategy_performance DB table
- [x] Wire signal logging into strategy_signals_log DB table

### Success metrics
| Metric | Minimum | Target |
|--------|---------|--------|
| Sharpe ratio | > 0.5 | > 1.0 |
| Win rate | > 40% | > 50% |
| Profit factor | > 1.2 | > 1.5 |
| Max drawdown | < 25% | < 15% |
| Symbols passing | ≥ 3 | ≥ 8 |

---

## Phase 2 — Paper Trading Burn-In (2 weeks) ← CURRENT

**Goal:** Validate that live signal generation matches backtest behavior.

**Gate criteria:** Paper trading results within 20% of backtest expectations over 10 trading days.

**Started:** 2026-03-12

### Tasks
- [x] Deploy winning strategies as active paper trading strategies
- [x] Configure 4h candle rollup in WebSocket (1h→4h, with indicator recompute)
- [x] Fix compute_all_indicators.py to use canonical indicator_calculator
- [x] Strategy performance tracking DB (strategy_performance + strategy_signals_log tables)
- [ ] Monitor daily: trade frequency, signal accuracy, regime filter behavior
- [ ] Compare paper fills vs backtest expectations (use strategy_tracker.compare_backtest_vs_paper())
- [ ] Review trade journal entries for anomalies
- [ ] Verify WebSocket data quality (compare 1h candles vs REST API)
- [ ] Risk guardian validation: check for false positives and missed events
- [ ] Track slippage between signal time and paper execution time

### Success metrics
| Metric | Minimum |
|--------|---------|
| Signal-to-backtest correlation | > 0.8 |
| No missed risk events | 100% |
| WebSocket uptime | > 99% |
| Data gaps (>1h) | 0 |

---

## Phase 3 — Dry-Run Mode (2 weeks)

**Goal:** Validate execution logic against real Coinbase order book without placing orders.

**Gate criteria:** Dry-run fills match paper fills within 2% slippage estimate.

### Tasks
- [ ] Enable dry-run mode in live_executor.py (already has the mode)
- [ ] Log what orders would have been placed (side, size, limit price)
- [ ] Compare dry-run execution prices vs actual market prices at signal time
- [ ] Measure realistic slippage for each symbol
- [ ] Validate position sizing logic (Kelly criterion / volatility targeting)
- [ ] Stress-test circuit breaker with simulated drawdown scenarios
- [ ] Verify API connectivity and authentication with Coinbase
- [ ] Test order types: market vs limit, and fill rates

### Success metrics
| Metric | Minimum |
|--------|---------|
| Estimated slippage | < 0.5% |
| Order placement latency | < 2s |
| Circuit breaker fires correctly | 100% |
| API auth valid | Confirmed |

---

## Phase 4 — Live Trading MVP (4 weeks)

**Goal:** Execute real trades with training wheels.

**Gate criteria:** 2 weeks profitable (or flat) with BTC-USD only before expanding.

### Tasks
- [ ] Set `LIVE_TRADING_ENABLED=true` in coinbase.env
- [ ] Start with BTC-USD only, $50/order max, $200/day limit (already configured)
- [ ] Monitor every trade via Telegram alerts (Regi sends execution confirmations)
- [ ] Daily P&L review in trade journal
- [ ] After 2 profitable weeks: add ETH-USD, SOL-USD
- [ ] After 4 weeks: expand to full portfolio universe if metrics hold
- [ ] Gradually increase limits: $100/order, $500/day

### Safety rails (already implemented)
- $50 max single order (`LIVE_MAX_SINGLE_ORDER_USD`)
- $200 max daily volume (`LIVE_MAX_DAILY_VOLUME_USD`)
- 5 max open orders (`LIVE_MAX_OPEN_ORDERS`)
- 60s min between orders (`LIVE_MIN_ORDER_INTERVAL_SEC`)
- 15% portfolio circuit breaker (`MAX_PORTFOLIO_DRAWDOWN`)
- 10% stop-loss per position (`STOP_LOSS_PCT`)

### Success metrics
| Metric | Minimum | Action if missed |
|--------|---------|-----------------|
| Monthly return | > -5% | Pause, review |
| Max drawdown | < 15% | Circuit breaker auto-pauses |
| Win rate | > 35% | Review signal quality |
| Sharpe (rolling 30d) | > 0 | Reduce position sizes |

---

## Phase 5 — Scale Up (Ongoing)

**Goal:** Increase capital, add strategies, and expand asset universe.

### Tasks
- [ ] Multi-strategy portfolio (momentum + mean-reversion + macro)
- [ ] Strategy-level allocation based on rolling performance
- [ ] Kelly criterion position sizing (replace flat % allocation)
- [ ] Cross-asset hedging (reduce crypto when BTC-SPY correlation > 0.8 + risk-off)
- [ ] ML signal classifier (XGBoost pre-filter on trade journal data)
- [ ] Multi-exchange support (Binance, Kraken for better execution)
- [ ] Options/derivatives data integration
- [ ] Research-to-strategy pipeline (arXiv → backtest → deploy)

---

## Infrastructure Backlog

- [ ] Database consolidation (crypto_data.db → regi_dwh/financials.db)
- [ ] Kalshi prediction market integration (same pattern as Polymarket)
- [ ] Multi-exchange crypto (Binance, Kraken — subclass BaseWebSocketClient)
- [ ] Prediction market trading strategy development (leverage binary indicators)
- [ ] News article vector store for semantic retrieval
- [ ] Dashboard: web UI showing portfolio, regime, recent trades
- [ ] Database backup strategy (daily SQLite backup)
- [ ] Alerting escalation (3+ risk events/hour → escalate beyond Telegram)
- [ ] Requirements.txt / pyproject.toml for dependency pinning
- [ ] 24/7 risk guardian (currently 7 AM - 11 PM only)

---

## Completed

| Date | Item | Notes |
|------|------|-------|
| 2026-03-16 | Warehouse Phase 3: query migration | All read queries use account_id, 8 files updated, fallback for NULL |
| 2026-03-15 | Warehouse Phase 1+2 | accounts bridge, dual-write account_id in all execution modules |
| 2026-03-15 | DEX integration (Arwen portfolio) | bankr_client, dex_executor, dex_data_collector, GeckoTerminal |
| 2026-03-12 | Multi-feed WebSocket supervisor | Exchange-agnostic base class, Coinbase + Polymarket concurrent |
| 2026-03-12 | Polymarket integration | 20+ markets, WS + REST, binary indicators (16 columns) |
| 2026-03-12 | Multi-portfolio system | 3 portfolios (Galadriel/Thranduil/Elrond), DB-driven config |
| 2026-03-12 | Dimension tables | exchanges, securities, strategy_registry, portfolios |
| 2026-03-12 | Data warehouse sync fixes | Strategy fallback, signal dedup, position sync, ADX clamp |
| 2026-03-12 | Phase 1 complete, Phase 2 started | 166 backtests, 7 strategies, paper trading live |
| 2026-03-12 | Strategy performance DB | strategy_performance + strategy_signals_log tables |
| 2026-03-12 | 4h candle rollup in WebSocket | 1h→4h aggregation + indicator recompute |
| 2026-03-12 | compute_all_indicators.py rewrite | Uses canonical indicator_calculator (BB, ADX fix) |
| 2026-03-11 | Coinbase WebSocket streaming | 18 symbols, 5m→1h rollup, auto indicators |
| 2026-03-11 | ADX fix + full recomputation | Wilder's EMA, mutual exclusivity, 244K rows |
| 2026-03-11 | Trade journal system | trade_outcomes table, analytics, LLM feedback |
| 2026-03-11 | Regi quant agent | DeepSeek-chat, Telegram @reginaldonaldobot |
| 2026-03-11 | Signal trading fix | Trend-following + momentum signals, CLI fix |
| 2026-03-10 | Historical backfill (400 days, 18 crypto) | 245K+ candles |
| 2026-03-10 | Equity data collection (10 stocks + 3 indices) | 730 days via yfinance |
| 2026-03-10 | Risk manager (stop/trail/TP/circuit breaker) | Enforced in signal pipeline |
| 2026-03-10 | Backtesting framework | Walk-forward, 3 strategies |
| 2026-03-10 | Correlation tracker + VIX regime | Daily snapshots |
| 2026-03-10 | Trading agent (LLM-powered decisions) | Morning + midday reviews |
| 2026-03-10 | Risk guardian (30-min defensive loop) | Auto-exits, no LLM |
| 2026-03-10 | Market intelligence vector store | Semantic search over history |
| 2026-03-10 | Smart rebalancer (drift-based) | Score-weighted, cost-aware |
| 2026-03-10 | Live executor (paper/dry-run/live) | Safety limits enforced |
| 2026-03-10 | Research reader (arXiv daily) | Journal + vector memory |
| 2026-03-10 | Gateway outage logging | risk_events.jsonl + outage-log.jsonl |
| 2026-03-10 | systemd migration (21 timers) | All Persistent=true |
| 2026-03-10 | Paper portfolio persistence | Full state serialization |
