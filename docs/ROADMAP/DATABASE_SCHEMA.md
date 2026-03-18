# Database Schema – Source of Truth

*Version: 2026‑03‑16 · Real‑Time Wealth Management System*
*Location: `crypto_data.db` (SQLite)*

---

## **Overview**

This database powers the real‑time trading system, tracking market data, positions, trades, risk events, and portfolio performance. It replaces JSON‑based state with a relational model, enabling sub‑second risk checks and audit trails.

**Design Principles:**
- **Single source of truth** – all trading state resides here
- **Real‑time ready** – tick‑level data, frequent updates
- **Audit trail** – every trade and risk event logged
- **Backward compatible** – legacy systems can read via views/adapters
- **Scalable** – ready for TimescaleDB migration when needed
- **Star schema** – portfolios as strategy containers, accounts bridge to venues (M:M)

---

## **Schema Diagram**

```
                    ┌──────────────────┐
                    │   portfolios     │
                    ├──────────────────┤
                    │ • id (PK)        │
                    │ • name           │
                    │ • initial_capital │
                    │ • is_active      │
                    └───────┬──────────┘
                            │
              ┌─────────────┼─────────────────┐
              │             │                 │
              ▼             ▼                 ▼
┌──────────────────┐ ┌──────────────────┐ ┌──────────────────┐
│    accounts      │ │portfolio_strategies│ │portfolio_valuations│
├──────────────────┤ ├──────────────────┤ ├──────────────────┤
│ • id (PK)        │ │ • portfolio_id   │ │ • id (PK)        │
│ • portfolio_id   │ │ • strategy_id    │ │ • portfolio_id   │
│ • venue_id (FK)  │ │ • allocation_pct │ │ • snapshot_time  │
│ • account_ref    │ │ • is_active      │ │ • total_value    │
│ • status         │ └──────────────────┘ │ • cash/invested  │
└────────┬─────────┘                      └──────────────────┘
         │
         ▼
┌──────────────────┐      ┌──────────────────┐
│    exchanges     │      │strategy_registry │
├──────────────────┤      ├──────────────────┤
│ • id (PK)        │      │ • id (PK)        │
│ • code           │      │ • name           │
│ • name           │      │ • class_name     │
│ • chain/chain_id │      │ • strategy_type  │
│ • fee_model      │      │ • parameters     │
│ • settlement_type│      └──────────────────┘
└──────────────────┘

┌──────────────────┐      ┌──────────────────┐      ┌──────────────────┐
│   positions      │      │     trades       │      │   securities     │
├──────────────────┤      ├──────────────────┤      ├──────────────────┤
│ • id (PK)        │      │ • id (PK)        │      │ • id (PK)        │
│ • portfolio_id   │      │ • portfolio_id   │      │ • symbol         │
│ • account_id (FK)│      │ • account_id (FK)│      │ • canonical_id   │
│ • symbol         │      │ • strategy_id(FK)│      │ • decimals       │
│ • quantity       │      │ • symbol/side    │      │ • indicator_profile│
│ • entry_price    │      │ • quantity/price  │      │ • settlement_type│
│ • status         │      │ • tx_hash (DEX)  │      └──────────────────┘
└──────────────────┘      │ • gas_used (DEX) │
                          │ • slippage_bps   │      ┌──────────────────┐
                          └──────────────────┘      │   transfers      │
                                                    ├──────────────────┤
┌──────────────────┐      ┌──────────────────┐      │ • from_account_id│
│   candlesticks   │      │    indicators    │      │ • to_account_id  │
├──────────────────┤      ├──────────────────┤      │ • amount/currency│
│ • symbol         │      │ • 17 standard    │      │ • transfer_type  │
│ • timeframe      │      │ • 16 binary      │      └──────────────────┘
│ • OHLCV          │      │ (prediction mkts)│
└──────────────────┘      └──────────────────┘

┌──────────────────┐      ┌──────────────────┐      ┌──────────────────┐
│  risk_events     │      │  market_regime   │      │  correlations    │
│  ticks           │      │  market_regime_  │      │  news_sentiment  │
│  system_metrics  │      │    detailed      │      │  collection_log  │
└──────────────────┘      └──────────────────┘      └──────────────────┘
```

---

## **Core Tables**

### **1. `portfolios` – Portfolio Master**
```sql
CREATE TABLE portfolios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL DEFAULT 'Main Portfolio',
    description TEXT,
    initial_capital REAL NOT NULL DEFAULT 1000.0,
    currency TEXT NOT NULL DEFAULT 'USD',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_active BOOLEAN DEFAULT 1
);
```

