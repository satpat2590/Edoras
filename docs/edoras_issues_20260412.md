# Edoras System Status & Issues Report
*Generated: 2026‑04‑12 20:56 EDT*  
*Last Updated: 2026‑04‑12*  
*Report ID: `edoras‑status‑20260412`*

---

## **📋 Executive Summary**

Edoras, the multi‑strategy crypto trading system, is **partially operational** with **critical data freshness failures**. While daily data collection and paper portfolio reporting work, intra‑day crypto updates are broken, causing the system to trade on stale data (5–9 h old 1h/4h candles). This report documents the architecture, current state, and actionable troubleshooting steps.

---

## **🏗️ System Architecture Overview**

### **Project Layout**
```
/home/satyamini/edoras/
├── src/edoras/                    # Core Python package (install via pip install -e .)
│   ├── config.py                 # Central configuration (DB path, thresholds)
│   ├── core/                     # Signal trading, paper trading, risk management
│   ├── data/                     # Data collectors, indicator calculator, freshness monitor
│   ├── llm/                      # LLM‑powered trading agent (5‑tier fallback)
│   ├── dex/                      # DEX execution (Bankr integration)
│   ├── scoring/                  # Portfolio optimization, strategy tracking
│   ├── reports/                  # Report engines, Telegram formatting
│   ├── cli/                      # CLI (`cli.py health`, `signals`, `trades`)
│   └── backtest/                 # 10 backtested strategies
├── crypto_data.db                # SQLite database (309 MB, 53 tables)
├── scripts/                      # Shell wrappers for systemd services
└── ~/.config/systemd/user/      # Systemd user‑level service/timer files
```

### **Python Environment**
- **Primary interpreter**: `/home/satyamini/miniconda3/bin/python3` (Python 3.12.1)
- **Package**: `edoras` installed in editable mode (`pip install -e .`)
- **Environment variables**: `PYTHONPATH=/home/satyamini/edoras/src`
- **Credentials**: `~/.config/coinbase.env` (Coinbase API key + EC private key)

### **Systemd Orchestration**
Edoras runs via **23 systemd user‑level timers**. Key services:

| Service/Timer | Purpose | Schedule | Current Status |
|--------------|---------|----------|----------------|
| `crypto‑daily‑analysis.timer` | Daily candle updates | 08:32 daily | ✅ **Working** (0.2 h stale) |
| `crypto‑intraday‑update.timer` | 1h/4h intra‑day updates | Every 2 h during market hours | ❌ **Broken** (timeout) |
| `data‑freshness‑monitor.timer` | Alert on stale feeds | Every 15 min | ⚠️ **Partial** (import fixed) |
| `crypto‑signal‑trading.timer` | Signal generation → paper trades | 00:09, 04:08, 08:10, 16:10, 20:07 | ✅ **Working** (on stale data) |
| `paper‑portfolio‑report.timer` | Daily portfolio report | 17:00 daily | ✅ **Working** |
| `trading‑agent.timer` | LLM‑powered trading decisions | Disabled per user preference | ✅ **Disabled** |

---

## **🔴 CRITICAL ISSUES (P0)**

### **1. Crypto Intra‑day Updates Failing**
**Impact**: Trading signals generated from **5–9 h stale data**, invalidating regime detection and momentum strategies.

**Symptoms**:
- `crypto‑intraday‑update.service` fails with timeout (2 min → increased to 5 min)
- Manual test reveals `no such table: candlesticks` error
- Coinbase API calls succeed, but SQLite insertions fail
- Data freshness monitor reports 54 OK, 39 stale feeds

**Root Cause**:
The `IntradayUpdater` class in `src/edoras/data/intraday_update.py` attempts to insert into a `candlesticks` table that either:
1. Does not exist in the database file it opens
2. Exists under a different name (schema shows `candlesticks` table exists)
3. Is connecting to wrong DB file (path mismatch)

**Evidence**:
```bash
$ python3 -m edoras.data.intraday_update --symbol BTC‑USD 2>&1 | grep -A2 -B2 "no such table"
2026‑04‑12 20:55:19,389 - __main__ - ERROR - Error inserting candlestick for BTC‑USD: no such table: candlesticks
...
2026‑04‑12 20:55:19,392 - __main__ - ERROR - Error calculating indicators for BTC‑USD 1h: ... no such table: candlesticks
```

**Database Schema**:
```sql
-- Table exists according to PRAGMA:
sqlite> PRAGMA table_info(candlesticks);
0|id|INTEGER|0||1
1|symbol|TEXT|1||0
2|timeframe|TEXT|1||0
3|timestamp|INTEGER|1||0
4|open|REAL|1||0
5|high|REAL|1||0
6|low|REAL|1||0
7|close|REAL|1||0
8|volume|REAL|1||0
9|created_at|TIMESTAMP|0|CURRENT_TIMESTAMP|0
```

**Action Required**:
1. Patch `intraday_update.py` to log absolute DB path on startup
2. Verify connection to correct `crypto_data.db` (not a temporary/in‑memory DB)
3. Check SQLite journal mode (`‑wal`, `‑shm` files present → WAL mode active)
4. Ensure table‑name case sensitivity (SQLite is case‑insensitive but preserves)

