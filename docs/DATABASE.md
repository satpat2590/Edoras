# Edoras Database Reference

**Last updated:** 2026-03-29
**Verified against code:** 2026-03-29
**Database:** `crypto_data.db` (SQLite)

---

## Schema Overview

### Core Market Data

| Table | Purpose | Key Columns | Rows | Latest Data |
|-------|---------|-------------|------|-------------|
| `candlesticks` | OHLCV price data across multiple timeframes (1m, 5m, 1h, 4h, 1d) for 238 symbols | symbol, timeframe, timestamp, open, high, low, close, volume | 780,999 | 2026-03-29 23:00 UTC |
| `indicators` | Technical indicators (SMA, EMA, RSI, MACD, BB, ADX, ATR, probability bands) computed from candlesticks for 115 symbols | symbol, timeframe, timestamp, sma_20/50/200, ema_12/26, rsi_14, macd_*, bb_*, atr_14, prob_* | 630,460 | 2026-03-29 23:00 UTC |
| `ticks` | Real-time tick-level price data | symbol, price, volume, bid, ask, timestamp | 99 | 2026-03-11 |
| `securities` | Master instrument registry covering crypto, equities, and DEX tokens | symbol, name, security_type, asset_class, exchange_id, chain, contract_address, is_dex | 330 | -- |
| `exchanges` | Venue definitions (CEX and DEX) with capability flags | code, name, exchange_type, chain, fee_model | 5 | -- |

### Trading

| Table | Purpose | Key Columns | Rows | Latest Data |
|-------|---------|-------------|------|-------------|
| `trades` | All executed trades (paper and live) with cost tracking | portfolio_id, symbol, side, quantity, price, amount_usd, fee, strategy_id, tx_hash | 100 | 2026-03-29 |
| `positions` | Current open/closed positions with stop-loss and take-profit levels | portfolio_id, symbol, quantity, entry_price, status, stop_loss_price, trailing_stop_price | 9 | 2026-03-27 |
| `trade_outcomes` | Closed trade P&L with signal attribution | symbol, entry/exit_date, entry/exit_price, outcome_pct, outcome_usd, signal_type, market_regime | 32 | 2026-03-24 |
| `cost_ledger` | Per-trade cost breakdown (fees, slippage, gas) | trade_id, portfolio_id, symbol, cost_type, amount_usd | 78 | 2026-03-21 |

### Strategy

| Table | Purpose | Key Columns | Rows | Latest Data |
|-------|---------|-------------|------|-------------|
| `strategy_registry` | Active strategy definitions and parameters | name, class_name, strategy_type, default_params_json, is_active | 13 | 2026-03-18 |
| `strategy_signals_log` | All signals emitted by strategies with execution/skip tracking | strategy_name, symbol, timeframe, signal_time, action, strength, reason, was_executed, skip_reason, outcome_pct | 302 | ongoing |
| `strategy_performance` | Per-strategy performance metrics (backtest and live) | strategy_name, symbol, timeframe, source, sharpe_ratio, max_drawdown, win_rate | 166 | -- |
| `strategy_catalogue` | Full backtest results archive with all risk metrics | strategy_name, symbol, timeframe, total_return, sharpe_ratio, sortino_ratio, max_drawdown, parameters_json | 162 | -- |
| `strategy_swaps` | Log of regime-triggered strategy rotations | portfolio_id, symbol, old_strategy, new_strategy, reason, regime | 12 | -- |

### Risk

| Table | Purpose | Key Columns | Rows | Latest Data |
|-------|---------|-------------|------|-------------|
| `risk_events` | Triggered risk rules (stop-loss, exposure breach, etc.) | portfolio_id, symbol, event_type, action_taken, reason | 0 | -- |
| `market_regime` | Daily macro regime classification (VIX + cross-asset correlation) | date, vix_value, regime, btc_sp500_corr, btc_nasdaq_corr | 20 | 2026-03-29 |
| `market_regime_detailed` | Extended regime data with sector momentum | timestamp, vix_value, regime, crypto/equity_sector_momentum | 0 | -- |
| `correlations` | Rolling pairwise asset correlations | date, symbol_a, symbol_b, window, correlation | 180 | 2026-03-29 |