**Purpose:** Tracks trading portfolios (supports multiple).  
**Default:** ID 1 = "Main Paper Portfolio" with $1,000 initial capital.  
**Relations:** `positions.portfolio_id`, `trades.portfolio_id`, `portfolio_performance.portfolio_id`  
**Indexes:** Primary key only.  
**Triggers:** `update_portfolio_timestamp` updates `updated_at` on change.

---

### **2. `positions` – Open/Closed Positions**
```sql
CREATE TABLE positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    portfolio_id INTEGER NOT NULL,
    account_id INTEGER,                -- FK → accounts.id (Phase 1, dual-write Phase 2)
    symbol TEXT NOT NULL,
    quantity REAL NOT NULL,
    entry_price REAL NOT NULL,
    entry_time TIMESTAMP NOT NULL,
    current_price REAL,
    stop_loss_price REAL,
    trailing_stop_price REAL,
    take_profit_levels TEXT,  -- JSON: {"0.15": false, "0.20": false, "0.25": false}
    status TEXT CHECK(status IN ('open', 'closed', 'partial')) DEFAULT 'open',
    pnl REAL DEFAULT 0.0,
    pnl_percent REAL DEFAULT 0.0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (portfolio_id) REFERENCES portfolios(id),
    FOREIGN KEY (account_id) REFERENCES accounts(id)
);
```

**Purpose:** Trades currently held (open) or historically held (closed).
**Constraints:**
- `status` ∈ {open, closed, partial}
- Unique constraint on `(portfolio_id, symbol, status)` prevents duplicate open positions
- Foreign keys to `portfolios` and `accounts`

**Indexes:**
- `idx_positions_portfolio_symbol` – UNIQUE(portfolio_id, symbol, status)

**Triggers:** `update_position_timestamp` updates `updated_at` on change.

**Fields:**
- `account_id`: Links position to the venue account (populated by dual-write in Phase 2)
- `take_profit_levels`: JSON object mapping profit level (0.15 = 15%) to boolean triggered flag
- `stop_loss_price`: Hard stop price (10% below entry by default)
- `trailing_stop_price`: Dynamic stop that trails upward after 5% gain
- `current_price`: Updated in real‑time via WebSocket ticks
- `pnl`/`pnl_percent`: Calculated on exit

---

### **3. `trades` – Trade Execution Log**
```sql
CREATE TABLE trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    portfolio_id INTEGER NOT NULL,
    account_id INTEGER,                -- FK → accounts.id (Phase 1, dual-write Phase 2)
    strategy_id INTEGER,               -- FK → strategy_registry.id (Phase 1)
    symbol TEXT NOT NULL,
    side TEXT CHECK(side IN ('BUY', 'SELL')) NOT NULL,
    quantity REAL NOT NULL,
    price REAL NOT NULL,
    amount_usd REAL NOT NULL,
    fee REAL DEFAULT 0.0,
    order_type TEXT DEFAULT 'market',
    status TEXT CHECK(status IN ('filled', 'partial', 'cancelled', 'rejected')) DEFAULT 'filled',
    decision_context TEXT,  -- JSON: LLM reasoning, signals, risk checks
    related_position_id INTEGER,
    risk_event_type TEXT,   -- 'stop_loss', 'trailing_stop', 'take_profit', 'circuit_breaker', 'manual'
    parent_trade_id INTEGER,  -- For take‑profit partial sells
    -- DEX-specific columns (Phase 1)
    tx_hash TEXT,              -- On-chain transaction hash
    block_number INTEGER,      -- Block number of transaction
    gas_used REAL,             -- Gas consumed
    gas_price_gwei REAL,       -- Gas price in Gwei
    slippage_bps REAL,         -- Actual slippage in basis points
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (portfolio_id) REFERENCES portfolios(id),
    FOREIGN KEY (related_position_id) REFERENCES positions(id),
    FOREIGN KEY (account_id) REFERENCES accounts(id),
    FOREIGN KEY (strategy_id) REFERENCES strategy_registry(id)
);
```

**Purpose:** Immutable record of every trade execution (paper, live CEX, or live DEX).
**Constraints:**
- `side` ∈ {BUY, SELL}
- `status` ∈ {filled, partial, cancelled, rejected}
- Foreign keys to `portfolios`, `positions`, `accounts`, `strategy_registry`

**Indexes:**
- `idx_trades_symbol_time` – (symbol, created_at)
- `idx_trades_portfolio_time` – (portfolio_id, created_at)

**Fields:**
- `account_id`: Links trade to the venue account (populated by dual-write in Phase 2)
- `strategy_id`: Which strategy generated this trade signal
- `decision_context`: JSON with LLM reasoning, signal scores, risk‑check results
- `risk_event_type`: Why this trade executed (risk rule or manual)
- `parent_trade_id`: Links partial sells to original position for audit trail
- `related_position_id`: Which position this trade affected
- `tx_hash`/`block_number`/`gas_used`/`gas_price_gwei`: DEX on-chain transaction details (NULL for CEX)
- `slippage_bps`: Actual vs expected price slippage in basis points

