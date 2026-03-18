#!/usr/bin/env python3
"""
Migrate existing SQLite database to enhanced schema.
Maintains backward compatibility while adding new tables.
"""

import sqlite3
import json
import os
from datetime import datetime
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class DatabaseMigrator:
    def __init__(self, db_path: str = "crypto_data.db"):
        self.db_path = db_path
        self.conn = None
        
    def connect(self):
        """Connect to database"""
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        logger.info(f"Connected to {self.db_path}")
        
    def close(self):
        """Close database connection"""
        if self.conn:
            self.conn.close()
            logger.info("Database connection closed")
    
    def apply_schema(self):
        """Apply enhanced schema"""
        schema_path = os.path.join(os.path.dirname(__file__), "../schema/enhanced_schema_sqlite.sql")
        
        with open(schema_path, 'r') as f:
            schema_sql = f.read()
        
        cursor = self.conn.cursor()
        
        # Remove comments and split
        lines = schema_sql.split('\n')
        cleaned_lines = []
        for line in lines:
            line = line.strip()
            if line and not line.startswith('--'):
                cleaned_lines.append(line)
        
        full_sql = '\n'.join(cleaned_lines)
        statements = full_sql.split(';')
        
        for statement in statements:
            statement = statement.strip()
            if statement:
                try:
                    cursor.execute(statement)
                    logger.debug(f"Executed: {statement[:80]}...")
                except sqlite3.Error as e:
                    # Ignore "already exists" errors
                    if "already exists" not in str(e) and "duplicate" not in str(e):
                        logger.warning(f"SQL execution warning: {e}")
                        logger.debug(f"Statement: {statement}")
        
        self.conn.commit()
        logger.info("Enhanced schema applied")
    
    def migrate_paper_trades(self):
        """Migrate paper_trades to new trades table.

        NOTE: paper_trades has been consolidated into trades (March 2026).
        paper_trades_legacy holds the original rows; a backward-compat VIEW
        named paper_trades exists. This method is kept for safety on re-runs.
        """
        cursor = self.conn.cursor()

        # Check if the legacy table exists (post-consolidation)
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='paper_trades_legacy'")
        if cursor.fetchone():
            logger.info("paper_trades already consolidated into trades — skipping")
            return

        # Pre-consolidation path: check if old paper_trades table exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='paper_trades'")
        if not cursor.fetchone():
            logger.info("No paper_trades table found — skipping")
            return

        # Check if migration already done
        cursor.execute("SELECT COUNT(*) FROM trades")
        if cursor.fetchone()[0] > 0:
            logger.info("trades table already populated — skipping paper_trades migration")
            return

        cursor.execute("""
            INSERT INTO trades (
                portfolio_id, symbol, side, quantity, price, amount_usd, fee,
                order_type, status, portfolio_value, cash_after, created_at
            )
            SELECT
                COALESCE(portfolio_id, 1),
                symbol, side, quantity, price, amount_usd, fee,
                'market', 'filled', portfolio_value, cash_after, timestamp
            FROM paper_trades
            ORDER BY timestamp
        """)

        migrated = cursor.rowcount
        self.conn.commit()
        logger.info(f"Migrated {migrated} trades from paper_trades")
    
    def migrate_portfolio_state(self):
        """Migrate portfolio state from JSON file to database"""
        state_file = "paper_portfolio_full_state.json"
        
        if not os.path.exists(state_file):
            logger.warning(f"{state_file} not found, skipping portfolio state migration")
            return
        
        with open(state_file, 'r') as f:
            state = json.load(f)
        
        cursor = self.conn.cursor()
        
        # Update portfolio cash
        cursor.execute("""
            UPDATE portfolios 
            SET initial_capital = ?
            WHERE id = 1
        """, (state.get("initial_capital", 1000.0),))
        
        # Create positions from current state
        positions = state.get("positions", {})
        
        for symbol, pos_data in positions.items():
            quantity = pos_data.get("quantity", 0)
            avg_price = pos_data.get("avg_price", 0)
            
            if quantity <= 0:
                continue
            
            # Get current price from candlesticks
            cursor.execute("""
                SELECT close FROM candlesticks 
                WHERE symbol = ? AND timeframe = '1d'
                ORDER BY timestamp DESC LIMIT 1
            """, (symbol,))
            
            result = cursor.fetchone()
            current_price = result[0] if result else avg_price
            
            # Check if position exists
            cursor.execute("""
                SELECT id FROM positions 
                WHERE portfolio_id = 1 AND symbol = ? AND status = 'open'
            """, (symbol,))
            
            existing = cursor.fetchone()
            
            if existing:
                # Update existing position
                cursor.execute("""
                    UPDATE positions SET
                        quantity = ?,
                        entry_price = ?,
                        current_price = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (quantity, avg_price, current_price, existing[0]))
            else:
                # Insert new position
                cursor.execute("""
                    INSERT INTO positions (
                        portfolio_id, symbol, quantity, entry_price, entry_time,
                        current_price, status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'open', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """, (
                    1, symbol, quantity, avg_price, 
                    state.get("last_updated", datetime.now().isoformat()),
                    current_price
                ))
        
        self.conn.commit()
        logger.info(f"Migrated {len(positions)} positions from {state_file}")
    
    def create_portfolio_snapshot(self):
        """Create initial portfolio performance snapshot"""
        cursor = self.conn.cursor()
        
        # Calculate current portfolio value
        cursor.execute("""
            SELECT 
                COALESCE(SUM(p.quantity * p.current_price), 0) as invested,
                (SELECT initial_capital FROM portfolios WHERE id = 1) as initial_capital
            FROM positions p
            WHERE p.portfolio_id = 1 AND p.status = 'open'
        """)
        
        result = cursor.fetchone()
        invested = result[0] if result else 0
        initial_capital = result[1] if result else 1000.0
        
        # Get cash from portfolio state file
        cash = initial_capital - invested
        
        total_value = invested + cash
        
        cursor.execute("""
            INSERT INTO portfolio_performance (
                portfolio_id, snapshot_time, total_value, cash, invested,
                positions_count, created_at
            ) VALUES (?, datetime('now'), ?, ?, ?, 
                (SELECT COUNT(*) FROM positions WHERE portfolio_id = 1 AND status = 'open'),
                CURRENT_TIMESTAMP)
        """, (1, total_value, cash, invested))
        
        self.conn.commit()
        logger.info(f"Created initial portfolio snapshot: total=${total_value:.2f}, cash=${cash:.2f}, invested=${invested:.2f}")
    
    def create_backward_compatibility_views(self):
        """Create views for legacy code compatibility"""
        cursor = self.conn.cursor()
        
        # View that mimics paper_snapshots
        cursor.execute("""
            CREATE VIEW IF NOT EXISTS legacy_paper_snapshots AS
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
            LIMIT 1
        """)
        
        logger.info("Created backward compatibility views")
    
    def run_full_migration(self):
        """Run all migration steps"""
        logger.info("Starting database migration to enhanced schema")
        
        self.connect()
        
        try:
            self.apply_schema()
            self.migrate_paper_trades()
            self.migrate_portfolio_state()
            self.create_portfolio_snapshot()
            self.create_backward_compatibility_views()
            
            logger.info("Migration completed successfully")
            
        except Exception as e:
            logger.error(f"Migration failed: {e}")
            self.conn.rollback()
            raise
        
        finally:
            self.close()

if __name__ == "__main__":
    migrator = DatabaseMigrator()
    migrator.run_full_migration()