### Portfolio

| Table | Purpose | Key Columns | Rows | Latest Data |
|-------|---------|-------------|------|-------------|
| `portfolios` | Portfolio definitions with symbol lists and strategy routing | name, initial_capital, mode, symbols_json, strategy_routes_json, asset_class | 4 | -- |
| `portfolio_strategies` | Strategy-to-portfolio allocation mapping | portfolio_id, strategy_id, allocation_pct, is_active | 7 | 2026-03-19 |
| `portfolio_templates` | Predefined portfolio allocation templates | name, strategy_allocations_json, expected_sharpe | 2 | -- |
| `paper_snapshots` | Daily NAV snapshots for paper portfolios | date, portfolio_id, portfolio_value, cash, positions_json | 32 | 2026-03-29 |
| `portfolio_valuations` | Point-in-time portfolio valuations with cost basis | portfolio_id, total_nav_usd, total_cost_basis_usd, unrealized/realized_pnl | 6 | 2026-03-15 |
| `portfolio_performance` | Aggregated performance metrics (Sharpe, drawdown, volatility) | portfolio_id, total_value, daily_pnl, sharpe_30d, max_drawdown_30d | 1 | 2026-03-11 |
| `portfolio_analysis` | Per-symbol multi-timeframe analysis with support/resistance | symbol, timeframe, short/medium/long_term_signal, action, confidence, reasoning | 472 | 2026-03-29 |
| `accounts` | Venue accounts linked to portfolios | portfolio_id, venue_id, account_name, account_type, status | 4 | -- |

### Dimension

| Table | Purpose | Key Columns | Rows | Latest Data |
|-------|---------|-------------|------|-------------|
| `traders` | Agent/trader identity registry | code, name, trader_type | 6 | -- |
| `trader_wallets` | Wallet/API credentials per trader per exchange | trader_id, exchange_id, wallet_type, wallet_ref | 6 | -- |
| `trader_portfolio_access` | RBAC: which traders can execute on which portfolios | trader_id, portfolio_id, role | 12 | -- |

### Intelligence

| Table | Purpose | Key Columns | Rows | Latest Data |
|-------|---------|-------------|------|-------------|
| `sentiment_scores` | Aggregated sentiment per symbol from news/social sources | symbol, timestamp, score, confidence, summary, headline_count | 139 | 2026-03-29 |
| `news_sentiment_stream` | Raw news headlines with per-article sentiment | symbol, headline, summary, sentiment_score, source, news_time | 0 | -- |
| `market_memory` | Persistent market observations and learned patterns (with embeddings) | date, category, content, embedding, metadata | 112 | 2026-03-29 |

### DEX

| Table | Purpose | Key Columns | Rows | Latest Data |
|-------|---------|-------------|------|-------------|
| `dex_tokens` | On-chain token metadata (contract, liquidity, holder count) | security_id, chain, contract_address, liquidity, volume_24h, market_cap | 4 | 2026-03-15 |
| `dex_transactions` | On-chain swap transaction log | trade_id, tx_hash, chain, action, from_token, to_token, slippage_actual, gas_used, bankr_job_id | 0 | -- |

### Tax / Accounting

| Table | Purpose | Key Columns | Rows | Latest Data |
|-------|---------|-------------|------|-------------|
| `tax_lots` | FIFO/LIFO tax lot tracking for cost basis | portfolio_id, symbol, buy_trade_id, remaining_quantity, cost_basis_per_unit, status | 40 | 2026-03-20 |
| `lot_dispositions` | Tax lot disposition records with wash sale detection | tax_lot_id, sell_trade_id, realized_gain_usd, holding_period_days, term, is_wash_sale | 41 | 2026-03-19 |
| `transfers` | Inter-account/chain transfers | from_account_id, to_account_id, symbol, quantity, transfer_type, tx_hash | 0 | -- |