---

### **4. `risk_events` – Risk Rule Triggers**
```sql
CREATE TABLE risk_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    portfolio_id INTEGER NOT NULL,
    symbol TEXT,
    event_type TEXT NOT NULL,  -- 'stop_loss', 'trailing_stop', 'take_profit', 'circuit_breaker', 'position_limit', 'sector_limit'
    trigger_price REAL,
    current_price REAL,
    quantity REAL,
    action_taken TEXT,  -- 'full_exit', 'partial_exit', 'alert_only'
    reason TEXT,
    metadata TEXT,  -- JSON with additional context
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (portfolio_id) REFERENCES portfolios(id)
);
```

**Purpose:** Log every risk‑rule evaluation, whether it triggered action or not.  
**Indexes:**
- `idx_risk_events_time` – (created_at)
- `idx_risk_events_symbol_time` – (symbol, created_at)

**Use case:** Forensic analysis of risk decisions, monitoring false positives.

---

### **5. `portfolio_performance` – Portfolio Snapshots**
```sql
CREATE TABLE portfolio_performance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    portfolio_id INTEGER NOT NULL,
    snapshot_time TIMESTAMP NOT NULL,
    total_value REAL NOT NULL,
    cash REAL NOT NULL,
    invested REAL NOT NULL,
    daily_pnl REAL,
    daily_return REAL,
    sharpe_30d REAL,
    max_drawdown_30d REAL,
    volatility_30d REAL,
    positions_count INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (portfolio_id) REFERENCES portfolios(id),
    UNIQUE(portfolio_id, snapshot_time)
);
```

**Purpose:** Time‑series of portfolio metrics (hourly/daily snapshots).  
**Constraints:** Unique on `(portfolio_id, snapshot_time)` – one snapshot per time point.

**Fields:**
- `snapshot_time`: When metrics were calculated
- `total_value` = `cash` + `invested`
- `invested`: Sum of `position.quantity * position.current_price` for open positions
- `daily_pnl`/`daily_return`: Since previous snapshot
- Performance metrics calculated over trailing windows

---

### **6. `ticks` – Real‑Time Market Data**
```sql
CREATE TABLE ticks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    price REAL NOT NULL,
    volume REAL,
    exchange TEXT DEFAULT 'coinbase',
    bid REAL,
    ask REAL,
    timestamp TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**Purpose:** Raw WebSocket tick data (sub‑second granularity).  
**Indexes:** `idx_ticks_symbol_time` – (symbol, timestamp)

**Use case:**
- Real‑time risk evaluation
- Candle aggregation (1s, 1m, 5m, 1h, 4h, 1d)
- Latency monitoring
- Market microstructure analysis

**Volume:** ~10k ticks/day per symbol at peak activity.

---

### **7. `candlesticks` – OHLCV Aggregations**
```sql
CREATE TABLE candlesticks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,  -- '1s', '1m', '5m', '1h', '4h', '1d'
    timestamp INTEGER NOT NULL,  -- Unix timestamp, start of candle
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**Purpose:** Candles aggregated from ticks, used by indicators and signals.  
**Timeframes:** 1s, 1m, 5m, 1h, 4h, 1d (1d also from daily batch).  
**Indexes:** Composite index on `(symbol, timeframe, timestamp)`.

**Note:** Real‑time system updates 1m candles from ticks; other timeframes aggregated from 1m.

---