---

### **2. Data Freshness Violations**
**Current Staleness** (as of 2026‑04‑12 20:56 EDT):

| Symbol | Timeframe | Latest (UTC) | Hours Ago | Threshold |
|--------|-----------|--------------|-----------|-----------|
| ADA‑USD | 1h | 2026‑04‑12 23:00 | 5.8 h | 2 h |
| ADA‑USD | 4h | 2026‑04‑12 20:00 | 8.8 h | 6 h |
| ADA‑USD | 1d | 2026‑04‑12 00:00 | 28.8 h | 24 h |
| *[All crypto portfolio symbols show same staleness]* | | | | |

**Equity symbols** (AAPL, MSFT, etc.) are 245–265 h stale — expected, not critical for crypto trading.

**Immediate Risk**: Trading signals based on stale data → systematic mis‑pricing, incorrect regime classification, invalid momentum/mean‑reversion triggers.

---

## **⚠️ PARTIAL ISSUES (P1)**

### **3. Data Freshness Monitor Environment**
**Status**: Fixed import errors (PATH/PYTHONPATH corrected), but reports stale feeds correctly.

**Files**:
- Service: `~/.config/systemd/user/data‑freshness‑monitor.service`
- Script: `~/edoras/run_data_freshness_monitor.sh` (updated to include miniconda3 path)

**Test Command**:
```bash
cd ~/edoras && bash run_data_freshness_monitor.sh 2>&1 | head -5
```

---

### **4. Equity Data Collection**
**Status**: Expected failure — equity feeds not updated since 2026‑04‑02. Not critical unless multi‑asset portfolio expansion planned.

**Action**: Can disable equity‑related alerts in `data_freshness_monitor.py` or adjust thresholds.

---

## **✅ WORKING COMPONENTS**

### **5. Daily Data Collection**
- `crypto‑daily‑analysis.service` runs successfully
- Daily candles: 0.2 h stale (within 24 h threshold)
- Fixed by ensuring `edoras` package importable in miniconda3 environment

### **6. Paper Portfolio Reporting**
- Generates daily report at 17:00 EDT
- Archives as `reports/paper_YYYYMMDD.txt`
- Latest (2026‑04‑12): **$913.58** (−8.64% from $1,000 initial), 7 positions

### **7. Signal Generation & Paper Trading**
- **Signals (24h)**: 11 generated, 4 executed, 7 skipped
- **Trades (24h)**: 5 trades (BUY AMP/BNB/BONK/UNI, SELL AMP)
- Regime‑aware routing active
- Polymarket overlay firing (SELL ETH‑USD signals)

### **8. LLM Trading Disabled**
- `trading‑agent.timer` stopped & disabled per user philosophy
- LLM now only for qualitative overlay (not binary BUY/SELL decisions)

---

## **🔧 TROUBLESHOOTING GUIDE**

### **For External Agents / Debuggers**

#### **A. Verify System State**
```bash
# 1. Check all timers
systemctl --user list‑timers --all

# 2. Check service status
systemctl --user status crypto‑intraday‑update.service --no‑pager -l
systemctl --user status data‑freshness‑monitor.service --no‑pager -l

# 3. Database verification
sqlite3 ~/edoras/crypto_data.db "SELECT COUNT(*) FROM candlesticks WHERE symbol='ETH‑USD' AND timeframe='1h';"
sqlite3 ~/edoras/crypto_data.db "SELECT MAX(timestamp) FROM candlesticks WHERE symbol='ETH‑USD' AND timeframe='1h';"
```

#### **B. Test Intra‑day Update Manually**
```bash
cd ~/edoras
export PATH="/home/satyamini/miniconda3/bin:$PATH"
export PYTHONPATH="/home/satyamini/edoras/src:$PYTHONPATH"
source ~/.config/coinbase.env 2>/dev/null

# Test single symbol
python3 -m edoras.data.intraday_update --symbol ETH‑USD 2>&1 | tail -30
```

#### **C. Patch IntradayUpdater for Debugging**
Add to `src/edoras/data/intraday_update.py` `__init__` method:
```python
import os
logger.info(f"DB path: {os.path.abspath(self.db_path)}")
logger.info(f"DB exists: {os.path.exists(self.db_path)}")
if os.path.exists(self.db_path):
    import sqlite3
    conn = sqlite3.connect(self.db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%candle%';")
    tables = cursor.fetchall()
    logger.info(f"Candle‑like tables: {tables}")
    conn.close()
```

#### **D. Check Coinbase API Connectivity**
```python
import os, time
from coinbase.rest import RESTClient
# Load environment from ~/.config/coinbase.env
client = RESTClient()
end_time = int(time.time())
start_time = end_time - 48*3600
candles = client.get_candles(product_id="ETH‑USD", start=str(start_time), 
                             end=str(end_time), granularity='ONE_HOUR')
print(f"Response: {type(candles)}, has candles: {hasattr(candles, 'candles')}")
```

