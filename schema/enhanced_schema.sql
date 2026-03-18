-- Enhanced SQLite schema for real-time wealth management
-- Designed to be compatible with TimescaleDB migration later

-- 1. Portfolios table (multiple portfolios possible)
CREATE TABLE IF NOT EXISTS portfolios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL DEFAULT 'Main Portfolio',
    description TEXT,
    initial_capital REAL NOT NULL DEFAULT 1000.0,
    currency TEXT NOT NULL DEFAULT 'USD',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_active BOOLEAN DEFAULT 1
);

-- 2. Detailed positions table (replaces positions_json in paper_snapshots)
CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    portfolio_id INTEGER NOT NULL REFERENCES portfolios(id),
    symbol TEXT NOT NULL,
    quantity REAL NOT NULL,
    entry_price REAL NOT NULL,
    entry_time TIMESTAMP NOT NULL,
    current_price REAL,
    stop_loss_price REAL,
    trailing_stop_price REAL,
    take_profit_levels TEXT, -- JSON array: [{"level": 0.15, "triggered": false}, ...]
    status TEXT CHECK(status IN ('open', 'closed', 'partial')) DEFAULT 'open',
    pnl REAL DEFAULT 0.0,
    pnl_percent REAL DEFAULT 0.0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(portfolio_id, symbol) WHERE status = 'open'
);

-- 3. Unified trades table (source of truth for all trades across all portfolios)
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    portfolio_id INTEGER NOT NULL REFERENCES portfolios(id),
    symbol TEXT NOT NULL,
    side TEXT CHECK(side IN ('BUY', 'SELL')) NOT NULL,
    quantity REAL NOT NULL,
    price REAL NOT NULL,
    amount_usd REAL NOT NULL,
    fee REAL DEFAULT 0.0,
    order_type TEXT DEFAULT 'market',
    status TEXT CHECK(status IN ('filled', 'partial', 'cancelled', 'rejected')) DEFAULT 'filled',
    decision_context TEXT, -- JSON with LLM reasoning, signals, risk checks
    related_position_id INTEGER REFERENCES positions(id),
    risk_event_type TEXT, -- 'stop_loss', 'trailing_stop', 'take_profit', 'circuit_breaker', 'manual'
    parent_trade_id INTEGER, -- For take-profit partial sells
    portfolio_value REAL, -- Portfolio value at time of trade
    cash_after REAL, -- Cash balance after trade
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX trades_symbol_time_idx (symbol, created_at),
    INDEX trades_portfolio_idx (portfolio_id, created_at)
);

-- 4. Risk events log
CREATE TABLE IF NOT EXISTS risk_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    portfolio_id INTEGER NOT NULL REFERENCES portfolios(id),
    symbol TEXT,
    event_type TEXT NOT NULL, -- 'stop_loss', 'trailing_stop', 'take_profit', 'circuit_breaker', 'position_limit', 'sector_limit'
    trigger_price REAL,
    current_price REAL,
    quantity REAL,
    action_taken TEXT, -- 'full_exit', 'partial_exit', 'alert_only'
    reason TEXT,
    metadata TEXT, -- JSON with additional context
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX risk_events_time_idx (created_at),
    INDEX risk_events_symbol_idx (symbol, created_at)
);

-- 5. Portfolio performance snapshots (hourly/daily)
CREATE TABLE IF NOT EXISTS portfolio_performance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    portfolio_id INTEGER NOT NULL REFERENCES portfolios(id),
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
    UNIQUE(portfolio_id, snapshot_time)
);

-- 6. Real-time ticks (for WebSocket data)
CREATE TABLE IF NOT EXISTS ticks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    price REAL NOT NULL,
    volume REAL,
    exchange TEXT DEFAULT 'coinbase',
    bid REAL,
    ask REAL,
    timestamp TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX ticks_symbol_time_idx (symbol, timestamp)
);

-- 7. Market regime with more granularity
CREATE TABLE IF NOT EXISTS market_regime_detailed (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TIMESTAMP NOT NULL,
    vix_value REAL,
    regime TEXT, -- 'risk_on', 'risk_off', 'neutral', 'high_volatility'
    btc_spy_corr REAL,
    btc_qqq_corr REAL,
    spy_qqq_corr REAL,
    crypto_sector_momentum REAL,
    equity_sector_momentum REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(timestamp)
);

-- 8. News sentiment stream
CREATE TABLE IF NOT EXISTS news_sentiment_stream (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    headline TEXT,
    summary TEXT,
    sentiment_score REAL,
    confidence REAL,
    source TEXT,
    news_time TIMESTAMP,
    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX news_symbol_time_idx (symbol, news_time)
);

-- 9. System metrics (for monitoring)
CREATE TABLE IF NOT EXISTS system_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    metric_name TEXT NOT NULL,
    metric_value REAL,
    labels TEXT, -- JSON
    timestamp TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX metrics_name_time_idx (metric_name, timestamp)
);

-- Initialize default portfolio
INSERT OR IGNORE INTO portfolios (id, name, initial_capital) 
VALUES (1, 'Main Paper Portfolio', 1000.0);

-- Migrate existing paper trades
INSERT INTO trades (portfolio_id, symbol, side, quantity, price, amount_usd, fee, created_at)
SELECT 
    1 as portfolio_id,
    symbol,
    side,
    quantity,
    price,
    amount_usd,
    fee,
    timestamp
FROM paper_trades
WHERE NOT EXISTS (SELECT 1 FROM trades WHERE trades.created_at = paper_trades.timestamp);

-- Migrate positions from paper_portfolio_full_state.json
-- This will be done by Python script

-- Create views for backward compatibility
CREATE VIEW IF NOT EXISTS v_current_positions AS
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

CREATE VIEW IF NOT EXISTS v_portfolio_snapshot AS
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

-- Update triggers
CREATE TRIGGER IF NOT EXISTS update_portfolio_timestamp 
AFTER UPDATE ON portfolios
BEGIN
    UPDATE portfolios SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS update_position_timestamp 
AFTER UPDATE ON positions
BEGIN
    UPDATE positions SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;