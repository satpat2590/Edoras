-- Migration 001: Unify strategy_catalogue, strategy_performance, and strategy_registry
--
-- This SQL is for reference / documentation. The actual migration is run by
-- the Python code in __init__.py:run_migration_001() which checks column
-- existence before each ALTER TABLE (SQLite doesn't support IF NOT EXISTS
-- on ALTER TABLE).

-- Link strategy_performance to its source catalogue entry
ALTER TABLE strategy_performance ADD COLUMN catalogue_id INTEGER REFERENCES strategy_catalogue(id);

-- Link strategy_registry to the backtest that qualified it for deployment
ALTER TABLE strategy_registry ADD COLUMN qualifying_catalogue_id INTEGER REFERENCES strategy_catalogue(id);
ALTER TABLE strategy_registry ADD COLUMN qualified_at TEXT;

-- Fix trade_outcomes: add proper FKs to the trades table
-- (trade_outcomes.trade_id is a PK AUTOINCREMENT, NOT an FK to trades.id)
ALTER TABLE trade_outcomes ADD COLUMN buy_trade_id INTEGER REFERENCES trades(id);
ALTER TABLE trade_outcomes ADD COLUMN sell_trade_id INTEGER REFERENCES trades(id);

-- Track how each catalogue entry was produced
ALTER TABLE strategy_catalogue ADD COLUMN source TEXT DEFAULT 'backtest';
-- Values: 'backtest', 'walk_forward', 'holdout_gate', 'portfolio_backtest'

-- Unified view joining registry → catalogue → performance
CREATE VIEW IF NOT EXISTS strategy_overview AS
SELECT
    r.id AS registry_id,
    r.name,
    r.strategy_type,
    r.is_active,
    c.id AS catalogue_id,
    c.symbol,
    c.timeframe,
    c.sharpe_ratio,
    c.total_return,
    c.max_drawdown,
    c.total_trades,
    c.catalogued_at,
    p.id AS performance_id,
    p.source AS perf_source,
    p.period_start,
    p.period_end
FROM strategy_registry r
LEFT JOIN strategy_catalogue c ON c.id = r.qualifying_catalogue_id
LEFT JOIN strategy_performance p ON p.catalogue_id = c.id;