### **8. `indicators` – Technical Indicators**
```sql
CREATE TABLE indicators (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    timestamp INTEGER NOT NULL,
    sma_20 REAL,
    sma_50 REAL,
    sma_200 REAL,
    ema_12 REAL,
    ema_26 REAL,
    rsi_14 REAL,
    macd_line REAL,
    macd_signal REAL,
    macd_histogram REAL,
    bb_upper REAL,
    bb_middle REAL,
    bb_lower REAL,
    bb_width REAL,
    atr_14 REAL,
    volume_sma_20 REAL,
    volume_ratio REAL,
    adx_14 REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**Purpose:** Pre‑calculated technical indicators for all symbols/timeframes.  
**Indicators:** 17 total – SMA, EMA, RSI, MACD, Bollinger Bands, ATR, Volume SMA, ADX.  
**Indexes:** Composite on `(symbol, timeframe, timestamp)`.

**Calculation:** Batch job (daily) for 1d timeframe, real‑time for 1m/5m/1h.

---

## **Market Intelligence Tables**

### **9. `market_regime` – Daily Regime Classification**
```sql
CREATE TABLE market_regime (
    date TEXT PRIMARY KEY,
    vix_value REAL,
    regime TEXT,  -- 'risk_on', 'risk_off', 'neutral', 'high_volatility'
    btc_sp500_corr REAL,
    btc_nasdaq_corr REAL
);
```

**Purpose:** Daily market regime based on VIX and correlations.  
**Source:** `correlation_tracker.py` batch job.

---

### **10. `market_regime_detailed` – Granular Regime Tracking**
```sql
CREATE TABLE market_regime_detailed (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TIMESTAMP NOT NULL,
    vix_value REAL,
    regime TEXT,
    btc_spy_corr REAL,
    btc_qqq_corr REAL,
    spy_qqq_corr REAL,
    crypto_sector_momentum REAL,
    equity_sector_momentum REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(timestamp)
);
```

**Purpose:** Higher‑frequency regime tracking (hourly).  
**Use case:** Real‑time signal adjustment based on current regime.

---

### **11. `correlations` – Cross‑Asset Correlations**
```sql
CREATE TABLE correlations (
    date TEXT NOT NULL,
    symbol_a TEXT NOT NULL,
    symbol_b TEXT NOT NULL,
    window INTEGER NOT NULL,  -- days
    correlation REAL NOT NULL,
    PRIMARY KEY (date, symbol_a, symbol_b, window)
);
```

**Purpose:** Rolling correlations between assets (BTC‑SPY, BTC‑QQQ, etc.).  
**Window:** Typically 30, 60, 90 days.

---

### **12. `news_sentiment_stream` – Real‑Time News Sentiment**
```sql
CREATE TABLE news_sentiment_stream (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    headline TEXT,
    summary TEXT,
    sentiment_score REAL,  -- -1 (bearish) to +1 (bullish)
    confidence REAL,
    source TEXT,
    news_time TIMESTAMP,
    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**Purpose:** News articles with GPT‑generated sentiment scores.  
**Indexes:** `idx_news_symbol_time` – (symbol, news_time)  
**Pipeline:** RSS → GPT‑4o‑mini → sentiment → database (< 30s latency).

---

## **System Tables**

### **13. `system_metrics` – Monitoring & Observability**
```sql
CREATE TABLE system_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    metric_name TEXT NOT NULL,
    metric_value REAL,
    labels TEXT,  -- JSON: {"component": "websocket", "symbol": "BTC-USD"}
    timestamp TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**Purpose:** System health metrics (latency, queue depth, error rates).  
**Indexes:** `idx_metrics_name_time` – (metric_name, timestamp)  
**Metrics:** 
- `websocket_latency_ms`
- `risk_check_duration_ms` 
- `tick_queue_depth`
- `database_connection_count`
- `memory_usage_mb`

---

### **14. `collection_log` – Data Collection Audit**
```sql
CREATE TABLE collection_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    last_timestamp INTEGER,
    data_points INTEGER DEFAULT 0,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status TEXT DEFAULT 'active',
    error_message TEXT
);
```

**Purpose:** Tracks data collection jobs (success/failure, timestamps).  
**Use case:** Gap detection, monitoring batch job health.

---

## **Warehouse Tables (Phase 1/2 – 2026‑03‑15)**

### **17. `accounts` – Portfolio‑Venue Bridge (M:M)**
```sql
CREATE TABLE accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    portfolio_id INTEGER NOT NULL,
    venue_id INTEGER NOT NULL,           -- FK → exchanges.id
    account_ref TEXT,                     -- External ID (API key label, wallet address)
    account_type TEXT DEFAULT 'trading',  -- 'trading', 'custody', 'staking'
    status TEXT DEFAULT 'active',         -- 'active', 'suspended', 'closed'
    opened_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    closed_at TIMESTAMP,
    metadata TEXT,                        -- JSON: chain info, permissions, etc.
    FOREIGN KEY (portfolio_id) REFERENCES portfolios(id),
    FOREIGN KEY (venue_id) REFERENCES exchanges(id),
    UNIQUE(portfolio_id, venue_id, account_ref)
);
```

**Purpose:** Bridges portfolios to venues. A portfolio can trade on multiple venues; a venue can serve multiple portfolios.
**Seeded data:** 4 accounts — Galadriel→Coinbase, Galadriel→yfinance, Arwen→Bankr, Thranduil→Coinbase.
**Key relationship:** `trades.account_id` and `positions.account_id` link to this table.
**Resolver:** `config.resolve_account_id(portfolio_id, venue_code=)` maps portfolio to account with in-memory cache.

---

### **18. `portfolio_strategies` – Strategy Assignment (M:M)**
```sql
CREATE TABLE portfolio_strategies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    portfolio_id INTEGER NOT NULL,
    strategy_id INTEGER NOT NULL,
    allocation_pct REAL DEFAULT 100.0,
    is_active BOOLEAN DEFAULT 1,
    assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (portfolio_id) REFERENCES portfolios(id),
    FOREIGN KEY (strategy_id) REFERENCES strategy_registry(id),
    UNIQUE(portfolio_id, strategy_id)
);
```

**Purpose:** Assigns strategies to portfolios with allocation weights.
**Seeded data:** 3 links for Galadriel (BollingerReversion, MultiSignal, ScoreBasedRelaxed).

---

### **19. `transfers` – Capital Movements**
```sql
CREATE TABLE transfers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_account_id INTEGER,             -- NULL for external deposit
    to_account_id INTEGER,               -- NULL for external withdrawal
    amount REAL NOT NULL,
    currency TEXT NOT NULL DEFAULT 'USD',
    transfer_type TEXT NOT NULL,          -- 'deposit', 'withdrawal', 'bridge', 'internal'
    status TEXT DEFAULT 'completed',      -- 'pending', 'completed', 'failed'
    tx_hash TEXT,                         -- On-chain hash for bridge/DEX transfers
    initiated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    metadata TEXT,                        -- JSON: chain, gas, notes
    FOREIGN KEY (from_account_id) REFERENCES accounts(id),
    FOREIGN KEY (to_account_id) REFERENCES accounts(id)
);
```

**Purpose:** Tracks capital flows between accounts (deposits, withdrawals, cross-chain bridges).
**Status:** Schema created, no data yet.

---

### **20. `portfolio_valuations` – NAV Time Series**
```sql
CREATE TABLE portfolio_valuations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    portfolio_id INTEGER NOT NULL,
    snapshot_time TIMESTAMP NOT NULL,
    total_value REAL NOT NULL,
    cash REAL NOT NULL,
    invested REAL NOT NULL,
    num_positions INTEGER DEFAULT 0,
    source TEXT DEFAULT 'paper_snapshot',  -- 'paper_snapshot', 'api_sync', 'manual'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (portfolio_id) REFERENCES portfolios(id),
    UNIQUE(portfolio_id, snapshot_time)
);
```

**Purpose:** Portfolio NAV over time. Supplements `portfolio_performance` and `paper_snapshots`.
**Seeded data:** 6 valuations backfilled from `paper_snapshots`.

---

### **21. `dex_tokens` – DEX Token Metadata**
```sql
CREATE TABLE dex_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    name TEXT,
    chain TEXT NOT NULL,              -- 'base', 'ethereum'
    contract_address TEXT NOT NULL,
    pool_address TEXT,
    gecko_pool_id TEXT,               -- GeckoTerminal pool identifier
    decimals INTEGER DEFAULT 18,
    is_active BOOLEAN DEFAULT 1,
    min_liquidity_usd REAL DEFAULT 100000,
    max_slippage_pct REAL DEFAULT 5.0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(chain, contract_address)
);
```

**Purpose:** Metadata for DEX tokens tracked via GeckoTerminal.
**Use case:** DEX data collection, risk rule lookups.

---

### **22. `dex_transactions` – On‑Chain Transaction Log**
```sql
CREATE TABLE dex_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    portfolio_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity REAL NOT NULL,
    price REAL NOT NULL,
    amount_usd REAL NOT NULL,
    tx_hash TEXT UNIQUE,
    block_number INTEGER,
    gas_used REAL,
    gas_price_gwei REAL,
    slippage_bps REAL,
    status TEXT DEFAULT 'confirmed',
    chain TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (portfolio_id) REFERENCES portfolios(id)
);
```

**Purpose:** Raw on-chain DEX transaction records (Bankr API).
**Note:** Will be merged into `trades` table in Phase 4 of warehouse redesign.

---

## **Dimension Tables (Enhanced in Phase 1)**

### **`exchanges` – Venue Registry**
```sql
CREATE TABLE exchanges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    exchange_type TEXT,           -- 'cex', 'dex', 'data_provider', 'prediction_market'
    api_base_url TEXT,
    has_websocket BOOLEAN DEFAULT 0,
    has_rest BOOLEAN DEFAULT 1,
    live_trading BOOLEAN DEFAULT 0,
    -- Phase 1 additions:
    chain TEXT,                  -- 'ethereum', 'base' (for DEX venues)
    chain_id INTEGER,            -- EVM chain ID (1=ETH, 8453=Base)
    fee_model TEXT,              -- 'maker_taker', 'flat', 'gas_only'
    settlement_type TEXT,        -- 'instant', 'T+1', 'on_chain'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**Seeded data:** coinbase, yfinance, polymarket, kalshi, bankr.

