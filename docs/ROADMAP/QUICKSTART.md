# Real-Time Trading System - Quick Start Guide

**Target:** Full paper-trading system by Friday, March 13

## Current Status (March 11)

✅ **Completed:**
- Enhanced database schema with positions, trades, risk events, portfolio tracking
- WebSocket client for Coinbase (BTC-USD, ETH-USD)
- Real-time tick storage in database
- Basic risk manager (stop-loss, trailing stop, take-profit, circuit breaker)
- Migration from legacy JSON files to database
- Backward compatibility layer

🔄 **In Progress:**
- Integration with existing batch jobs
- Telegram alerts for risk events
- Performance monitoring

📋 **Remaining for Friday:**
- Systemd service setup for 24/7 operation
- Integration testing with existing signals
- Performance validation (backtest parity)
- Documentation and runbooks

## Setup Instructions

### 1. Install Dependencies

```bash
cd /home/satyamini/.openclaw/workspace/projects/edoras

# Install Python packages
pip install websockets aiosqlite --user

# Verify installation
python3 -c "import websockets, aiosqlite; print('Dependencies OK')"
```

### 2. Initialize Enhanced Database

```bash
# Backup existing database
cp crypto_data.db crypto_data.db.backup.$(date +%Y%m%d_%H%M%S)

# Run migration
python3 migration/migrate_to_enhanced.py

# Verify migration
python3 check_migration.py
```

### 3. Test WebSocket Connection

```bash
# Quick test (3 seconds)
python3 test_websocket2.py

# Integration test (10 seconds, stores ticks in DB)
python3 test_integration.py
```

### 4. Run Real-Time Supervisor (Test)

```bash
# Test for 30 seconds
python3 test_supervisor.py
```

### 5. Manual Risk Check

```bash
# Manually run risk manager to check current positions
python3 -c "
import asyncio
import sys
sys.path.insert(0, '.')
from realtime.risk.real_time_risk import RealTimeRiskManager

async def test():
    risk = RealTimeRiskManager()
    await risk.connect_to_db()
    await risk.check_positions()
    await risk.update_portfolio_snapshot()
    await risk.stop()

asyncio.run(test())
"
```

## Production Deployment

### Systemd Service (Recommended for 24/7 operation)

Create `/home/satyamini/.config/systemd/user/coinbase-realtime.service`:

```ini
[Unit]
Description=Coinbase Real-Time Trading System
After=network.target
StartLimitIntervalSec=0

[Service]
Type=simple
Restart=always
RestartSec=10
User=satyamini
WorkingDirectory=/home/satyamini/.openclaw/workspace/projects/edoras
ExecStart=/usr/bin/python3 /home/satyamini/.openclaw/workspace/projects/edoras/realtime/supervisor.py
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
```

Enable and start:

```bash
systemctl --user daemon-reload
systemctl --user enable coinbase-realtime
systemctl --user start coinbase-realtime
systemctl --user status coinbase-realtime
```

### Legacy System Integration

The real-time system runs alongside existing batch jobs. No changes needed to:

- `crypto-daily-analysis.timer` (8:30 AM)
- `crypto-intraday-update.timer` (every 4h)
- `equity-daily-update.timer` (5 AM)
- `signal-trading.timer` (8 AM, 12 PM, 4 PM)

The legacy adapter (`migration/legacy_adapter.py`) updates JSON files for backward compatibility. Run it periodically:

```bash
# Add to crontab (every 5 minutes)
*/5 * * * * cd /home/satyamini/.openclaw/workspace/projects/edoras && python3 migration/legacy_adapter.py
```

## Monitoring

### Database Queries

```sql
-- Current positions
SELECT symbol, quantity, entry_price, current_price, 
       (current_price - entry_price) / entry_price * 100 as pnl_percent
FROM positions 
WHERE status = 'open';

-- Recent risk events
SELECT symbol, event_type, reason, created_at 
FROM risk_events 
ORDER BY created_at DESC LIMIT 10;

-- Portfolio performance
SELECT snapshot_time, total_value, cash, invested, daily_pnl, daily_return
FROM portfolio_performance 
ORDER BY snapshot_time DESC LIMIT 5;

-- Tick volume (last hour)
SELECT symbol, COUNT(*) as tick_count, 
       MIN(price) as low, MAX(price) as high,
       AVG(price) as avg_price
FROM ticks 
WHERE timestamp > datetime('now', '-1 hour')
GROUP BY symbol;
```

### Log Files

```bash
# Supervisor logs (if using systemd)
journalctl --user -u coinbase-realtime -f

# Manual logging
tail -f realtime.log  # If implemented
```

## Risk Configuration

Edit `realtime/config.py`:

```python
# Risk parameters
STOP_LOSS_PCT = 0.10           # 10% stop-loss
TRAILING_STOP_ACTIVATION_PCT = 0.05  # Activate after 5% gain
TRAILING_STOP_PCT = 0.05       # 5% trailing stop
TAKE_PROFIT_LEVELS = [(0.15, 0.33), (0.20, 0.33), (0.25, 1.00)]  # Scale-out
CIRCUIT_BREAKER_PCT = 0.15     # 15% portfolio drawdown

# Execution limits
MAX_ORDER_USD = 50.0           # Max $50 per order
MAX_DAILY_USD = 200.0          # Max $200 per day
ORDER_COOLDOWN_SECONDS = 60    # 1 minute between orders
```

## Troubleshooting

### WebSocket Connection Issues

```bash
# Test connectivity
python3 test_websocket2.py

# Check network
curl -I https://ws-feed.exchange.coinbase.com

# Check firewall
sudo ufw status
```

### Database Issues

```bash
# Check database integrity
sqlite3 crypto_data.db "PRAGMA integrity_check;"

# Repair if needed
cp crypto_data.db crypto_data.db.corrupted
sqlite3 crypto_data.db.corrupted ".dump" | sqlite3 crypto_data.db.new
mv crypto_data.db.new crypto_data.db
```

### Performance Issues

```bash
# Monitor database size
ls -lh crypto_data.db

# Clean old ticks (keep last 7 days)
sqlite3 crypto_data.db "DELETE FROM ticks WHERE timestamp < datetime('now', '-7 days');"

# Reindex
sqlite3 crypto_data.db "REINDEX;"
```

## Next Steps After Friday

1. **Add More Symbols:** Expand from BTC-USD, ETH-USD to full portfolio
2. **Equity Data:** Integrate Polygon.io WebSocket for SPY, QQQ, etc.
3. **News Sentiment:** Real-time news → sentiment → trade pipeline
4. **Advanced Signals:** Machine learning on real-time data
5. **Live Trading:** Gradual migration from paper to live execution

## Support

- Telegram: @satyanabot
- Logs: `journalctl --user -u coinbase-realtime`
- Database: `crypto_data.db` (SQLite)
- Source: `/home/satyamini/.openclaw/workspace/projects/edoras/`

---

**Success Metric for Friday:** Real-time system running 24/7 with BTC-USD and ETH-USD, performing risk checks every 10 seconds, storing ticks in database, and maintaining backward compatibility with existing paper trading system.