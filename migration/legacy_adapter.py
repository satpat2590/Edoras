#!/usr/bin/env python3
"""
Adapter for legacy paper trading system.
Updates legacy JSON files from new database tables.
"""

import sqlite3
import json
import os
from datetime import datetime
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class LegacyAdapter:
    """Bridges new database schema with legacy JSON files"""
    
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
    
    def update_paper_portfolio_state(self):
        """Update paper_portfolio_full_state.json from database"""
        cursor = self.conn.cursor()
        
        # Get portfolio value
        cursor.execute("""
            SELECT total_value, cash, invested 
            FROM portfolio_performance 
            WHERE portfolio_id = 1
            ORDER BY snapshot_time DESC LIMIT 1
        """)
        portfolio_row = cursor.fetchone()
        
        if not portfolio_row:
            logger.warning("No portfolio performance data found")
            return
        
        total_value = portfolio_row['total_value']
        cash = portfolio_row['cash']
        invested = portfolio_row['invested']
        
        # Get open positions
        cursor.execute("""
            SELECT symbol, quantity, entry_price, current_price
            FROM positions 
            WHERE portfolio_id = 1 AND status = 'open'
        """)
        positions = cursor.fetchall()
        
        # Build positions dict
        positions_dict = {}
        entry_prices = {}
        for pos in positions:
            symbol = pos['symbol']
            positions_dict[symbol] = {
                'quantity': pos['quantity'],
                'avg_price': pos['entry_price']
            }
            entry_prices[symbol] = pos['entry_price']
        
        # Get trade history (last 50 trades)
        cursor.execute("""
            SELECT created_at, side, symbol, quantity, price, amount_usd
            FROM trades 
            WHERE portfolio_id = 1
            ORDER BY created_at DESC LIMIT 50
        """)
        trades = cursor.fetchall()
        
        trade_history = []
        for trade in trades:
            trade_history.append({
                'timestamp': trade['created_at'],
                'type': trade['side'],
                'symbol': trade['symbol'],
                'quantity': trade['quantity'],
                'price': trade['price'],
                'amount_usd': trade['amount_usd'],
                'cost': trade['amount_usd'] * 0.001,  # 0.1% fee estimate
                'total_cost': trade['amount_usd'] * 1.001 if trade['side'] == 'BUY' else trade['amount_usd'] * 0.999
            })
        
        # Build state object
        state = {
            'capital': cash,
            'initial_capital': 1000.0,
            'positions': positions_dict,
            'entry_prices': entry_prices,
            'trade_history': trade_history,
            'last_updated': datetime.now().isoformat(),
            'portfolio_value': total_value
        }
        
        # Write to file
        with open('paper_portfolio_full_state.json', 'w') as f:
            json.dump(state, f, indent=2)
        
        logger.info(f"Updated paper_portfolio_full_state.json with {len(positions_dict)} positions")
    
    def update_paper_snapshots(self):
        """Ensure paper_snapshots table has latest data (for legacy code)"""
        cursor = self.conn.cursor()
        
        # Get latest portfolio performance
        cursor.execute("""
            SELECT snapshot_time, total_value, cash, invested
            FROM portfolio_performance 
            WHERE portfolio_id = 1
            ORDER BY snapshot_time DESC LIMIT 1
        """)
        perf = cursor.fetchone()
        
        if not perf:
            return
        
        # Get positions as JSON
        cursor.execute("""
            SELECT symbol, quantity, entry_price
            FROM positions 
            WHERE portfolio_id = 1 AND status = 'open'
        """)
        positions = cursor.fetchall()
        
        positions_json = {}
        for pos in positions:
            positions_json[pos['symbol']] = {
                'quantity': pos['quantity'],
                'avg_price': pos['entry_price']
            }
        
        # Insert or update paper_snapshots
        cursor.execute("""
            INSERT OR REPLACE INTO paper_snapshots (date, portfolio_value, cash, num_positions, positions_json, created_at)
            VALUES (date(?), ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (
            perf['snapshot_time'], perf['total_value'], perf['cash'],
            len(positions), json.dumps(positions_json)
        ))
        
        self.conn.commit()
        logger.info(f"Updated paper_snapshots table")
    
    def run(self):
        """Run all adapter tasks"""
        logger.info("Starting legacy adapter...")
        
        self.connect()
        
        try:
            self.update_paper_portfolio_state()
            self.update_paper_snapshots()
            logger.info("Legacy adapter completed successfully")
            
        except Exception as e:
            logger.error(f"Legacy adapter failed: {e}")
            raise
        
        finally:
            self.close()

if __name__ == "__main__":
    adapter = LegacyAdapter()
    adapter.run()