---

### **`securities` – Instrument Registry**
```sql
CREATE TABLE securities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL UNIQUE,
    name TEXT,
    security_type TEXT,          -- 'crypto', 'equity', 'index', 'prediction_binary'
    exchange TEXT,               -- Exchange code
    asset_class TEXT,            -- 'crypto', 'equity', 'prediction'
    sector TEXT,
    settlement_type TEXT,        -- 'continuous', 'binary_expiry', 'scalar_expiry'
    indicator_profile TEXT DEFAULT 'standard',  -- 'standard', 'binary', 'none'
    price_min REAL,
    price_max REAL,
    expiry_date TEXT,
    -- Phase 1 additions:
    canonical_instrument_id INTEGER,  -- Self-FK grouping same asset across venues
    decimals INTEGER,                 -- Token decimals (18 for ERC-20)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (canonical_instrument_id) REFERENCES securities(id)
);
```

**Purpose:** Master list of all tradeable instruments across venues.
**Seeded data:** 52+ securities (18 crypto, 4 DEX, 20+ prediction, 10 equity, 3 index).
**Canonical mapping:** WETH-BASE → ETH-USD (same underlying asset).

---

### **`strategy_registry` – Strategy Catalog**
```sql
CREATE TABLE strategy_registry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    class_name TEXT NOT NULL,
    description TEXT,
    supported_security_types TEXT,  -- JSON array
    default_params TEXT,            -- JSON
    is_active BOOLEAN DEFAULT 1,
    -- Phase 1 additions:
    strategy_type TEXT,             -- 'momentum', 'mean_reversion', 'trend', 'hybrid', 'score'
    parameters TEXT,                -- JSON with tuned params
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**Seeded data:** 7 strategies (ScoreBased, MACDCross, RSIMeanReversion, ADXTrend, BollingerReversion, MultiSignal, ScoreBasedRelaxed).

---

### **`trade_outcomes` – Trade Journal**
```sql
CREATE TABLE trade_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    portfolio_id INTEGER DEFAULT 1,
    symbol TEXT NOT NULL,
    entry_time TEXT NOT NULL,
    exit_time TEXT NOT NULL,
    entry_price REAL NOT NULL,
    exit_price REAL NOT NULL,
    quantity REAL NOT NULL,
    pnl_usd REAL NOT NULL,
    pnl_percent REAL NOT NULL,
    hold_duration_hours REAL,
    entry_signal_type TEXT,
    exit_signal_type TEXT,
    regime_at_entry TEXT,
    regime_at_exit TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**Purpose:** Auto-records when positions close. Feeds back into LLM trading decisions.
