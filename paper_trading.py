#!/usr/bin/env python3
"""
Paper Trading Portfolio Simulation
Simulates $1000 portfolio trading based on optimization signals.
Full state persistence: positions, cash, entry prices, and trade history
are saved to disk after every trade and loaded on startup.
"""

import os
import sqlite3
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
import json
import logging

logger = logging.getLogger(__name__)

try:
    from config import ACTIVE_PORTFOLIO_ID, resolve_account_id
except ImportError:
    ACTIVE_PORTFOLIO_ID = 1
    resolve_account_id = None

# Lazy-loaded trade journal (avoid circular imports)
_trade_journal = None
def _get_journal(db_path):
    global _trade_journal
    if _trade_journal is None:
        try:
            from trade_journal import TradeJournal
            _trade_journal = TradeJournal(db_path=db_path)
        except Exception as e:
            logger.debug(f"Trade journal unavailable: {e}")
    return _trade_journal

# Persistent state file — shared by trading agent, risk guardian, and midday reviews
PORTFOLIO_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "paper_portfolio_full_state.json")


class PaperTradingPortfolio:
    """Simulated trading portfolio with $1000 starting capital and full disk persistence."""

    def __init__(self, db_path: str = "crypto_data.db", initial_capital: float = 1000.0,
                 state_file: str = PORTFOLIO_STATE_FILE, portfolio_id: int = ACTIVE_PORTFOLIO_ID):
        self.db_path = db_path
        self.initial_capital = initial_capital
        self.state_file = state_file
        self.portfolio_id = portfolio_id
        self._account_id = None  # resolved lazily
        self.capital = initial_capital
        self.positions = {}  # symbol -> {'quantity': float, 'avg_price': float}
        self.entry_prices = {}  # symbol -> original entry price (for risk manager)
        self.transaction_cost = 0.001  # 0.1%
        self.trade_history = []
        self.portfolio_value_history = []

        # Load persisted state if it exists
        if not self._load_state():
            logger.info(f"Paper trading portfolio initialized fresh with ${initial_capital:.2f}")
        else:
            logger.info(f"Paper trading portfolio loaded: ${self.capital:.2f} cash, {len(self.positions)} positions")

    def _load_state(self) -> bool:
        """Load full portfolio state from disk. Returns True if loaded."""
        if not os.path.exists(self.state_file):
            return False
        try:
            with open(self.state_file, "r") as f:
                data = json.load(f)
            self.capital = data.get("capital", self.initial_capital)
            self.initial_capital = data.get("initial_capital", self.initial_capital)
            self.positions = data.get("positions", {})
            self.entry_prices = data.get("entry_prices", {})
            # Restore trade history (deserialize timestamps)
            self.trade_history = []
            for t in data.get("trade_history", []):
                if isinstance(t.get("timestamp"), str):
                    t["timestamp"] = datetime.fromisoformat(t["timestamp"])
                self.trade_history.append(t)
            return True
        except Exception as e:
            logger.warning(f"Could not load portfolio state: {e}")
            return False

    @property
    def account_id(self) -> int:
        """Lazily resolve account_id for this portfolio's Coinbase paper account."""
        if self._account_id is None and resolve_account_id:
            try:
                self._account_id = resolve_account_id(
                    self.portfolio_id, venue_code="coinbase", db_path=self.db_path
                )
            except (ValueError, Exception):
                pass
        return self._account_id

    def _save_state(self):
        """Persist full portfolio state to disk. Called after every trade."""
        try:
            # Serialize trade history (convert datetimes to ISO strings)
            serialized_trades = []
            for t in self.trade_history[-200:]:  # keep last 200 trades
                tc = dict(t)
                if isinstance(tc.get("timestamp"), datetime):
                    tc["timestamp"] = tc["timestamp"].isoformat()
                serialized_trades.append(tc)

            data = {
                "capital": self.capital,
                "initial_capital": self.initial_capital,
                "positions": self.positions,
                "entry_prices": self.entry_prices,
                "trade_history": serialized_trades,
                "last_updated": datetime.now().isoformat(),
                "portfolio_value": self.get_portfolio_value(),
            }
            with open(self.state_file, "w") as f:
                json.dump(data, f, indent=2)
            # Keep DB positions table in sync
            self.sync_positions_to_db()
        except Exception as e:
            logger.warning(f"Could not save portfolio state: {e}")
    
    def _ensure_db_schema(self):
        """Create portfolio tables in the database if they don't exist."""
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        # trades table is the unified source of truth (managed by enhanced schema).
        # Only ensure paper_snapshots here.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS paper_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                portfolio_value REAL NOT NULL,
                cash REAL NOT NULL,
                num_positions INTEGER NOT NULL,
                positions_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(date)
            )
        """)
        conn.commit()
        conn.close()

    def _save_trade_to_db(self, trade: dict):
        """Persist a trade record to the database."""
        try:
            self._ensure_db_schema()
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()
            ts = trade.get("timestamp")
            if isinstance(ts, datetime):
                ts = ts.isoformat()
            # Build decision context from trade metadata.
            # Priority: _pending_decision_context (one-shot) > _sticky_decision_context (batch) > trade dict > signal metadata
            ctx = trade.get("decision_context")
            if ctx is None and hasattr(self, '_pending_decision_context') and self._pending_decision_context:
                ctx = self._pending_decision_context
                self._pending_decision_context = None  # consume it
            if ctx is None and getattr(self, '_sticky_decision_context', None):
                ctx = self._sticky_decision_context  # NOT consumed — persists across batch trades
            if ctx is None and hasattr(self, '_last_signal_type'):
                sym = trade.get("symbol", "")
                import json as _json
                ctx = _json.dumps({
                    "signal_type": self._last_signal_type.get(sym),
                    "signal_strength": self._last_signal_strength.get(sym),
                    "exit_reason": self._last_exit_reason.get(sym),
                    "market_regime": getattr(self, '_last_regime', None),
                })
            # Resolve trader_id and strategy_id from trade metadata or caller
            trader_id = trade.get("trader_id") or getattr(self, '_current_trader_id', None)
            strategy_id = trade.get("strategy_id") or getattr(self, '_pending_strategy_id', None)
            if strategy_id:
                self._pending_strategy_id = None  # consume it
            cur.execute(
                "INSERT INTO trades "
                "(portfolio_id, account_id, symbol, side, quantity, price, amount_usd, fee, "
                "order_type, status, portfolio_value, cash_after, decision_context, "
                "trader_id, strategy_id, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    self.portfolio_id,
                    self.account_id,
                    trade.get("symbol", ""),
                    trade.get("type", "UNKNOWN"),
                    trade.get("quantity", 0),
                    trade.get("price", 0),
                    trade.get("amount_usd", 0),
                    trade.get("cost", 0),
                    "market",
                    "filled",
                    self.get_portfolio_value(),
                    self.capital,
                    ctx,
                    trader_id,
                    strategy_id,
                    ts,
                ),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"Could not save trade to DB: {e}")

    def sync_positions_to_db(self):
        """Sync in-memory positions to the `positions` table (source of truth for all consumers)."""
        try:
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()
            # Phase 3: delete via account_id when available
            if self.account_id:
                cur.execute("DELETE FROM positions WHERE account_id = ?", (self.account_id,))
            else:
                cur.execute("DELETE FROM positions WHERE portfolio_id = ?", (self.portfolio_id,))
            for symbol, pos in self.positions.items():
                price = self.get_current_price(symbol)
                entry_price = pos.get('avg_price', 0)
                entry_time = self.entry_prices.get(f"{symbol}_date", datetime.now().isoformat())
                pnl = (price - entry_price) * pos['quantity'] if entry_price > 0 else 0
                pnl_pct = ((price - entry_price) / entry_price * 100) if entry_price > 0 else 0
                cur.execute(
                    "INSERT INTO positions "
                    "(portfolio_id, account_id, symbol, quantity, entry_price, entry_time, "
                    "current_price, status, pnl, pnl_percent, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, CURRENT_TIMESTAMP)",
                    (self.portfolio_id, self.account_id, symbol, pos['quantity'], entry_price, entry_time, price, pnl, pnl_pct),
                )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.debug(f"Could not sync positions to DB: {e}")

    def save_daily_snapshot(self):
        """Save a daily portfolio snapshot to the database (call once per day)."""
        try:
            self._ensure_db_schema()
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()
            today = datetime.now().strftime("%Y-%m-%d")
            cur.execute(
                "INSERT OR REPLACE INTO paper_snapshots "
                "(date, portfolio_value, cash, num_positions, positions_json, portfolio_id) "
                "VALUES (?,?,?,?,?,?)",
                (
                    today,
                    self.get_portfolio_value(),
                    self.capital,
                    len(self.positions),
                    json.dumps(self.positions),
                    self.portfolio_id,
                ),
            )
            conn.commit()
            conn.close()
            logger.info(f"Daily snapshot saved for {today}")
        except Exception as e:
            logger.warning(f"Could not save daily snapshot: {e}")

    def get_current_price(self, symbol: str) -> float:
        """Get current price from database"""
        conn = sqlite3.connect(self.db_path)
        query = '''
        SELECT close FROM candlesticks 
        WHERE symbol = ? AND timeframe = '1h'
        ORDER BY timestamp DESC LIMIT 1
        '''
        
        cursor = conn.cursor()
        cursor.execute(query, (symbol,))
        result = cursor.fetchone()
        conn.close()
        
        if result:
            return float(result[0])
        else:
            logger.warning(f"No price data for {symbol}, using $0")
            return 0.0
    
    def _has_decision_context(self, symbol: str) -> bool:
        """Check that a decision context exists for this trade.

        Every trade must be attributed — if no context is available from any
        source, the trade is rejected to prevent unattributed executions.
        """
        if getattr(self, '_pending_decision_context', None):
            return True
        if getattr(self, '_sticky_decision_context', None):
            return True
        if hasattr(self, '_last_signal_type') and self._last_signal_type.get(symbol):
            return True
        return False

    def execute_buy(self, symbol: str, amount_usd: float, timestamp: datetime = None):
        """Execute buy order"""
        if amount_usd <= 0:
            return False

        if not self._has_decision_context(symbol):
            logger.warning(f"REJECTED buy {symbol}: no decision_context — set context before trading")
            return False
        
        price = self.get_current_price(symbol)
        if price <= 0:
            logger.warning(f"Cannot buy {symbol} - price is ${price}")
            return False
        
        # Calculate quantity
        quantity = amount_usd / price
        
        # Apply transaction cost
        cost = amount_usd * self.transaction_cost
        total_cost = amount_usd + cost
        
        if total_cost > self.capital:
            logger.warning(f"Insufficient capital: ${self.capital:.2f} < ${total_cost:.2f}")
            return False
        
        # Update capital
        self.capital -= total_cost
        
        # Update positions
        if symbol in self.positions:
            # Average price calculation
            old_qty = self.positions[symbol]['quantity']
            old_avg = self.positions[symbol]['avg_price']
            new_qty = old_qty + quantity
            new_avg = (old_qty * old_avg + quantity * price) / new_qty

            self.positions[symbol]['quantity'] = new_qty
            self.positions[symbol]['avg_price'] = new_avg
        else:
            self.positions[symbol] = {
                'quantity': quantity,
                'avg_price': price
            }
            # Track original entry for risk manager and journal
            self.entry_prices[symbol] = price
            self.entry_prices[f"{symbol}_date"] = (timestamp or datetime.now()).isoformat()
        
        # Record trade
        trade = {
            'timestamp': timestamp or datetime.now(),
            'type': 'BUY',
            'symbol': symbol,
            'quantity': quantity,
            'price': price,
            'amount_usd': amount_usd,
            'cost': cost,
            'total_cost': total_cost
        }
        self.trade_history.append(trade)

        self._save_state()
        self._save_trade_to_db(trade)
        logger.info(f"Bought {quantity:.6f} {symbol} at ${price:.2f} for ${amount_usd:.2f} (cost: ${cost:.2f})")
        return True
    
    def execute_sell(self, symbol: str, quantity: float, timestamp: datetime = None):
        """Execute sell order"""
        if symbol not in self.positions:
            logger.warning(f"No position in {symbol} to sell")
            return False

        if not self._has_decision_context(symbol):
            logger.warning(f"REJECTED sell {symbol}: no decision_context — set context before trading")
            return False
        
        position = self.positions[symbol]
        current_qty = position['quantity']
        
        # Can't sell more than we have
        if quantity > current_qty:
            quantity = current_qty
        
        price = self.get_current_price(symbol)
        if price <= 0:
            logger.warning(f"Cannot sell {symbol} - price is ${price}")
            return False
        
        # Calculate proceeds
        proceeds = quantity * price
        cost = proceeds * self.transaction_cost
        net_proceeds = proceeds - cost
        
        # Update capital
        self.capital += net_proceeds
        
        # Update position
        new_qty = current_qty - quantity
        position_closed = new_qty <= 0.000001

        if position_closed:
            # Record outcome in trade journal before removing position
            entry_price = position['avg_price']
            entry_date_str = self.entry_prices.get(f"{symbol}_date")
            journal = _get_journal(self.db_path)
            if journal:
                try:
                    journal.record_outcome(
                        symbol=symbol,
                        entry_date=entry_date_str or datetime.now().isoformat(),
                        exit_date=(timestamp or datetime.now()).isoformat(),
                        entry_price=entry_price,
                        exit_price=price,
                        quantity=current_qty,
                        signal_type=self._last_signal_type.get(symbol) if hasattr(self, '_last_signal_type') else None,
                        signal_strength=self._last_signal_strength.get(symbol) if hasattr(self, '_last_signal_strength') else None,
                        exit_reason=self._last_exit_reason.get(symbol, 'signal') if hasattr(self, '_last_exit_reason') else 'signal',
                        market_regime=self._last_regime if hasattr(self, '_last_regime') else None,
                    )
                except Exception as e:
                    logger.debug(f"Journal recording failed: {e}")
            del self.positions[symbol]
            self.entry_prices.pop(symbol, None)
            self.entry_prices.pop(f"{symbol}_date", None)
        else:
            self.positions[symbol]['quantity'] = new_qty
            # Average price remains the same for remaining position

        # Record trade
        trade = {
            'timestamp': timestamp or datetime.now(),
            'type': 'SELL',
            'symbol': symbol,
            'quantity': quantity,
            'price': price,
            'amount_usd': proceeds,
            'cost': cost,
            'net_proceeds': net_proceeds
        }
        self.trade_history.append(trade)

        self._save_state()
        self._save_trade_to_db(trade)
        logger.info(f"Sold {quantity:.6f} {symbol} at ${price:.2f} for ${proceeds:.2f} (cost: ${cost:.2f})")
        return True
    
    def execute_sell_all(self, symbol: str, timestamp: datetime = None):
        """Sell entire position in a symbol"""
        if symbol not in self.positions:
            return False
        
        quantity = self.positions[symbol]['quantity']
        return self.execute_sell(symbol, quantity, timestamp)
    
    def execute_partial_sell(self, symbol: str, percentage: float, timestamp: datetime = None):
        """Sell a percentage (0-1) of a position."""
        if symbol not in self.positions:
            return False
        qty = self.positions[symbol]['quantity'] * percentage
        return self.execute_sell(symbol, qty, timestamp)

    def set_trade_context(self, symbol: str, signal_type: str = None,
                          signal_strength: float = None, exit_reason: str = None,
                          market_regime: str = None):
        """Set context metadata for the next trade (used by journal on position close)."""
        if not hasattr(self, '_last_signal_type'):
            self._last_signal_type = {}
            self._last_signal_strength = {}
            self._last_exit_reason = {}
            self._last_regime = None
        if signal_type is not None:
            self._last_signal_type[symbol] = signal_type
        if signal_strength is not None:
            self._last_signal_strength[symbol] = signal_strength
        if exit_reason is not None:
            self._last_exit_reason[symbol] = exit_reason
        if market_regime is not None:
            self._last_regime = market_regime

    def freeze_portfolio(self, timestamp: datetime = None):
        """Liquidate all positions (circuit breaker activation)."""
        logger.warning("CIRCUIT BREAKER: Freezing portfolio — selling all positions")
        for symbol in list(self.positions.keys()):
            self.execute_sell_all(symbol, timestamp)

    def get_portfolio_value(self) -> float:
        """Calculate total portfolio value (cash + positions)"""
        total_value = self.capital
        
        for symbol, position in self.positions.items():
            price = self.get_current_price(symbol)
            quantity = position['quantity']
            position_value = price * quantity
            total_value += position_value
        
        return total_value
    
    def get_position_value(self, symbol: str) -> float:
        """Get current value of a position"""
        if symbol not in self.positions:
            return 0.0
        
        price = self.get_current_price(symbol)
        quantity = self.positions[symbol]['quantity']
        return price * quantity
    
    def get_position_pnl(self, symbol: str) -> Dict[str, float]:
        """Get P&L for a position"""
        if symbol not in self.positions:
            return {'unrealized': 0, 'unrealized_pct': 0}
        
        position = self.positions[symbol]
        current_price = self.get_current_price(symbol)
        current_value = current_price * position['quantity']
        cost_basis = position['avg_price'] * position['quantity']
        
        unrealized = current_value - cost_basis
        unrealized_pct = (unrealized / cost_basis * 100) if cost_basis > 0 else 0
        
        return {
            'unrealized': unrealized,
            'unrealized_pct': unrealized_pct,
            'current_value': current_value,
            'cost_basis': cost_basis
        }
    
    def rebalance_to_target(self, target_allocation: Dict[str, float]):
        """
        Rebalance portfolio to target allocation
        target_allocation: {symbol: target_weight (0-1)}
        """
        current_value = self.get_portfolio_value()
        
        # Calculate target amounts
        target_amounts = {}
        for symbol, weight in target_allocation.items():
            target_amounts[symbol] = current_value * weight
        
        # First, sell positions not in target or overallocated
        for symbol in list(self.positions.keys()):
            current_pos_value = self.get_position_value(symbol)
            target_amount = target_amounts.get(symbol, 0)
            
            # If symbol not in target or we have too much
            if symbol not in target_allocation or current_pos_value > target_amount:
                # Calculate amount to sell
                if symbol not in target_allocation:
                    # Sell all
                    self.execute_sell_all(symbol)
                else:
                    # Sell excess
                    excess_value = current_pos_value - target_amount
                    if excess_value > 0:
                        price = self.get_current_price(symbol)
                        if price > 0:
                            excess_qty = excess_value / price
                            self.execute_sell(symbol, excess_qty)
        
        # Then, buy underallocated positions
        for symbol, target_amount in target_amounts.items():
            current_pos_value = self.get_position_value(symbol)
            
            if current_pos_value < target_amount:
                # Need to buy more
                buy_amount = target_amount - current_pos_value
                if buy_amount > 10:  # Minimum $10 trade
                    self.execute_buy(symbol, buy_amount)
    
    def initialize_portfolio(self, top_symbols: List[str], weights: List[float] = None):
        """Initialize portfolio with top symbols"""
        if weights is None:
            # Equal weighting
            weight = 1.0 / len(top_symbols)
            weights = [weight] * len(top_symbols)
        
        # Clear any existing positions
        for symbol in list(self.positions.keys()):
            self.execute_sell_all(symbol)
        
        # Reset capital
        self.capital = self.initial_capital
        
        # Create target allocation
        target_allocation = {}
        for symbol, weight in zip(top_symbols, weights):
            target_allocation[symbol] = weight
        
        # Execute initial buys
        self.rebalance_to_target(target_allocation)
        
        logger.info(f"Portfolio initialized with {len(top_symbols)} symbols")
    
    def generate_performance_report(self) -> str:
        """Generate performance report"""
        report_lines = []
        report_lines.append("💰 **Paper Trading Portfolio Performance**")
        report_lines.append(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        report_lines.append("")
        
        # Portfolio summary
        portfolio_value = self.get_portfolio_value()
        total_pnl = portfolio_value - self.initial_capital
        pnl_pct = (total_pnl / self.initial_capital * 100) if self.initial_capital > 0 else 0
        
        report_lines.append("**📊 Portfolio Summary**")
        report_lines.append(f"• Initial capital: ${self.initial_capital:.2f}")
        report_lines.append(f"• Current value: ${portfolio_value:.2f}")
        report_lines.append(f"• Total P&L: ${total_pnl:.2f} ({pnl_pct:.2f}%)")
        report_lines.append(f"• Cash: ${self.capital:.2f}")
        report_lines.append(f"• Positions: {len(self.positions)}")
        report_lines.append("")
        
        # Position details
        if self.positions:
            report_lines.append("**📈 Current Positions**")
            from telegram_fmt import fmt_positions
            pos_list = []
            for symbol, position in sorted(self.positions.items()):
                quantity = position['quantity']
                avg_price = position['avg_price']
                current_price = self.get_current_price(symbol)
                current_value = current_price * quantity
                pnl_pct = ((current_price - avg_price) / avg_price * 100) if avg_price > 0 else 0
                pos_list.append({
                    "symbol": symbol, "quantity": quantity, "avg_price": avg_price,
                    "current_price": current_price, "value": current_value, "pnl_pct": pnl_pct,
                })
            report_lines.append(fmt_positions(pos_list))
            report_lines.append("")
        
        # Trade history
        if self.trade_history:
            report_lines.append("**📋 Recent Trades**")
            recent_trades = sorted(self.trade_history, key=lambda x: x['timestamp'], reverse=True)[:10]
            
            for trade in recent_trades:
                timestamp = trade['timestamp'].strftime('%m/%d %H:%M')
                symbol = trade['symbol']
                trade_type = trade['type']
                quantity = trade['quantity']
                price = trade['price']
                amount = trade.get('amount_usd', 0)
                
                report_lines.append(f"• {timestamp} {trade_type} {quantity:.4f} {symbol} @ ${price:.2f} (${amount:.2f})")
            
            report_lines.append("")
        
        # Recommendations
        report_lines.append("**💡 Next Steps**")
        report_lines.append("• Rebalance weekly based on updated scores")
        report_lines.append("• Monitor position sizes and risk exposure")
        report_lines.append("• Consider stop-losses at 10% drawdown")
        report_lines.append("• Review transaction costs impact")
        report_lines.append("")
        report_lines.append("---")
        report_lines.append("_Paper Trading Simulation • Not real money • For educational purposes_")
        
        return "\n".join(report_lines)


def main():
    """Main execution function"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Paper Trading Portfolio")
    parser.add_argument('--init', action='store_true', help='Initialize portfolio with top 5 cryptos')
    parser.add_argument('--report', action='store_true', help='Generate performance report')
    
    args = parser.parse_args()
    
    print("💰 Paper Trading Portfolio Simulation")
    print("=" * 60)
    
    try:
        portfolio = PaperTradingPortfolio("crypto_data.db", initial_capital=1000.0)
        
        if args.init:
            # Get top 5 symbols from enhanced optimizer
            from enhanced_optimizer import EnhancedPortfolioOptimizer
            optimizer = EnhancedPortfolioOptimizer("crypto_data.db")
            scores_df = optimizer.score_all_symbols()
            
            if not scores_df.empty:
                top_5 = scores_df.head(5)['symbol'].tolist()
                print(f"Initializing portfolio with: {top_5}")
                
                portfolio.initialize_portfolio(top_5)
                
                # Save portfolio state
                portfolio_state = {
                    'initialized': datetime.now().isoformat(),
                    'symbols': top_5,
                    'initial_capital': 1000.0
                }
                with open("paper_portfolio_state.json", "w") as f:
                    json.dump(portfolio_state, f, indent=2)
                
                print("✅ Portfolio initialized")
                
                # Generate initial report
                report = portfolio.generate_performance_report()
                print("\n" + report)
                
                # Save report
                with open("paper_portfolio_report.txt", "w") as f:
                    f.write(report)
                print("\n✅ Report saved to paper_portfolio_report.txt")
            else:
                print("❌ No symbol scores available")
        
        elif args.report:
            # Load existing portfolio or create new
            # For now, just generate report on current state
            report = portfolio.generate_performance_report()
            print("\n" + report)
            
            # Save report
            with open("paper_portfolio_report.txt", "w") as f:
                f.write(report)
            print("\n✅ Report saved to paper_portfolio_report.txt")
        
        else:
            print("Usage:")
            print("  python3 paper_trading.py --init   # Initialize portfolio with top 5 cryptos")
            print("  python3 paper_trading.py --report # Generate performance report")
            
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()