### System

| Table | Purpose | Key Columns | Rows | Latest Data |
|-------|---------|-------------|------|-------------|
| `collection_log` | Data pipeline health: last collection time per symbol/timeframe | symbol, timeframe, last_timestamp, data_points, status, error_message | 54 | 2026-03-29 |
| `system_metrics` | Generic metrics store for pipeline monitoring | metric_name, metric_value, labels, timestamp | 0 | -- |

### Vector (sqlite-vec)

| Table | Purpose | Rows |
|-------|---------|------|
| `vec_market_memory*` | Vector index for market_memory embeddings (5 shadow tables) | -- |
| `vec_trade_outcomes_vec*` | Vector index for trade outcome embeddings (5 shadow tables) | -- |

### Legacy

| Table | Purpose | Rows |
|-------|---------|------|
| `exchanges_old` | Pre-migration exchange definitions | 4 |
| `paper_trades_legacy` | Pre-refactor paper trade log | 21 |

---

## Table Classification

| Category | Tables |
|----------|--------|
| **Core Market Data** | candlesticks, indicators, ticks, securities, exchanges |
| **Trading** | trades, positions, trade_outcomes, cost_ledger |
| **Strategy** | strategy_registry, strategy_signals_log, strategy_performance, strategy_catalogue, strategy_swaps |
| **Risk** | risk_events, market_regime, market_regime_detailed, correlations |
| **Portfolio** | portfolios, portfolio_strategies, portfolio_templates, paper_snapshots, portfolio_valuations, portfolio_performance, portfolio_analysis, accounts |
| **Dimension** | traders, trader_wallets, trader_portfolio_access |
| **Intelligence** | sentiment_scores, news_sentiment_stream, market_memory |
| **DEX** | dex_tokens, dex_transactions |
| **Tax/Accounting** | tax_lots, lot_dispositions, transfers |
| **System** | collection_log, system_metrics, sqlite_sequence, sqlite_stat1 |
| **Vector** | vec_market_memory*, vec_trade_outcomes_vec* |
| **Legacy** | exchanges_old, paper_trades_legacy |

---

## Key Queries

### Signal to Outcome Pairs (LoRA training data)

```sql
-- Join signals with their trade outcomes for supervised learning
SELECT
    s.strategy_name,
    s.symbol,
    s.timeframe,
    s.signal_time,
    s.action,
    s.strength,
    s.reason,
    s.market_regime,
    s.adx,
    s.rsi,
    s.was_executed,
    s.skip_reason,
    s.outcome_pct,
    t.outcome_usd,
    t.holding_hours,
    t.exit_reason
FROM strategy_signals_log s
LEFT JOIN trade_outcomes t
    ON s.symbol = t.symbol
    AND date(s.signal_time) = date(t.entry_date)
WHERE s.was_executed = 1
ORDER BY s.signal_time;
```

### Trade Reasoning Examples (LoRA training data)

```sql
-- Extract trades with full decision context for reasoning fine-tuning
SELECT
    t.symbol,
    t.side,
    t.quantity,
    t.price,
    t.amount_usd,
    t.decision_context,
    t.created_at,
    p.name AS portfolio_name,
    p.mode,
    o.outcome_pct,
    o.outcome_usd,
    o.exit_reason,
    o.market_regime
FROM trades t
JOIN portfolios p ON t.portfolio_id = p.id
LEFT JOIN trade_outcomes o ON t.id = o.trade_id
WHERE t.decision_context IS NOT NULL
ORDER BY t.created_at;
```

### Strategy Performance Comparison