**Source:** `paper_trading.py` writes on position close.

---

### **`strategy_performance` – Backtest Results**
```sql
CREATE TABLE strategy_performance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_name TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT DEFAULT '1d',
    total_return REAL,
    sharpe_ratio REAL,
    max_drawdown REAL,
    win_rate REAL,
    profit_factor REAL,
    total_trades INTEGER,
    avg_trade_return REAL,
    tested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    parameters TEXT,
    notes TEXT,
    UNIQUE(strategy_name, symbol, timeframe)
);
```

**Purpose:** Stores backtest results for strategy comparison.
**Data:** 166 backtest results across 7 strategies × 18 symbols × 2 timeframes.

---

### **`strategy_signals_log` – Live Signal Tracking**
```sql
CREATE TABLE strategy_signals_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    portfolio_id INTEGER NOT NULL,
    strategy_name TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    signal_type TEXT NOT NULL,      -- 'BUY', 'SELL', 'HOLD'
    signal_strength REAL,
    price_at_signal REAL,
    indicators_json TEXT,           -- Snapshot of relevant indicators
    executed BOOLEAN DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**Purpose:** Logs every signal generated by active strategies for burn-in comparison.

---

## **Legacy Tables (Backward Compatibility)**

### **15. `paper_trades` – Legacy Trade Records**
```sql
CREATE TABLE paper_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    side TEXT NOT NULL,
    symbol TEXT NOT NULL,
    quantity REAL NOT NULL,
    price REAL NOT NULL,
    amount_usd REAL NOT NULL,
    fee REAL NOT NULL,
    portfolio_value REAL,
    cash_after REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**Purpose:** Original paper‑trade table, maintained for compatibility.  
**Migration:** Data copied to `trades` table, but new writes go to `trades`.

---

### **16. `paper_snapshots` – Legacy Portfolio Snapshots**
```sql
CREATE TABLE paper_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    portfolio_value REAL NOT NULL,
    cash REAL NOT NULL,
    num_positions INTEGER NOT NULL,
    positions_json TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**Purpose:** Daily JSON‑based snapshots, maintained for compatibility.  
**Migration:** Updated from `portfolio_performance` and `positions` via legacy adapter.

---

## **Views (Read‑Only Interfaces)**

### **`v_current_positions` – Current Open Positions**
```sql
CREATE VIEW v_current_positions AS
SELECT 
    p.symbol,
    p.quantity,
    p.entry_price,
    p.current_price,
    (p.current_price - p.entry_price) / p.entry_price * 100 as pnl_percent,
    p.entry_time,
    p.updated_at
