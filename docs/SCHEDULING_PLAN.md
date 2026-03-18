# Scheduling & Operational Plan

## Philosophy: Hybrid Alert + Periodic Check System

We implement a **three‑layer approach**:
1. **Daily Morning Check** – Comprehensive analysis at market open
2. **Intra‑day Signal Alerts** – Event‑driven notifications for strong opportunities
3. **Weekly Systematic Rebalancing** – Scheduled portfolio adjustments

This balances **responsiveness** (alerts for urgent signals) with **thoroughness** (daily deep analysis) and **discipline** (weekly rebalancing).

---

## Schedule Overview (EDT Timezone)

### Daily Operations
| Time (EDT) | Task | Purpose | Output |
|------------|------|---------|--------|
| **8:30 AM** | Technical Analysis | Market overview, top/bottom performers | Telegram report |
| **9:00 AM** | Portfolio Snapshot | Current holdings, P&L, risk metrics | Telegram report |
| **12:00 PM** | Signal Check | Mid‑day signal review | Alert if strong signals |
| **4:00 PM** | Signal Check | Afternoon signal review | Alert if strong signals |
| **5:00 PM** | Paper Portfolio Report | End‑of‑day performance | Telegram report |

### Weekly Operations
| Day | Time (EDT) | Task | Purpose |
|-----|------------|------|---------|
| **Monday** | 9:00 AM | Paper Portfolio Rebalancing | Adjust to top 5 cryptos |
| **Friday** | 5:00 PM | Weekly Performance Review | Summary of week's performance |

### Real‑time Alerts (Event‑driven)
| Condition | Trigger | Action |
|-----------|---------|--------|
| **RSI < 30** (oversold) | Any time during market hours | Buy alert |
| **RSI > 70** (overbought) | Any time during market hours | Sell alert |
| **MACD Bullish Crossover** | Any time during market hours | Buy alert |
| **MACD Bearish Crossover** | Any time during market hours | Sell alert |
| **Portfolio Concentration > 80%** | Daily check | Risk alert |
| **Daily Loss > 5%** | End‑of‑day check | Performance alert |

---

## Implementation Details

### 1. Daily Morning Check (8:30 AM EDT)
**Script**: `run_daily_analysis.sh`
**Purpose**: Full market analysis to start the day
**Outputs**:
- Technical analysis report (top 10/bottom 5 cryptos)
- Portfolio risk metrics (Sharpe, drawdown, VaR)
- Signal summary (buy/sell/neutral counts)

**Telegram Message**: "📊 Morning Market Analysis" (sent to the operator)

### 2. Portfolio Snapshot (9:00 AM EDT)  
**Script**: `daily_report_cron.sh`
**Purpose**: Portfolio health check
**Outputs**:
- Current holdings and values
- Daily P&L
- Concentration risk
- Cash position

**Telegram Message**: "💰 Portfolio Snapshot" (sent to the operator)

### 3. Signal Checks (12 PM, 4 PM EDT)
**Script**: `run_signal_alerts.sh`
**Purpose**: Intra‑day opportunity detection
**Logic**: Check for:
- RSI extremes (<30 or >70)
- MACD crossovers
- Strong trend signals (ADX > 25 with price movement)
- Volume spikes with price movement

**Alert Threshold**: Only alert if signal strength > 80/100
**Deduplication**: 24‑hour suppression for same symbol/signal

**Telegram Message**: "🚨 Signal Alert: [SYMBOL] [BUY/SELL]" (sent to the operator)

### 4. Paper Portfolio Report (5:00 PM EDT)
**Script**: `run_paper_report.sh` (to be created)
**Purpose**: End‑of‑day performance tracking
**Outputs**:
- Paper portfolio value and P&L
- Position details
- Trade history for the day
- Tomorrow's watchlist

**Telegram Message**: "📈 Paper Portfolio Report" (sent to the operator)

### 5. Weekly Rebalancing (Monday 9:00 AM EDT)
**Script**: `run_paper_rebalancing.sh` (to be created)
**Purpose**: Systematic portfolio adjustment
**Logic**:
1. Re‑score all symbols in expanded universe
2. Select top 5 by advanced score
3. Rebalance to equal weight (20% each)
4. Execute simulated trades with 0.1% transaction cost
5. Report changes

