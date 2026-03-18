import sqlite3

conn = sqlite3.connect('crypto_data.db')
cursor = conn.cursor()

# List of new tables to drop (keep original tables)
new_tables = [
    'portfolios', 'positions', 'trades', 'risk_events', 
    'portfolio_performance', 'ticks', 'market_regime_detailed',
    'news_sentiment_stream', 'system_metrics',
    'legacy_paper_snapshots', 'v_current_positions', 'v_portfolio_snapshot'
]

# Drop views first
cursor.execute("SELECT name FROM sqlite_master WHERE type='view'")
views = cursor.fetchall()
for view in views:
    view_name = view[0]
    if view_name in new_tables or view_name.startswith('v_') or view_name.startswith('legacy_'):
        print(f"Dropping view: {view_name}")
        cursor.execute(f"DROP VIEW IF EXISTS {view_name}")

# Drop triggers
cursor.execute("SELECT name FROM sqlite_master WHERE type='trigger'")
triggers = cursor.fetchall()
for trigger in triggers:
    trigger_name = trigger[0]
    if trigger_name.startswith('update_'):
        print(f"Dropping trigger: {trigger_name}")
        cursor.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")

# Drop indexes
cursor.execute("SELECT name FROM sqlite_master WHERE type='index'")
indexes = cursor.fetchall()
for index in indexes:
    index_name = index[0]
    if index_name.startswith('idx_'):
        print(f"Dropping index: {index_name}")
        cursor.execute(f"DROP INDEX IF EXISTS {index_name}")

# Drop tables
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = cursor.fetchall()
for table in tables:
    table_name = table[0]
    if table_name in new_tables:
        print(f"Dropping table: {table_name}")
        cursor.execute(f"DROP TABLE IF EXISTS {table_name}")

conn.commit()
conn.close()
print("Cleanup complete")