FROM positions p
WHERE p.status = 'open' AND p.portfolio_id = 1;
```

**Purpose:** Simple query for current portfolio.

---

### **`v_portfolio_snapshot` – Portfolio Performance Timeline**
```sql
CREATE VIEW v_portfolio_snapshot AS
SELECT 
    DATE(snapshot_time) as date,
    total_value,
    cash,
    invested,
    daily_pnl,
    daily_return
FROM portfolio_performance
WHERE portfolio_id = 1
ORDER BY snapshot_time DESC;
```

**Purpose:** Daily portfolio summary.

---

### **`legacy_paper_snapshots` – Backward‑Compatible View**
```sql
CREATE VIEW legacy_paper_snapshots AS
SELECT 
    DATE() as date,
    pp.total_value as portfolio_value,
    pp.cash,
    COUNT(p.id) as num_positions,
    json_object(
        GROUP_CONCAT(p.symbol),
        json_group_array(
            json_object(
                'quantity', p.quantity,
                'avg_price', p.entry_price
            )
        )
    ) as positions_json
FROM portfolio_performance pp
LEFT JOIN positions p ON p.portfolio_id = pp.portfolio_id AND p.status = 'open'
WHERE pp.portfolio_id = 1
GROUP BY pp.id
ORDER BY pp.snapshot_time DESC
LIMIT 1;
```

**Purpose:** Mimics old `paper_snapshots` table for legacy code.

---

## **Triggers**

### **`update_portfolio_timestamp`**
```sql
CREATE TRIGGER update_portfolio_timestamp 
AFTER UPDATE ON portfolios
BEGIN
    UPDATE portfolios SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;
```

### **`update_position_timestamp`**
```sql
CREATE TRIGGER update_position_timestamp 
AFTER UPDATE ON positions
BEGIN
    UPDATE positions SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;
```

---

## **Indexes Summary**

| Table | Index | Columns | Purpose |
|-------|-------|---------|---------|
| `positions` | `idx_positions_portfolio_symbol` | `(portfolio_id, symbol, status)` | Unique constraint, fast lookup |
| `trades` | `idx_trades_symbol_time` | `(symbol, created_at)` | Trade history by symbol |
| `trades` | `idx_trades_portfolio_time` | `(portfolio_id, created_at)` | Portfolio trade history |
| `risk_events` | `idx_risk_events_time` | `(created_at)` | Recent risk events |
| `risk_events` | `idx_risk_events_symbol_time` | `(symbol, created_at)` | Risk events by symbol |
| `ticks` | `idx_ticks_symbol_time` | `(symbol, timestamp)` | Time‑series queries |
| `news_sentiment_stream` | `idx_news_symbol_time` | `(symbol, news_time)` | News lookup |
| `system_metrics` | `idx_metrics_name_time` | `(metric_name, timestamp)` | Metric time‑series |
| `candlesticks` | (implicit) | `(symbol, timeframe, timestamp)` | Candle lookup |
| `indicators` | (implicit) | `(symbol, timeframe, timestamp)` | Indicator lookup |
| `accounts` | (implicit) | `UNIQUE(portfolio_id, venue_id, account_ref)` | Prevent duplicate accounts |
| `portfolio_strategies` | (implicit) | `UNIQUE(portfolio_id, strategy_id)` | Prevent duplicate assignments |
| `portfolio_valuations` | (implicit) | `UNIQUE(portfolio_id, snapshot_time)` | One valuation per time point |
| `dex_tokens` | (implicit) | `UNIQUE(chain, contract_address)` | Token dedup |
| `dex_transactions` | (implicit) | `UNIQUE(tx_hash)` | Transaction dedup |

---

## **Data Lifecycle & Retention**

| Table | Retention Policy | Cleanup |
|-------|-----------------|---------|
| `ticks` | 7 days | `DELETE FROM ticks WHERE timestamp < datetime('now', '-7 days')` |
| `candlesticks` | Keep all | None (historical analysis required) |
| `indicators` | Keep all | None |
| `trades` | Keep all | None (audit trail) |
| `risk_events` | 90 days | Archive to cold storage after 90d |
| `system_metrics` | 30 days | Roll up to daily aggregates after 30d |

**Implementation:** Monthly maintenance job via `systemd` timer.

---

## **Migration Path to TimescaleDB**

Current schema is SQLite‑compatible but designed for easy migration to TimescaleDB (PostgreSQL):

1. **Hypertables:** `ticks`, `candlesticks`, `portfolio_performance`, `risk_events`
2. **Continuous aggregates:** 1m → 5m → 1h → 4h → 1d candles
3. **Partitioning:** By symbol for `ticks`, by portfolio for `trades`
4. **Replication:** Read replicas for analytics queries

**Migration script:** `migration/timescale_migration.py` (to be written).

---

## **Query Patterns**

### **Current Portfolio Value**
```sql
SELECT 
    pp.total_value,
    pp.cash,
    pp.invested,
    pp.daily_pnl,
    pp.daily_return
