-- Enhanced SQLite schema for real-time wealth management
-- SQLite compatible version

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
    portfolio_id INTEGER NOT NULL,
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
    FOREIGN KEY (portfolio_id) REFERENCES portfolios(id)
);
-- CREATE UNIQUE INDEX IF NOT EXISTS idx_positions_portfolio_symbol_open ON positions(portfolio_id, symbol) WHERE status = 'open';
-- SQLite doesn't support partial indexes with WHERE, using regular unique constraint
CREATE UNIQUE INDEX IF NOT EXISTS idx_positions_portfolio_symbol ON positions(portfolio_id, symbol, status);

-- 3. Unified trades table (source of truth for all trades across all portfolios)
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    portfolio_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT CHECK(side IN ('BUY', 'SELL')) NOT NULL,
    quantity REAL NOT NULL,
    price REAL NOT NULL,
    amount_usd REAL NOT NULL,
    fee REAL DEFAULT 0.0,
    order_type TEXT DEFAULT 'market',
    status TEXT CHECK(status IN ('filled', 'partial', 'cancelled', 'rejected')) DEFAULT 'filled',
    decision_context TEXT, -- JSON with LLM reasoning, signals, risk checks
    related_position_id INTEGER,
    risk_event_type TEXT, -- 'stop_loss', 'trailing_stop', 'take_profit', 'circuit_breaker', 'manual'
    parent_trade_id INTEGER, -- For take-profit partial sells
    portfolio_value REAL, -- Portfolio value at time of trade
    cash_after REAL, -- Cash balance after trade
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (portfolio_id) REFERENCES portfolios(id),
    FOREIGN KEY (related_position_id) REFERENCES positions(id)
);
CREATE INDEX IF NOT EXISTS idx_trades_symbol_time ON trades(symbol, created_at);
CREATE INDEX IF NOT EXISTS idx_trades_portfolio_time ON trades(portfolio_id, created_at);

-- 4. Risk events log
CREATE TABLE IF NOT EXISTS risk_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    portfolio_id INTEGER NOT NULL,
    symbol TEXT,
    event_type TEXT NOT NULL, -- 'stop_loss', 'trailing_stop', 'take_profit', 'circuit_breaker', 'position_limit', 'sector_limit'
    trigger_price REAL,
    current_price REAL,
    quantity REAL,
    action_taken TEXT, -- 'full_exit', 'partial_exit', 'alert_only'
    reason TEXT,
    metadata TEXT, -- JSON with additional context
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (portfolio_id) REFERENCES portfolios(id)
);
CREATE INDEX IF NOT EXISTS idx_risk_events_time ON risk_events(created_at);
CREATE INDEX IF NOT EXISTS idx_risk_events_symbol_time ON risk_events(symbol, created_at);