```sql
-- Compare all strategies by risk-adjusted return
SELECT
    strategy_name,
    symbol,
    timeframe,
    source,
    total_return,
    sharpe_ratio,
    sortino_ratio,
    max_drawdown,
    win_rate,
    profit_factor,
    total_trades
FROM strategy_performance
ORDER BY sharpe_ratio DESC;
```

### Current Portfolio State

```sql
-- Full portfolio snapshot: positions + cash + NAV
SELECT
    p.name AS portfolio,
    p.mode,
    ps.portfolio_value AS nav,
    ps.cash,
    ps.num_positions,
    pos.symbol,
    pos.quantity,
    pos.entry_price,
    pos.current_price,
    pos.pnl,
    pos.pnl_percent,
    pos.stop_loss_price,
    pos.status
FROM portfolios p
LEFT JOIN paper_snapshots ps ON p.id = ps.portfolio_id
    AND ps.date = (SELECT MAX(date) FROM paper_snapshots WHERE portfolio_id = p.id)
LEFT JOIN positions pos ON p.id = pos.portfolio_id AND pos.status = 'open'
WHERE p.is_active = 1
ORDER BY p.name, pos.symbol;
```

### Daily NAV History

```sql
-- Time series of portfolio NAV for equity curve / drawdown analysis
SELECT
    ps.date,
    p.name AS portfolio,
    ps.portfolio_value,
    ps.cash,
    ps.num_positions,
    LAG(ps.portfolio_value) OVER (PARTITION BY ps.portfolio_id ORDER BY ps.date) AS prev_nav,
    ROUND(
        (ps.portfolio_value - LAG(ps.portfolio_value) OVER (PARTITION BY ps.portfolio_id ORDER BY ps.date))
        / LAG(ps.portfolio_value) OVER (PARTITION BY ps.portfolio_id ORDER BY ps.date) * 100,
        2
    ) AS daily_return_pct
FROM paper_snapshots ps
JOIN portfolios p ON ps.portfolio_id = p.id
ORDER BY ps.portfolio_id, ps.date;
```

---

## Data Freshness Expectations

| Table | Update Frequency | Source Module | Staleness Threshold |
|-------|-----------------|---------------|---------------------|
| candlesticks | Every 5 min (intraday), hourly (1h/4h), daily (1d) | crypto_data_collector, dex_data_collector, equity_data_collector, intraday_update | 15 min (5m), 2 h (1h), 8 h (4h), 26 h (1d) |
| indicators | Follows candlestick updates | compute_all_indicators | Same as candlesticks |
| ticks | Real-time when streaming enabled | (tick collector) | 5 min |
| trades | On execution | dex_executor, paper_trading | Event-driven |
| positions | On trade execution | dex_executor, paper_trading | Event-driven |
| trade_outcomes | On position close | trade_journal | Event-driven |
| strategy_signals_log | On signal emission | signal_trading, strategy_tracker | Event-driven |
| strategy_performance | After backtest or live period close | strategy_tracker | 24 h |
| strategy_catalogue | After backtest run | backtest/catalogue | On-demand |
| strategy_registry | On strategy deployment | backtest/deployer | On-demand |
| paper_snapshots | Daily (end of day) | paper_trading | 26 h |
| portfolio_analysis | Daily | (analysis pipeline) | 26 h |
| sentiment_scores | Every 6 h | sentiment | 12 h |
| market_regime | Daily | correlation_tracker, regime_monitor | 26 h |
| correlations | Daily | correlation_tracker | 26 h |
| market_memory | Daily | (market memory writer) | 26 h |
| collection_log | On each collection run | crypto_data_collector | Self-monitoring |
| dex_tokens | On discovery/refresh | dex_data_collector | 24 h |
| dex_transactions | On DEX execution | dex_executor | Event-driven |
| securities | On symbol addition | (migration / collector) | Static |
| portfolios | On portfolio creation/edit | backtest/deployer, bootstrap_db | Static |

---

## Table Readers/Writers