**Telegram Message**: "🔄 Weekly Rebalancing Complete" (sent to the operator)

### 6. Real‑time Alert Monitoring
**Script**: Enhanced `signal_alerts.py` with more frequent checks
**Schedule**: Every 30 minutes during market hours (9 AM – 7 PM EDT)
**Optimization**: Only check symbols with recent price movement (>2% change)

---

## Alert Strategy: When to Notify vs When to Act Automatically

### Notify Human (the operator) When:
1. **Strong buy/sell signals** (score > 85 or < 15)
2. **Portfolio concentration risk** > 80% in top 3 holdings
3. **Daily loss** > 5% in paper portfolio
4. **System issues** (API failures, data gaps)
5. **Weekly rebalancing decisions** (for review before execution)

### Act Automatically (Paper Trading) When:
1. **Weekly rebalancing** (Monday 9 AM) – already scheduled
2. **Stop‑loss triggers** (10% loss from entry) – to be implemented
3. **Take‑profit triggers** (20% gain from entry) – to be implemented

### My Role (Aleph) as Operator:
- **Monitor alerts** and bring important ones to the operator's attention
- **Execute scheduled tasks** (daily reports, weekly rebalancing)
- **Maintain system health** (data collection, error recovery)
- **Provide analysis** when requested or when interesting patterns emerge

---

## Cron Schedule (UTC Times)

```bash
# Daily technical analysis (8:30 AM EDT / 12:30 UTC)
30 12 * * * /home/satyamini/.openclaw/workspace/projects/edoras/run_daily_analysis.sh

# Portfolio snapshot (9:00 AM EDT / 13:00 UTC)
0 13 * * * /home/satyamini/.openclaw/workspace/projects/edoras/daily_report_cron.sh

# Signal checks (12 PM, 4 PM EDT / 16, 20 UTC)
0 16,20 * * * /home/satyamini/.openclaw/workspace/projects/edoras/run_signal_alerts.sh

# Paper portfolio report (5:00 PM EDT / 21:00 UTC)
0 21 * * * /home/satyamini/.openclaw/workspace/projects/edoras/run_paper_report.sh

# Weekly paper portfolio rebalancing (Monday 9:00 AM EDT / 13:00 UTC)
0 13 * * 1 /home/satyamini/.openclaw/workspace/projects/edoras/run_paper_rebalancing.sh

# Price/signal alerts every 30 min during market hours (9 AM‑7 PM EDT / 13‑23 UTC)
*/30 13-23 * * * /home/satyamini/.openclaw/workspace/projects/edoras/price_alerts_cron.sh

# Random portfolio reports (1‑3x daily between 8 AM‑8 PM EDT)
0 7 * * * /home/satyamini/.openclaw/workspace/projects/edoras/schedule_daily.sh
```

---

## New Scripts to Create

### 1. `run_paper_report.sh`
```bash
#!/bin/bash
# End‑of‑day paper portfolio performance report

cd /home/satyamini/.openclaw/workspace/projects/edoras

# Load environment
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
export PATH="$HOME/.nvm/versions/node/v22.22.1/bin:$PATH"

# Run paper trading report
python3 paper_trading.py --report

# Send via Telegram
python3 send_paper_report.py
```

### 2. `run_paper_rebalancing.sh`
```bash
#!/bin/bash
# Weekly paper portfolio rebalancing

cd /home/satyamini/.openclaw/workspace/projects/edoras

# Load environment
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
export PATH="$HOME/.nvm/versions/node/v22.22.1/bin:$PATH"

# Run rebalancing
python3 paper_rebalancing.py

# Send report
python3 send_rebalancing_report.py
```