#### **E. Emergency Stop Trading**
If data freshness cannot be resolved immediately:
```bash
systemctl --user stop crypto‑signal‑trading.timer
systemctl --user disable crypto‑signal‑trading.timer
```

---

## **📈 PERFORMANCE METRICS**

### **Portfolio Summary**
- **Initial capital**: $1,000.00
- **Current value**: $913.58 (−8.64%)
- **Cash**: $5.87
- **Positions**: 7
- **24h trade volume**: $174.97 bought, $46.67 sold
- **Net flow**: −$128.30

### **Signal Statistics (Last 24h)**
| Time | Executed | Action | Symbol | Strength | Strategy |
|------|----------|--------|--------|----------|----------|
| 00:09 | YES | BUY | AMP‑USD | 70.0 | RegimeAware |
| 00:09 | YES | BUY | BNB‑USD | 80.0 | RegimeAware |
| 20:07 | YES | BUY | BONK‑USD | 70.0 | RegimeAware |
| 20:07 | YES | BUY | UNI‑USD | 70.0 | RegimeAware |

### **Data Freshness Summary**
- Crypto 1h: 5.8 h stale (threshold 2 h) → **VIOLATION**
- Crypto 4h: 8.8 h stale (threshold 6 h) → **VIOLATION**
- Crypto 1d: 28.8 h stale (threshold 24 h) → **VIOLATION**
- Equity: 245–265 h stale (expected)

---

## **🚀 RECOMMENDED ACTION PLAN**

### **Phase 1: Immediate (Today)**
1. **Debug intra‑day DB issue**  
   - Patch `intraday_update.py` with DB‑path logging  
   - Confirm connection to correct `crypto_data.db`  
   - Verify table‑name case/pluralization  

2. **Test fix with single symbol**  
   - Run `python3 -m edoras.data.intraday_update --symbol ETH‑USD`  
   - Check SQLite for new 1h candle  

3. **If fix successful**  
   - Run full intra‑day update manually  
   - Verify data freshness within 2 h  
   - Re‑enable `crypto‑intraday‑update.timer`  

4. **If fix fails**  
   - Pause trading: `systemctl --user stop crypto‑signal‑trading.timer`  
   - Investigate SQLite schema mismatch  

### **Phase 2: Short‑term (This Week)**
1. **Add stale‑data circuit breaker** to `signal_trading.py`  
2. **Review paper trades** executed on stale data for potential reversal  
3. **Plan LLM integration refactor** (qualitative overlay only)  
4. **Database optimization** (VACUUM, incremental indicator calculation)  

### **Phase 3: Medium‑term (Next 2 Weeks)**
1. **Implement caching** for regime classification, correlation matrices  
2. **Async pipeline** for parallel data collection  
3. **Performance monitoring dashboard**  
4. **Documentation update** (ARCHITECTURE.md, RUNBOOK.md)  

---

## **📁 FILE REFERENCE**

| File | Purpose | Status |
|------|---------|--------|
| `~/edoras/src/edoras/data/intraday_update.py` | Intra‑day crypto updates | ❌ **Broken** |
| `~/edoras/src/edoras/data/data_freshness_monitor.py` | Freshness checks | ⚠️ **Partial** |
| `~/edoras/src/edoras/core/signal_trading.py` | Signal generation | ✅ **Working** |
| `~/edoras/src/edoras/core/paper_trading.py` | Paper execution | ✅ **Working** |
| `~/edoras/src/edoras/llm/trading_agent.py` | LLM agent | ✅ **Disabled** |
| `~/edoras/crypto_data.db` | SQLite database | ✅ **Exists** |
| `~/.config/systemd/user/crypto‑intraday‑update.service` | Intra‑day service | ❌ **Timeout** |
| `~/.config/systemd/user/data‑freshness‑monitor.service` | Freshness service | ✅ **Fixed** |
| `~/edoras/run_data_freshness_monitor.sh` | Freshness wrapper | ✅ **Fixed** |

---

## **🧠 CONTEXT FOR EXTERNAL AGENTS**

- **User preference**: LLMs should provide **qualitative research overlay**, not binary BUY/SELL decisions  
- **Python environment**: Must use `/home/satyamini/miniconda3/bin/python3`  
- **Package imports**: `edoras` installed via `pip install -e .` in `~/edoras`  
- **Credentials**: `~/.config/coinbase.env` contains API key + EC private key (newline fix required)  
- **Telegram integration**: OpenClaw CLI used for alerts (`send_telegram` in `lib_telegram.sh`)  
- **Database**: SQLite with WAL mode active (`‑wal`, `‑shm` files present)  

---

## **📞 SUPPORT CONTACTS**

- **System Owner**: Satyam Patel (Satyam)  
- **Primary AI Agent**: Satya (Hermes Agent v0.8.0)  
- **Backup Documentation**: `~/edoras/SYSTEM_REFERENCE.md`, `~/edoras/AGENTS.md`  
- **Issue Tracking**: This markdown file (`~/edoras/edoras_issues_20260412.md`)

---

*Report generated automatically by Satya (Hermes Agent) based on system inspection.*  
*Next update scheduled when intra‑day data freshness restored.*