FROM portfolio_performance pp
WHERE pp.portfolio_id = 1
ORDER BY pp.snapshot_time DESC
LIMIT 1;
```

### **Open Positions with P&L**
```sql
SELECT 
    symbol,
    quantity,
    entry_price,
    current_price,
    (current_price - entry_price) / entry_price * 100 as pnl_percent,
    stop_loss_price,
    trailing_stop_price
FROM positions
WHERE portfolio_id = 1 AND status = 'open';
```

### **Recent Risk Events**
```sql
SELECT 
    symbol,
    event_type,
    trigger_price,
    current_price,
    action_taken,
    reason,
    created_at
FROM risk_events
WHERE portfolio_id = 1
ORDER BY created_at DESC
LIMIT 10;
```

### **Trade History with Context**
```sql
SELECT 
    created_at,
    symbol,
    side,
    quantity,
    price,
    amount_usd,
    risk_event_type,
    json_extract(decision_context, '$.signal_strength') as signal_strength
FROM trades
WHERE portfolio_id = 1
ORDER BY created_at DESC
LIMIT 20;
```

### **Market Data Freshness**
```sql
SELECT 
    symbol,
    MAX(timestamp) as last_tick,
    COUNT(*) as ticks_last_hour,
    AVG(price) as avg_price
FROM ticks
WHERE timestamp > datetime('now', '-1 hour')
GROUP BY symbol;
```

---

## **Maintenance Operations**

### **Weekly**
```sql
-- Reindex for performance
REINDEX;

-- Update statistics
ANALYZE;

-- Vacuum (if needed)
VACUUM;
```

### **Monthly**
```sql
-- Archive old ticks
DELETE FROM ticks WHERE timestamp < datetime('now', '-7 days');

-- Archive old risk events  
DELETE FROM risk_events WHERE created_at < datetime('now', '-90 days');

-- Archive system metrics
DELETE FROM system_metrics WHERE timestamp < datetime('now', '-30 days');
```

### **On Demand**
```sql
-- Check database integrity
PRAGMA integrity_check;

-- Check foreign key constraints  
PRAGMA foreign_key_check;

-- See table sizes
SELECT name, (pgsize/1024/1024) as size_mb 
FROM dbstat 
WHERE name NOT LIKE 'sqlite_%' 
ORDER BY pgsize DESC;
```

---

## **Change Log**

| Date | Change | Author |
|------|--------|--------|
| 2026‑03‑11 | Initial schema created for real‑time system | Aleph |
| 2026‑03‑11 | Added `market_regime_detailed`, `news_sentiment_stream` | Aleph |
| 2026‑03‑11 | Added triggers for `updated_at` columns | Aleph |
| 2026‑03‑11 | Created backward‑compatibility views | Aleph |
| 2026‑03‑11 | Documented schema as source of truth | Aleph |
| 2026‑03‑12 | Added dimension tables: `exchanges`, `securities`, `strategy_registry` | Aleph |
| 2026‑03‑12 | Added `strategy_performance`, `strategy_signals_log`, `trade_outcomes` | Aleph |
| 2026‑03‑15 | Phase 1 warehouse redesign: `accounts`, `portfolio_strategies`, `transfers`, `portfolio_valuations` | Aleph |
| 2026‑03‑15 | Enhanced `exchanges` (+chain/chain_id/fee_model/settlement_type) | Aleph |
| 2026‑03‑15 | Enhanced `trades` (+account_id/strategy_id/DEX columns) | Aleph |
| 2026‑03‑15 | Enhanced `positions` (+account_id), `securities` (+canonical_instrument_id/decimals) | Aleph |
| 2026‑03‑15 | Added DEX tables: `dex_tokens`, `dex_transactions` | Aleph |
| 2026‑03‑15 | Phase 2: dual-write account_id in paper_trading, dex_executor, real_time_risk | Aleph |
| 2026‑03‑16 | Phase 3: all read queries migrated to account_id (8 files, with fallback) | Aleph |
| 2026‑03‑16 | Added `config.get_account_ids()` utility for bulk account resolution | Aleph |
| 2026‑03‑16 | Documentation update for Phase 1-3 completeness | Aleph |

---

**File:** `ROADMAP/DATABASE_SCHEMA.md`
**Generated:** 2026‑03‑11 16:47 EDT · **Updated:** 2026‑03‑16
**Valid Until:** Schema changes require update to this document.  

*This document is the single source of truth for database structure. Any code change that modifies the schema must update this document accordingly.*