-- 5. Portfolio performance snapshots (hourly/daily)
CREATE TABLE IF NOT EXISTS portfolio_performance (
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
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ticks_symbol_time ON ticks(symbol, timestamp);

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
    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_news_symbol_time ON news_sentiment_stream(symbol, news_time);

-- 9. System metrics (for monitoring)
CREATE TABLE IF NOT EXISTS system_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    metric_name TEXT NOT NULL,
    metric_value REAL,
    labels TEXT, -- JSON
    timestamp TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_metrics_name_time ON system_metrics(metric_name, timestamp);

-- Initialize default portfolio
INSERT OR IGNORE INTO portfolios (id, name, initial_capital) 
VALUES (1, 'Main Paper Portfolio', 1000.0);

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

-- 11. Tax lots (FIFO cost basis tracking)
CREATE TABLE IF NOT EXISTS tax_lots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    portfolio_id INTEGER NOT NULL REFERENCES portfolios(id),
    symbol TEXT NOT NULL,
    buy_trade_id INTEGER NOT NULL REFERENCES trades(id),
    acquired_at TIMESTAMP NOT NULL,
    original_quantity REAL NOT NULL,
    remaining_quantity REAL NOT NULL,
    cost_basis_per_unit REAL NOT NULL,
    total_cost_basis REAL NOT NULL,
    status TEXT CHECK(status IN ('open', 'depleted')) DEFAULT 'open',
    strategy_id INTEGER REFERENCES strategy_registry(id),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_tax_lots_portfolio_symbol ON tax_lots(portfolio_id, symbol) WHERE status = 'open';
CREATE INDEX IF NOT EXISTS idx_tax_lots_acquired ON tax_lots(portfolio_id, acquired_at);

-- 12. Lot dispositions (realized gain/loss per lot)
CREATE TABLE IF NOT EXISTS lot_dispositions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tax_lot_id INTEGER NOT NULL REFERENCES tax_lots(id),
    sell_trade_id INTEGER NOT NULL REFERENCES trades(id),
    portfolio_id INTEGER NOT NULL REFERENCES portfolios(id),
    symbol TEXT NOT NULL,
    quantity REAL NOT NULL,
    proceeds_per_unit REAL NOT NULL,
    cost_basis_per_unit REAL NOT NULL,
    realized_gain_usd REAL NOT NULL,
    holding_period_days INTEGER NOT NULL,
    term TEXT CHECK(term IN ('short', 'long')) NOT NULL,
    is_wash_sale INTEGER DEFAULT 0,
    wash_disallowed_usd REAL DEFAULT 0.0,
    replacement_lot_id INTEGER REFERENCES tax_lots(id),
    disposition_method TEXT DEFAULT 'fifo' CHECK(disposition_method IN ('fifo', 'specific_id')),
    disposed_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_dispositions_sell_trade ON lot_dispositions(sell_trade_id);
CREATE INDEX IF NOT EXISTS idx_dispositions_portfolio_time ON lot_dispositions(portfolio_id, disposed_at);

-- 13. Cost ledger (fee and cost tracking per trade)
CREATE TABLE IF NOT EXISTS cost_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id INTEGER NOT NULL REFERENCES trades(id),
    portfolio_id INTEGER NOT NULL REFERENCES portfolios(id),
    symbol TEXT NOT NULL,
    cost_type TEXT NOT NULL CHECK(cost_type IN ('exchange_fee', 'gas_fee', 'slippage', 'network_fee')),
    amount_usd REAL NOT NULL,
    strategy_id INTEGER REFERENCES strategy_registry(id),
    detail TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_cost_ledger_trade ON cost_ledger(trade_id);
CREATE INDEX IF NOT EXISTS idx_cost_ledger_portfolio_type ON cost_ledger(portfolio_id, cost_type);

-- Views: Tax & Cost reporting
CREATE VIEW IF NOT EXISTS v_realized_gains AS
SELECT d.portfolio_id, d.symbol, d.term, d.disposed_at, d.quantity,
    d.cost_basis_per_unit, d.proceeds_per_unit, d.realized_gain_usd,
    d.holding_period_days, d.is_wash_sale, d.wash_disallowed_usd,
    d.realized_gain_usd - d.wash_disallowed_usd AS adjusted_gain_usd,
    tl.strategy_id, sr.name AS strategy_name
FROM lot_dispositions d
JOIN tax_lots tl ON d.tax_lot_id = tl.id
LEFT JOIN strategy_registry sr ON tl.strategy_id = sr.id;

CREATE VIEW IF NOT EXISTS v_open_lots AS
SELECT tl.portfolio_id, tl.symbol, tl.acquired_at, tl.remaining_quantity,
    tl.cost_basis_per_unit,
    tl.remaining_quantity * tl.cost_basis_per_unit AS remaining_cost_basis,
    CAST(julianday('now') - julianday(tl.acquired_at) AS INTEGER) AS days_held,
    CASE WHEN julianday('now') - julianday(tl.acquired_at) >= 365 THEN 'long' ELSE 'short' END AS projected_term,
    sr.name AS strategy_name
FROM tax_lots tl
LEFT JOIN strategy_registry sr ON tl.strategy_id = sr.id
WHERE tl.status = 'open' AND tl.remaining_quantity > 0;

CREATE VIEW IF NOT EXISTS v_cost_summary AS
SELECT cl.portfolio_id, cl.cost_type, cl.strategy_id, sr.name AS strategy_name,
    cl.symbol, SUM(cl.amount_usd) AS total_cost_usd, COUNT(*) AS event_count,
    AVG(cl.amount_usd) AS avg_cost_usd
FROM cost_ledger cl
LEFT JOIN strategy_registry sr ON cl.strategy_id = sr.id
GROUP BY cl.portfolio_id, cl.cost_type, cl.strategy_id, cl.symbol;