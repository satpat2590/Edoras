# Portfolio Management Strategy & Operational Guide

## Philosophy: Data‑Freshness‑First Approach

**Core principle:** Portfolio management decisions require fresh data. Stale data leads to poor decisions. We guarantee data freshness through tiered ingestion.

---

## Data Ingestion Architecture

### Three‑Tier Data Freshness

#### **Tier 1: Real‑time Signals (<5 min latency)**
- **Source:** Direct Coinbase API calls
- **Frequency:** Event‑driven (signal detection) + scheduled (30 min)
- **Data:** Current prices, RSI extremes, MACD crossovers
- **Purpose:** Immediate buy/sell alerts

#### **Tier 2: Intra‑day Updates (<2 h latency)**
- **Source:** Coinbase candles API (1‑hour timeframe)
- **Frequency:** Every 2 hours (10:30 AM, 12:30 PM, 2:30 PM, 4:30 PM, 6:30 PM EDT)
- **Data:** Latest 1‑hour candlesticks for portfolio symbols
- **Purpose:** Updated technical indicators, trend confirmation

#### **Tier 3: Daily Aggregates (<24 h latency)**
- **Source:** Coinbase candles API (1h, 4h, 1d timeframes)
- **Frequency:** Daily at 8:30 AM EDT
- **Data:** Full historical backfill, all timeframes, expanded universe
- **Purpose:** Portfolio‑level analysis, risk metrics, rebalancing decisions

---

## Performance Optimization Rules

### 1. API Call Management
- **Rate limit:** Coinbase allows 10 requests/second → batch carefully
- **Symbol filtering:** Skip unsupported pairs immediately (no API validation)
- **Request batching:** Fetch multiple timeframes where possible
- **Error handling:** Fail fast, log, continue – no exponential backoff

### 2. Supported Symbols (Hardcoded)
**Portfolio symbols (always supported):**
- `ETH-USD`, `BTC-USD`, `XRP-USD`, `TROLL-USD`
- `BONK-USD`, `FET-USD`, `AMP-USD`, `GRT-USD`

**Top crypto symbols (verified supported):**
- `BNB-USD`, `SOL-USD`, `ADA-USD`, `AVAX-USD`, `DOGE-USD`
- `DOT-USD`, `LINK-USD`, `SHIB-USD`, `LTC-USD`, `UNI-USD`

**Unsupported (skip entirely):**
- `CLV-USD`, `USDC-USD`, `TRX-USD`, `MATIC-USD`

### 3. Database Optimization
- **Indexes:** `(symbol, timeframe, timestamp)` on candlesticks & indicators
- **Vacuum:** Weekly maintenance to reduce bloat
- **Connection pooling:** Single connection per process
- **Batch inserts:** Use transactions for bulk operations

---

## Portfolio Monitoring Checklist

### **Daily Morning Review (8:30 AM EDT)**
1. **Data freshness check**
   - Verify overnight data collection completed
   - Confirm latest candlestick within last 2 h
2. **Portfolio health**
   - Overall P&L vs previous day
   - Concentration risk (top 3 holdings <80%)
   - Cash position adequacy
3. **Market overview**
   - Top/bottom performing cryptos in universe
   - Overall market trend (bullish/neutral/bearish)
4. **Signal summary**
   - Active buy/sell signals across timeframes
   - Signal strength and confidence levels

### **Intra‑day Checks (Every 2 h)**
1. **Price action**
   - Significant moves (>5% in 2 h)
   - Volume spikes (unusual activity)
2. **Technical signals**
   - RSI extremes (<30 oversold, >70 overbought)
   - MACD crossovers (bullish/bearish)
   - Support/resistance breaks
3. **Portfolio alerts**
   - Stop‑loss triggers (individual positions)
   - Take‑profit opportunities
   - Rebalancing needs (drift >5% from target)

### **Weekly Review (Monday 9 AM EDT)**
1. **Performance analysis**
   - Weekly P&L vs benchmarks
   - Sharpe ratio, max drawdown, volatility
2. **Strategy evaluation**
   - Signal accuracy (backtest vs actual)
   - Trade execution quality
   - Transaction cost impact
3. **Rebalancing decision**
   - Score‑based ranking of all symbols
   - Target allocation (equal weight top 5)
   - Trade execution (simulated for paper portfolio)

---

## Signal Hierarchy & Action Framework

### **Priority 1: Strong Buy/Sell (Immediate Action)**
- **Conditions:**
  - RSI < 25 (oversold) OR RSI > 75 (overbought)
  - MACD bullish/bearish crossover with strong histogram
  - Price breaks key support/resistance with high volume
- **Action:** Telegram alert, consider position adjustment

### **Priority 2: Moderate Signal (Monitor Closely)**
- **Conditions:**
  - RSI 25‑30 (near oversold) OR 70‑75 (near overbought)
  - MACD histogram turning (momentum shift)
  - Moving average alignment (price > all MAs)