### 3. `paper_rebalancing.py`
```python
#!/usr/bin/env python3
"""
Weekly paper portfolio rebalancing
"""

import sys
sys.path.insert(0, '.')

from paper_trading import PaperTradingPortfolio
from enhanced_optimizer import EnhancedPortfolioOptimizer

def main():
    print("🔄 Weekly Paper Portfolio Rebalancing")
    
    # Load portfolio
    portfolio = PaperTradingPortfolio("crypto_data.db", initial_capital=1000.0)
    
    # Get updated scores
    optimizer = EnhancedPortfolioOptimizer("crypto_data.db")
    scores_df = optimizer.score_all_symbols()
    
    if scores_df.empty:
        print("❌ No scores available")
        return
    
    # Select top 5 symbols
    top_5 = scores_df.head(5)['symbol'].tolist()
    print(f"Top 5 symbols: {top_5}")
    
    # Equal weight (20% each)
    target_allocation = {symbol: 0.20 for symbol in top_5}
    
    # Rebalance
    portfolio.rebalance_to_target(target_allocation)
    
    # Generate report
    report = portfolio.generate_performance_report()
    
    # Save report
    with open("paper_rebalancing_report.txt", "w") as f:
        f.write(report)
    
    print("✅ Rebalancing complete")
    print("\n" + report)

if __name__ == "__main__":
    main()
```

### 4. `send_paper_report.py` & `send_rebalancing_report.py`
Similar to existing Telegram reporting scripts but focused on paper trading.

---

## Monitoring & Maintenance

### Daily Health Checks
1. **Data collection success** (verify candlestick counts)
2. **API rate limits** (Coinbase 10 requests/second)
3. **Database size** (should grow ~5‑10 MB per week)
4. **Telegram delivery success** (check message IDs)

### Weekly Maintenance
1. **Database optimization** (`VACUUM` SQLite)
2. **Log rotation** (archive old logs)
3. **Score model calibration** (adjust weights if needed)
4. **Alert threshold review** (tune based on performance)

### Monthly Review
1. **Paper trading performance** vs benchmarks
2. **Signal accuracy analysis** (backtest alerts)
3. **Model improvements** (new indicators, timeframes)
4. **Feature roadmap** (next enhancements)

---

## Escalation Procedures

### Alert Priority Levels
- **P1 (Critical)**: System down, data collection failed > 24 hours
- **P2 (High)**: Strong buy/sell signals, portfolio risk > threshold
- **P3 (Medium)**: Daily reports, weekly rebalancing
- **P4 (Low)**: Performance metrics, informational updates

### Response Times
- **P1**: Immediate (within 1 hour)
- **P2**: Same business day (within 4 hours)
- **P3**: Next business day (within 24 hours)
- **P4**: When convenient

### Notification Channels
1. **Telegram** (primary) – for all alerts and reports
2. **Email** (backup) – for critical system issues
3. **System logs** – for debugging and audit trail

---

## Success Metrics

### Operational Metrics
- **Data collection uptime**: > 99%
- **Report delivery success**: > 95%
- **Alert accuracy**: > 60% (profitable signals)
- **System response time**: < 5 seconds for API calls

### Performance Metrics (Paper Portfolio)
- **Annualized return**: Target > 20%
- **Sharpe ratio**: Target > 1.0
- **Maximum drawdown**: Limit < 15%
- **Win rate**: > 55% of trades profitable

### User Experience Metrics
- **Alert relevance**: User finds > 80% of alerts useful
- **Report clarity**: Easy to understand and act upon
- **System reliability**: Minimal manual intervention needed

---

## Conclusion

This hybrid scheduling approach provides:
1. **Comprehensive daily analysis** for informed decision‑making
2. **Responsive alerts** for time‑sensitive opportunities
3. **Systematic discipline** through weekly rebalancing
4. **Clear escalation paths** for different priority levels

The system balances automation with human oversight, allowing the operator to stay informed without being overwhelmed, while I (Aleph) handle the operational execution and bring important decisions to his attention.

**Next Steps**:
1. Create the new scripts (`run_paper_report.sh`, `paper_rebalancing.py`, etc.)
2. Add the new cron jobs to the system
3. Test the complete workflow
4. Monitor for 1 week and adjust thresholds as needed

---

**Last Updated**: 2026‑03‑09  
**Version**: 1.0