| Table | Writers | Readers |
|-------|---------|---------|
| `candlesticks` | crypto_data_collector, dex_data_collector, equity_data_collector, historical_backfill, intraday_update | backtest/engine, cli, compute_all_indicators, regime_monitor |
| `indicators` | compute_all_indicators, crypto_data_collector, equity_data_collector, historical_backfill, intraday_update | advanced_scorer, cli, signal_trading |
| `trades` | dex_executor, paper_trading | cli, report_engine, trade_journal |
| `positions` | dex_executor, paper_trading | cli, dex_risk_rules, dex_trading_agent, trading_agent |
| `trade_outcomes` | trade_journal | cli, report_engine |
| `strategy_registry` | backtest/deployer | signal_trading |
| `strategy_performance` | strategy_tracker | strategy_tracker |
| `strategy_signals_log` | signal_trading, strategy_tracker | cli, report_engine, scripts/verify_signal_flow |
| `strategy_catalogue` | backtest/catalogue | regime_monitor |
| `portfolios` | backtest/deployer, bootstrap_db | cli, config, regime_monitor |
| `accounts` | migration only | config |
| `paper_snapshots` | paper_trading | cli, report_engine |
| `sentiment_scores` | sentiment | report_engine, signal_trading, trading_agent |
| `correlations` | correlation_tracker | report_engine |
| `market_regime` | correlation_tracker, regime_monitor | report_engine |
| `dex_tokens` | dex_data_collector | (migration only) |
| `dex_transactions` | dex_executor | cli |
| `collection_log` | crypto_data_collector | crypto_data_collector |

---

## Data Extraction Interface

The Mac Mini training pipeline pulls data from the laptop (primary host) using the following methods.

### SCP (full database snapshot)

```bash
# Pull entire database for local querying on Mac Mini
scp satyamini@<laptop-ip>:~/.openclaw/workspace/projects/edoras/crypto_data.db /data/edoras/
```

### CSV Export (table-level)

```bash
# Export specific tables as CSV for pandas/polars ingestion
sqlite3 -header -csv crypto_data.db \
  "SELECT * FROM strategy_signals_log WHERE was_executed = 1;" \
  > signals_executed.csv

sqlite3 -header -csv crypto_data.db \
  "SELECT * FROM trade_outcomes;" \
  > trade_outcomes.csv
```

### JSONL Export (for LoRA fine-tuning)

```bash
# Export signal-outcome pairs as JSONL for training data
sqlite3 -json crypto_data.db "
  SELECT s.*, t.outcome_usd, t.holding_hours, t.exit_reason
  FROM strategy_signals_log s
  LEFT JOIN trade_outcomes t ON s.symbol = t.symbol AND date(s.signal_time) = date(t.entry_date)
  WHERE s.was_executed = 1;
" | python3 -c "
import sys, json
for row in json.load(sys.stdin):
    print(json.dumps(row))
" > training_signals.jsonl
```

### Incremental Sync

For ongoing training, export only new data since last pull:

```bash
# Track last export timestamp in a local file on Mac Mini
LAST_TS=$(cat /data/edoras/.last_sync 2>/dev/null || echo "2000-01-01")

sqlite3 -json crypto_data.db "
  SELECT * FROM trade_outcomes WHERE created_at > '$LAST_TS';
" | python3 -c "
import sys, json
for row in json.load(sys.stdin):
    print(json.dumps(row))
" >> training_outcomes.jsonl

date -u +%Y-%m-%dT%H:%M:%S > /data/edoras/.last_sync
```

### Notes

- The database is SQLite; concurrent reads are safe but only one writer at a time.
- Vector tables (`vec_*`) use the `sqlite-vec` extension and require it loaded for queries.
- Timestamps in `candlesticks`, `indicators`, and `sentiment_scores` are Unix epoch integers. All other tables use ISO 8601 strings.
- The database file is ~500 MB+; prefer table-level CSV/JSONL exports over full SCP when bandwidth is limited.