- **Action:** Add to watchlist, prepare for potential action

### **Priority 3: Weak Signal (Hold & Observe)**
- **Conditions:**
  - Mixed technical indicators
  - Low volume, consolidation pattern
  - No clear trend direction
- **Action:** Maintain current positions, wait for confirmation

---

## Risk Management Rules

### **Position Sizing**
- **Maximum per position:** 25% of portfolio (paper: 20% equal weight)
- **Maximum sector exposure:** 40% (e.g., all DeFi tokens combined)
- **Minimum diversification:** 5 positions minimum

### **Stop‑Loss & Take‑Profit**
- **Stop‑loss:** 10% below entry (trailing after 5% gain)
- **Take‑profit:** 20% above entry (scale out at 15%, 20%, 25%)
- **Breakeven stop:** Move to entry after 5% gain

### **Portfolio‑Level Limits**
- **Maximum drawdown:** 15% (trigger risk‑off mode)
- **Volatility target:** 30‑50% annualized (adjust position size)
- **Correlation limit:** Portfolio beta < 1.5 vs BTC

---

## Operational Procedures

### **Daily Tasks (Automated)**
1. **8:30 AM EDT:** Full data ingestion & analysis
2. **Every 2 h (10:30 AM‑6:30 PM):** Intra‑day updates
3. **12 PM & 4 PM EDT:** Signal alerts check
4. **5 PM EDT:** End‑of‑day paper portfolio report

### **Weekly Tasks (Automated)**
1. **Monday 9 AM EDT:** Paper portfolio rebalancing
2. **Friday 5 PM EDT:** Weekly performance review

### **Manual Overrides**
- **Emergency stop:** Suspend all trading alerts
- **Manual trade:** Execute outside automated signals (document rationale)
- **Strategy adjustment:** Modify scoring weights, thresholds

---

## System Health Monitoring

### **Daily Checks**
- ✅ All scheduled tasks executed (systemd timer status)
- ✅ API connectivity (Coinbase, Telegram)
- ✅ Database integrity (row counts, latest timestamps)
- ✅ Log file rotation (no disk space issues)

### **Weekly Maintenance**
- **Database optimization:** `VACUUM`, `ANALYZE`
- **Log archival:** Compress old logs, retain 30 days
- **Performance review:** Execution times, API success rates
- **Backup verification:** Database backup integrity

---

## Escalation & Decision Framework

### **Automated Decisions (No human approval needed)**
- Routine data collection & analysis
- Signal detection & Telegram alerts
- Paper portfolio rebalancing (weekly)
- Performance reporting

### **Human Notification Required**
- Strong buy/sell signals (Priority 1)
- Portfolio‑level risk breaches (drawdown >10%)
- System failures (data ingestion, API connectivity)
- Unusual market conditions (volatility spike >20%)

### **Human Approval Required**
- Real trading execution (if permissions granted)
- Strategy parameter changes (scoring weights, thresholds)
- Portfolio allocation changes beyond automated rules
- System architecture modifications

---

## Continuous Improvement

### **Weekly Review Items**
1. **Signal accuracy:** Compare predicted vs actual price movement
2. **Execution quality:** Slippage, timing, missed opportunities
3. **Model performance:** Scoring model predictive power
4. **System reliability:** Uptime, error rates, recovery time

### **Monthly Enhancements**
1. **Feature engineering:** New technical indicators
2. **Model refinement:** Adjust scoring weights based on backtest
3. **Process automation:** Reduce manual intervention points
4. **Documentation updates:** Reflect operational improvements

---

## Success Metrics

### **Operational Metrics**
- **Data freshness:** >95% of candlesticks within 2 h of real‑time
- **System uptime:** >99% scheduled task completion
- **Alert accuracy:** >60% profitable signal rate
- **Execution speed:** <5 minute latency for priority signals

### **Performance Metrics (Paper Portfolio)**
- **Annualized return:** Target >20%
- **Sharpe ratio:** Target >1.0
- **Maximum drawdown:** Limit <15%
- **Win rate:** >55% of trades profitable

### **User Experience Metrics**
- **Alert relevance:** >80% of alerts considered useful
- **Report clarity:** Easy to understand and act upon
- **System transparency:** Clear rationale for all decisions
- **Responsiveness:** <30 minute response to issues

---

## Compliance & Documentation

### **Required Documentation**
1. **Trade log:** All simulated trades with rationale
2. **System changes:** Version control for all scripts
3. **Performance reports:** Daily, weekly, monthly summaries
4. **Incident reports:** System failures and resolutions

### **Audit Trail**
- All decisions timestamped and logged
- Signal detection rationale preserved
- Trade execution details (price, quantity, fees)
- Portfolio state snapshots (daily)

---

**Last Updated:** 2026‑03‑09  
**Version:** 1.0  
**Effective Date:** 2026‑03‑10  
**Review Frequency:** Monthly