"""
Real-time risk manager.
Checks open positions against current prices and triggers exits when risk thresholds are hit.
"""

import asyncio
import logging
import json
from datetime import datetime
import aiosqlite
from typing import Dict, List, Optional

import config

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class RealTimeRiskManager:
    """Monitors positions and triggers risk-based exits"""

    def __init__(self, db_path: str = config.DB_PATH):
        self.db_path = db_path
        self.db_pool = None
        self.running = False
        self._account_ids = None  # Phase 3: cached account IDs

        # Risk parameters
        self.stop_loss_pct = config.STOP_LOSS_PCT
        self.trailing_activation_pct = config.TRAILING_STOP_ACTIVATION_PCT
        self.trailing_stop_pct = config.TRAILING_STOP_PCT
        self.take_profit_levels = config.TAKE_PROFIT_LEVELS
        self.circuit_breaker_pct = config.CIRCUIT_BREAKER_PCT
        
    async def connect_to_db(self):
        """Connect to database"""
        self.db_pool = await aiosqlite.connect(self.db_path)
        # Phase 3: resolve account IDs for the default portfolio
        self._account_ids = config.get_account_ids(config.DEFAULT_PORTFOLIO_ID, db_path=self.db_path)
        logger.info(f"Risk manager connected to database (accounts: {self._account_ids})")
    
    async def check_positions(self):
        """Check all open positions against risk rules"""
        if not self.db_pool:
            await self.connect_to_db()
        
        try:
            # Get all open positions (Phase 3: query via account_ids)
            if self._account_ids:
                placeholders = ','.join('?' * len(self._account_ids))
                pos_sql = f"""
                    SELECT id, symbol, quantity, entry_price, current_price,
                           stop_loss_price, trailing_stop_price, take_profit_levels,
                           account_id
                    FROM positions
                    WHERE status = 'open' AND account_id IN ({placeholders})
                """
                pos_params = self._account_ids
            else:
                pos_sql = """
                    SELECT id, symbol, quantity, entry_price, current_price,
                           stop_loss_price, trailing_stop_price, take_profit_levels,
                           account_id
                    FROM positions
                    WHERE status = 'open' AND portfolio_id = ?
                """
                pos_params = (config.DEFAULT_PORTFOLIO_ID,)
            async with self.db_pool.execute(pos_sql, pos_params) as cursor:
                positions = await cursor.fetchall()

            for position in positions:
                (pos_id, symbol, quantity, entry_price, current_price,
                 stop_loss_price, trailing_stop_price, take_profit_levels_json,
                 pos_account_id) = position
                
                if not current_price:
                    # Get latest price from ticks table
                    async with self.db_pool.execute("""
                        SELECT price FROM ticks 
                        WHERE symbol = ? 
                        ORDER BY timestamp DESC LIMIT 1
                    """, (symbol,)) as cursor:
                        price_row = await cursor.fetchone()
                        if price_row:
                            current_price = price_row[0]
                        else:
                            logger.warning(f"No price data for {symbol}")
                            continue
                
                # Parse take-profit levels
                take_profit_levels = {}
                if take_profit_levels_json:
                    try:
                        take_profit_levels = json.loads(take_profit_levels_json)
                    except:
                        take_profit_levels = {str(level): False for level, _ in self.take_profit_levels}
                
                # Calculate gain/loss
                gain_pct = (current_price / entry_price) - 1
                
                # 1. Check stop-loss
                if not stop_loss_price:
                    stop_loss_price = entry_price * (1 - self.stop_loss_pct)
                
                if current_price <= stop_loss_price:
                    await self.trigger_exit(
                        position_id=pos_id,
                        symbol=symbol,
                        quantity=quantity,
                        price=current_price,
                        exit_type="stop_loss",
                        reason=f"Price ${current_price:.2f} ≤ stop-loss ${stop_loss_price:.2f}",
                        account_id=pos_account_id,
                    )
                    continue
                
                # 2. Check trailing stop
                if gain_pct >= self.trailing_activation_pct:
                    if not trailing_stop_price:
                        trailing_stop_price = current_price * (1 - self.trailing_stop_pct)
                    else:
                        # Update trailing stop to follow price up
                        trailing_stop_price = max(
                            trailing_stop_price,
                            current_price * (1 - self.trailing_stop_pct)
                        )
                    
                    # Ensure trailing stop never goes below entry (breakeven floor)
                    trailing_stop_price = max(trailing_stop_price, entry_price)
                    
                    # Update trailing stop in database
                    await self.db_pool.execute("""
                        UPDATE positions SET trailing_stop_price = ? WHERE id = ?
                    """, (trailing_stop_price, pos_id))
                    
                    if current_price <= trailing_stop_price:
                        await self.trigger_exit(
                            position_id=pos_id,
                            symbol=symbol,
                            quantity=quantity,
                            price=current_price,
                            exit_type="trailing_stop",
                            reason=f"Price ${current_price:.2f} ≤ trailing stop ${trailing_stop_price:.2f}",
                            account_id=pos_account_id,
                        )
                        continue
                
                # 3. Check take-profit levels
                for level, sell_pct in self.take_profit_levels:
                    level_str = str(level)
                    if gain_pct >= level and not take_profit_levels.get(level_str, False):
                        # Partial exit
                        sell_quantity = quantity * sell_pct
                        await self.trigger_partial_exit(
                            position_id=pos_id,
                            symbol=symbol,
                            quantity=sell_quantity,
                            price=current_price,
                            exit_type="take_profit",
                            reason=f"Take-profit at +{level*100:.0f}% (gain: +{gain_pct*100:.1f}%)",
                            sell_pct=sell_pct,
                            account_id=pos_account_id,
                        )
                        
                        # Mark level as triggered
                        take_profit_levels[level_str] = True
                        await self.db_pool.execute("""
                            UPDATE positions SET take_profit_levels = ? WHERE id = ?
                        """, (json.dumps(take_profit_levels), pos_id))
            
            await self.db_pool.commit()
            
            # 4. Check portfolio-wide circuit breaker
            await self.check_circuit_breaker()
            
        except Exception as e:
            logger.error(f"Error checking positions: {e}")
    
    async def trigger_exit(self, position_id: int, symbol: str, quantity: float,
                          price: float, exit_type: str, reason: str,
                          account_id: int = None):
        """Trigger full exit of a position"""
        logger.info(f"🚨 {exit_type}: {symbol} {quantity:.6f} @ ${price:.2f} - {reason}")

        try:
            # Create trade record (trader_id=4 is Risk Engine)
            await self.db_pool.execute("""
                INSERT INTO trades (
                    portfolio_id, account_id, symbol, side, quantity, price, amount_usd, fee,
                    order_type, status, risk_event_type, trader_id, created_at
                ) VALUES (?, ?, ?, 'SELL', ?, ?, ?, 0.0, 'market', 'filled', ?, 4, CURRENT_TIMESTAMP)
            """, (
                config.DEFAULT_PORTFOLIO_ID, account_id, symbol, quantity, price, quantity * price,
                exit_type
            ))

            # Update position status
            await self.db_pool.execute("""
                UPDATE positions SET 
                    status = 'closed',
                    exit_price = ?,
                    exit_time = CURRENT_TIMESTAMP,
                    pnl = (? - entry_price) * quantity,
                    pnl_percent = ((? / entry_price) - 1) * 100,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (price, price, price, position_id))
            
            # Record risk event
            await self.db_pool.execute("""
                INSERT INTO risk_events (
                    portfolio_id, symbol, event_type, trigger_price, current_price,
                    quantity, action_taken, reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'full_exit', ?, CURRENT_TIMESTAMP)
            """, (
                config.DEFAULT_PORTFOLIO_ID, symbol, exit_type, price, price,
                quantity, reason
            ))
            
            await self.db_pool.commit()
            
            # TODO: Send Telegram alert
            
        except Exception as e:
            logger.error(f"Failed to execute exit: {e}")
    
    async def trigger_partial_exit(self, position_id: int, symbol: str, quantity: float,
                                 price: float, exit_type: str, reason: str, sell_pct: float,
                                 account_id: int = None):
        """Trigger partial exit (take-profit)"""
        logger.info(f"🎯 Partial {exit_type}: {symbol} {quantity:.6f} ({sell_pct*100:.0f}%) @ ${price:.2f}")

        try:
            # Create trade record (trader_id=4 is Risk Engine)
            await self.db_pool.execute("""
                INSERT INTO trades (
                    portfolio_id, account_id, symbol, side, quantity, price, amount_usd, fee,
                    order_type, status, risk_event_type, trader_id, created_at
                ) VALUES (?, ?, ?, 'SELL', ?, ?, ?, 0.0, 'market', 'filled', ?, 4, CURRENT_TIMESTAMP)
            """, (
                config.DEFAULT_PORTFOLIO_ID, account_id, symbol, quantity, price, quantity * price,
                exit_type
            ))

            # Update position quantity
            await self.db_pool.execute("""
                UPDATE positions SET 
                    quantity = quantity - ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (quantity, position_id))
            
            # Check if position is now negligible
            async with self.db_pool.execute("""
                SELECT quantity FROM positions WHERE id = ?
            """, (position_id,)) as cursor:
                remaining = await cursor.fetchone()
            
            if remaining and remaining[0] < 0.000001:  # Less than 0.000001 BTC
                await self.db_pool.execute("""
                    UPDATE positions SET 
                        status = 'closed',
                        exit_price = ?,
                        exit_time = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (price, position_id))
                action = "full_exit"
            else:
                action = "partial_exit"
            
            # Record risk event
            await self.db_pool.execute("""
                INSERT INTO risk_events (
                    portfolio_id, symbol, event_type, trigger_price, current_price,
                    quantity, action_taken, reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (
                config.DEFAULT_PORTFOLIO_ID, symbol, exit_type, price, price,
                quantity, action, reason
            ))
            
            await self.db_pool.commit()
            
            # TODO: Send Telegram alert
            
        except Exception as e:
            logger.error(f"Failed to execute partial exit: {e}")
    
    async def check_circuit_breaker(self):
        """Check portfolio-wide circuit breaker"""
        try:
            # Get portfolio value
            async with self.db_pool.execute("""
                SELECT total_value FROM portfolio_performance 
                WHERE portfolio_id = ?
                ORDER BY snapshot_time DESC LIMIT 1
            """, (config.DEFAULT_PORTFOLIO_ID,)) as cursor:
                result = await cursor.fetchone()
            
            if not result:
                return
            
            current_value = result[0]
            
            # Get peak value (simplified - would track historically in production)
            async with self.db_pool.execute("""
                SELECT MAX(total_value) FROM portfolio_performance 
                WHERE portfolio_id = ?
            """, (config.DEFAULT_PORTFOLIO_ID,)) as cursor:
                peak_result = await cursor.fetchone()
            
            peak_value = peak_result[0] if peak_result and peak_result[0] else current_value
            
            if peak_value == 0:
                return
            
            drawdown = (peak_value - current_value) / peak_value
            
            if drawdown >= self.circuit_breaker_pct:
                logger.warning(f"⚠️ Circuit breaker threshold reached: {drawdown*100:.1f}% drawdown")
                
                # Liquidate all positions (Phase 3: query via account_ids)
                if self._account_ids:
                    placeholders = ','.join('?' * len(self._account_ids))
                    cb_sql = f"SELECT id, symbol, quantity FROM positions WHERE status = 'open' AND account_id IN ({placeholders})"
                    cb_params = self._account_ids
                else:
                    cb_sql = "SELECT id, symbol, quantity FROM positions WHERE status = 'open' AND portfolio_id = ?"
                    cb_params = (config.DEFAULT_PORTFOLIO_ID,)
                async with self.db_pool.execute(cb_sql, cb_params) as cursor:
                    positions = await cursor.fetchall()
                
                for pos_id, symbol, quantity in positions:
                    # Get current price
                    async with self.db_pool.execute("""
                        SELECT price FROM ticks 
                        WHERE symbol = ? 
                        ORDER BY timestamp DESC LIMIT 1
                    """, (symbol,)) as cursor:
                        price_row = await cursor.fetchone()
                    
                    if price_row:
                        price = price_row[0]
                        await self.trigger_exit(
                            position_id=pos_id,
                            symbol=symbol,
                            quantity=quantity,
                            price=price,
                            exit_type="circuit_breaker",
                            reason=f"Portfolio drawdown {drawdown*100:.1f}% ≥ {self.circuit_breaker_pct*100:.0f}%"
                        )
                
                # Record circuit breaker event
                await self.db_pool.execute("""
                    INSERT INTO risk_events (
                        portfolio_id, event_type, current_price, action_taken, reason, created_at
                    ) VALUES (?, 'circuit_breaker', ?, 'liquidated_all', ?, CURRENT_TIMESTAMP)
                """, (config.DEFAULT_PORTFOLIO_ID, current_value, 
                      f"Drawdown {drawdown*100:.1f}% triggered circuit breaker"))
                
                await self.db_pool.commit()
                
                # TODO: Send critical Telegram alert
                
        except Exception as e:
            logger.error(f"Error checking circuit breaker: {e}")
    
    async def update_portfolio_snapshot(self):
        """Update portfolio performance snapshot"""
        try:
            # Calculate current portfolio value (Phase 3: query via account_ids)
            if self._account_ids:
                placeholders = ','.join('?' * len(self._account_ids))
                snap_sql = f"""
                    SELECT
                        COALESCE(SUM(p.quantity * p.current_price), 0) as invested,
                        (SELECT initial_capital FROM portfolios WHERE id = ?) as initial_capital
                    FROM positions p
                    WHERE p.account_id IN ({placeholders}) AND p.status = 'open'
                """
                snap_params = [config.DEFAULT_PORTFOLIO_ID] + self._account_ids
            else:
                snap_sql = """
                    SELECT
                        COALESCE(SUM(p.quantity * p.current_price), 0) as invested,
                        (SELECT initial_capital FROM portfolios WHERE id = ?) as initial_capital
                    FROM positions p
                    WHERE p.portfolio_id = ? AND p.status = 'open'
                """
                snap_params = (config.DEFAULT_PORTFOLIO_ID, config.DEFAULT_PORTFOLIO_ID)
            async with self.db_pool.execute(snap_sql, snap_params) as cursor:
                result = await cursor.fetchone()
            
            if not result:
                return
            
            invested = result[0] if result[0] else 0
            initial_capital = result[1] if result[1] else config.INITIAL_CAPITAL
            
            cash = initial_capital - invested
            total_value = invested + cash
            
            # Get previous snapshot for daily P&L calculation
            async with self.db_pool.execute("""
                SELECT total_value FROM portfolio_performance 
                WHERE portfolio_id = ?
                ORDER BY snapshot_time DESC LIMIT 1
            """, (config.DEFAULT_PORTFOLIO_ID,)) as cursor:
                prev_result = await cursor.fetchone()
            
            prev_value = prev_result[0] if prev_result else total_value
            daily_pnl = total_value - prev_value
            daily_return = (daily_pnl / prev_value * 100) if prev_value != 0 else 0
            
            # Create new snapshot (Phase 3: count via account_ids)
            if self._account_ids:
                placeholders = ','.join('?' * len(self._account_ids))
                count_sql = f"SELECT COUNT(*) FROM positions WHERE account_id IN ({placeholders}) AND status = 'open'"
                count_row = await (await self.db_pool.execute(count_sql, self._account_ids)).fetchone()
                pos_count = count_row[0] if count_row else 0
            else:
                count_row = await (await self.db_pool.execute(
                    "SELECT COUNT(*) FROM positions WHERE portfolio_id = ? AND status = 'open'",
                    (config.DEFAULT_PORTFOLIO_ID,),
                )).fetchone()
                pos_count = count_row[0] if count_row else 0

            await self.db_pool.execute("""
                INSERT INTO portfolio_performance (
                    portfolio_id, snapshot_time, total_value, cash, invested,
                    daily_pnl, daily_return, positions_count, created_at
                ) VALUES (?, datetime('now'), ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (
                config.DEFAULT_PORTFOLIO_ID, total_value, cash, invested,
                daily_pnl, daily_return, pos_count
            ))
            
            await self.db_pool.commit()
            
            logger.debug(f"Portfolio snapshot: total=${total_value:.2f}, cash=${cash:.2f}, invested=${invested:.2f}")
            
        except Exception as e:
            logger.error(f"Error updating portfolio snapshot: {e}")
    
    async def run(self):
        """Main risk manager loop"""
        await self.connect_to_db()
        self.running = True
        
        logger.info("RealTimeRiskManager started")
        
        while self.running:
            try:
                await self.check_positions()
                await self.update_portfolio_snapshot()
                
                # Sleep for 10 seconds between checks
                await asyncio.sleep(10)
                
            except Exception as e:
                logger.error(f"Risk manager error: {e}")
                await asyncio.sleep(30)  # Longer sleep on error
    
    async def stop(self):
        """Stop risk manager"""
        self.running = False
        if self.db_pool:
            await self.db_pool.close()
        logger.info("RealTimeRiskManager stopped")

async def main():
    """Test the risk manager"""
    risk_manager = RealTimeRiskManager()
    
    try:
        await risk_manager.run()
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
    finally:
        await risk_manager.stop()

if __name__ == "__main__":
    asyncio.run(main())