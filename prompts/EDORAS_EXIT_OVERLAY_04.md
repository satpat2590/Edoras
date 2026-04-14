# Claude Code Prompt — Edoras: Exit Strategy Overlay & TSMOM Routing

## Context

Edoras is a crypto trading system at `~/.openclaw/workspace/projects/edoras/`. After fixing signal blockage, HMM convergence, and silent strategies, the system now has clear diagnostics via `strategy-trace`. The current problem:

**The portfolio has no active exit strategy.** The system has 5 held positions (BNB -5.6%, BNKR -4.1%, UNI -1.5%, DOGE -0.4%, BTC +0.3%) in a uniform downtrend (all symbols: price < SMA20 < SMA50). Only TSMOM_3M on DOGE generates a SELL signal. The other 4 positions will bleed until the risk manager's mechanical -10% stop-loss fires — the worst possible exit point.

**Root cause:** Each symbol has one strategy that handles both entry AND exit. Most strategies (BollingerReversion, MultiSignal) are entry-focused and nearly silent on exits. When the regime shifts from sideways (where entries happened) to downtrend (current), there's no strategy watching positions for exit opportunities.

**The fix:** Add an exit overlay that runs on ALL held positions, independent of the entry strategy. The entry strategy decides when to buy. The exit overlay decides when to sell. They can be different strategies.

Current strategy routing:
```
DOGE-USD  → TSMOM_3M (4h)          — just rerouted, generating SELL
AVAX-USD  → MultiSignal (4h)       — silent (no position held)
BTC-USD   → BollingerReversion (4h) — silent (ADX too high for ranging strategy)
ADA-USD   → MultiSignal (4h)       — silent (consensus too low)
XRP-USD   → MultiSignal (4h)       — silent (no position held)
UNI-USD   → RegimeAware (4h)       — silent
```

Current positions: BNB-USD (-5.6%), BNKR-BASE (-4.1%), UNI-USD (-1.5%), DOGE-USD (-0.4%), BTC-USD (+0.3%)

## Architecture Decision

We're implementing an **exit overlay** pattern, not replacing the strategy routing system:

```
Signal Pipeline (current):
  1. Regime detection (HMM/heuristic)
  2. Entry strategy generates signals (per-symbol routing)
  3. Polymarket overlay (boost/add signals)
  4. Execute signals

Signal Pipeline (new):
  1. Regime detection (HMM/heuristic)
  2. Entry strategy generates signals (per-symbol routing)
  3. EXIT OVERLAY: check all held positions for exit conditions  ← NEW
  4. Polymarket overlay (boost/add signals)
  5. Execute signals
```

The exit overlay runs AFTER entry strategies and BEFORE execution. It can generate SELL signals for any held position, regardless of which strategy entered it. Entry strategy signals still take priority for BUY decisions.

## What to Build

### Part 1: TSMOM Routing Analysis

Before building the exit overlay, analyze whether more symbols should use TSMOM as their PRIMARY strategy (not just exit overlay).

**Step 1: Query TSMOM backtest results**

```sql
-- TSMOM_3M performance across all symbols
SELECT symbol, sharpe_ratio, win_rate, total_return, total_trades, max_drawdown
FROM strategy_performance
WHERE strategy_name IN ('TSMOM', 'TSMOM_3M')
AND total_trades >= 3
ORDER BY sharpe_ratio DESC;

-- Compare against current assigned strategies for held symbols
SELECT sp.symbol, sp.strategy_name, sp.sharpe_ratio, sp.win_rate, sp.total_trades
FROM strategy_performance sp
WHERE sp.symbol IN ('BNB-USD', 'UNI-USD', 'BTC-USD', 'DOGE-USD')
AND sp.strategy_name IN ('TSMOM', 'TSMOM_3M', 'BollingerReversion', 'MultiSignal', 'RegimeAware')
AND sp.total_trades >= 2
ORDER BY sp.symbol, sp.sharpe_ratio DESC;
```

**Step 2: Recommend routing changes**

For each currently held symbol, compare the assigned strategy's backtest performance against TSMOM_3M. If TSMOM_3M has a higher Sharpe ratio with meaningful trade count, recommend rerouting.

Print the comparison as a table:
```
| Symbol   | Current Strategy    | Current Sharpe | TSMOM_3M Sharpe | TSMOM Trades | Recommend |
|----------|--------------------|--------------:|----------------:|-------------:|-----------|
| BTC-USD  | BollingerReversion | 0.20          | ???             | ???          | ???       |
| UNI-USD  | RegimeAware        | 0.09          | ???             | ???          | ???       |
| BNB-USD  | (unrouted)         | -             | ???             | ???          | ???       |
```

**Step 3: Apply routing changes**

For symbols where TSMOM_3M clearly outperforms (Sharpe delta > 0.3), update the routing in the database:

```python
import sqlite3, json

conn = sqlite3.connect('crypto_data.db')
row = conn.execute("SELECT strategy_routes_json FROM portfolios WHERE id=1").fetchone()
routes = json.loads(row[0])

# Example: reroute BNB-USD to TSMOM_3M if backtest supports it
# routes['BNB-USD'] = {'strategy': 'TSMOM_3M', 'timeframe': '4h', 'params': {}}

conn.execute("UPDATE portfolios SET strategy_routes_json=? WHERE id=1", (json.dumps(routes),))
conn.commit()
```

Only reroute if the data clearly supports it. Document every change and the backtest evidence.

---

### Part 2: Exit Overlay Module

Create `exit_overlay.py` in the project root. This module generates exit signals for ALL held positions based on multiple exit criteria.

```python
#!/usr/bin/env python3
"""
Exit Overlay — generates SELL signals for held positions based on
trend, momentum, volatility, and correlation conditions.

Runs AFTER entry strategies in the signal pipeline. Can generate
SELL signals for any held position regardless of which strategy
entered it.

The exit overlay does NOT generate BUY signals. It only exits.

Architecture:
  Entry strategy → decides WHEN to buy
  Exit overlay   → decides WHEN to sell (independent of entry strategy)
  Risk manager   → mechanical stops (last line of defense)

The exit overlay sits BETWEEN the entry strategy and the risk manager:
  - Smarter than mechanical stops (reads trend, momentum, correlation)
  - Less aggressive than the entry strategy's own exit logic
  - Catches regime transitions that the entry strategy doesn't handle
"""

import logging
import sqlite3
import numpy as np
import pandas as pd
from typing import Dict, List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)
```

The exit overlay should implement these exit conditions:

#### Exit Condition 1: Momentum Exit (TSMOM-style)

For each held position, check if momentum has turned negative:

```python
def check_momentum_exit(self, symbol: str, df: pd.DataFrame, position: dict) -> Optional[dict]:
    """
    TSMOM-style momentum exit.
    
    If the N-day return is negative AND the position is losing money,
    generate a SELL signal. This catches sustained downtrends early
    before the -10% stop-loss fires.
    
    Parameters:
      - lookback: 21 trading days (~1 month) for the momentum calculation
      - threshold: momentum must be below this to trigger (default: -2%)
      - min_held_hours: don't exit positions held less than this (default: 24h)
    """
    # Calculate lookback-period return
    # If return < threshold AND position P&L is negative → SELL
    # Strength scales with how negative the momentum is:
    #   -2% to -5%: strength 50-65 (moderate exit)
    #   -5% to -10%: strength 65-80 (strong exit)
    #   -10%+: strength 80-100 (urgent exit)
```

#### Exit Condition 2: Trend Break Exit

```python
def check_trend_break_exit(self, symbol: str, df: pd.DataFrame, position: dict) -> Optional[dict]:
    """
    Exit when the trend structure breaks against the position.
    
    If ALL of these are true:
      - Price < SMA20 (short-term trend broken)
      - SMA20 < SMA50 (medium-term trend broken)  
      - MACD histogram is negative (momentum confirming)
      - ADX > 20 (the downtrend has strength, not just noise)
    
    AND the position is at a loss, generate a SELL signal.
    
    This is different from momentum exit — momentum looks at returns,
    trend break looks at technical structure. A position can have flat
    returns but broken trend structure (early warning).
    
    Strength: 60-80 depending on how many conditions are met and ADX strength.
    """
```

#### Exit Condition 3: Volatility Spike Exit

```python
def check_volatility_exit(self, symbol: str, df: pd.DataFrame, position: dict) -> Optional[dict]:
    """
    Exit when volatility expands significantly against the position.
    
    If ATR(14) has increased by more than 50% compared to ATR at entry time,
    AND the position is losing money, it means the market is getting more
    dangerous and the loss could accelerate.
    
    This catches situations where a quiet sideways market (where you entered)
    suddenly becomes volatile (regime transition).
    
    Strength: 55-70 depending on volatility expansion magnitude.
    """
    # Compare current ATR to ATR at position entry time
    # Need to look up ATR at entry from historical indicators
    # If current_atr > entry_atr * 1.5 AND position P&L < 0 → SELL
```

#### Exit Condition 4: Correlation Contagion Exit

```python
def check_correlation_exit(self, symbol: str, df: pd.DataFrame, position: dict, 
                           portfolio_positions: dict) -> Optional[dict]:
    """
    Exit when a symbol becomes highly correlated with a losing leader.
    
    If BTC is trending down AND this symbol's rolling correlation with BTC
    has increased above 0.8, the position is likely to follow BTC down.
    Exit before the correlation-driven loss materializes.
    
    Also check: if 3+ portfolio positions are all losing simultaneously
    (correlation cluster), exit the weakest one to reduce exposure.
    
    This uses the correlation_tracker module if available.
    
    Strength: 50-65 (moderate, since correlation is predictive not confirmed).
    """
    # Check BTC trend direction (from indicators)
    # If BTC is in downtrend:
    #   Calculate rolling 14-day correlation between this symbol and BTC
    #   If correlation > 0.8 AND position is losing → SELL
    #
    # Also: count how many portfolio positions are losing
    # If losing_count >= 3 AND this position has the worst P&L% → SELL
```

#### Exit Condition 5: Time-Based Deterioration

```python
def check_time_deterioration_exit(self, symbol: str, position: dict) -> Optional[dict]:
    """
    Exit positions that have been held too long without making money.
    
    If a position has been held for more than max_hold_days (default: 14)
    AND it has a negative P&L, the original thesis is likely invalidated.
    Don't wait for the stop-loss — exit at the current level.
    
    This prevents "zombie positions" that tie up capital in losing trades
    for weeks while better opportunities pass.
    
    Strength: 50-60 (moderate, since time alone isn't a strong signal).
    """
    # Calculate hours held from position entry_time
    # If held > max_hold_days * 24 AND P&L < 0 → SELL
```

#### The Main Exit Overlay Runner

```python
def check_all_exits(self, portfolio_positions: dict, db_path: str) -> List[dict]:
    """
    Run all exit checks on all held positions.
    
    Returns a list of SELL signals, each with:
      - symbol
      - action: "SELL"  
      - strength: 50-100
      - reason: descriptive string with exit condition name
      - exit_type: "momentum_exit" | "trend_break" | "volatility_spike" | 
                   "correlation_contagion" | "time_deterioration"
      - _strategy_name: "exit_overlay"
      - _timeframe: "4h" (or whichever timeframe was used)
    
    If multiple exit conditions fire for the same symbol, use the STRONGEST one.
    """
    # For each held position:
    #   1. Load indicator window (4h, last 60 bars)
    #   2. Run all 5 exit checks
    #   3. Keep the strongest signal per symbol
    #   4. Log all checks (fired and not-fired) for diagnostics
    
    # Return the list of exit signals
```

#### Configuration

The exit overlay should be configurable via parameters (with sensible defaults):

```python
EXIT_OVERLAY_DEFAULTS = {
    # Momentum exit
    "momentum_lookback_days": 21,
    "momentum_threshold": -0.02,      # -2% return triggers exit
    "momentum_min_held_hours": 24,    # Don't exit positions held < 24h
    
    # Trend break exit
    "trend_break_min_adx": 20,        # ADX must confirm trend has strength
    "trend_break_min_held_hours": 12, # Don't exit very fresh positions
    
    # Volatility exit
    "volatility_expansion_threshold": 1.5,  # ATR must be 1.5x entry ATR
    
    # Correlation exit
    "correlation_threshold": 0.8,     # Rolling correlation with BTC
    "correlation_window_days": 14,    # Window for rolling correlation
    "cluster_loss_count": 3,          # How many losing positions = cluster
    
    # Time deterioration
    "max_hold_days": 14,              # Exit losing positions after this
    
    # General
    "min_loss_pct_to_exit": -0.005,   # Position must be at least -0.5% to trigger exit
                                       # (don't exit positions that are basically flat)
}
```

---

### Part 3: Integrate Exit Overlay into Signal Pipeline

Modify `signal_trading.py` to run the exit overlay after entry strategies:

```python
# In check_all_symbols(), after the entry strategy loop and before Polymarket overlay:

# ── Exit overlay: check all held positions for exit conditions ──
if self.portfolio and hasattr(self, 'exit_overlay'):
    exit_signals = self.exit_overlay.check_all_exits(
        portfolio_positions=self.portfolio.positions,
        db_path=self.db_path,
    )
    for exit_sig in exit_signals:
        # Check if entry strategy already generated a signal for this symbol
        existing = next((s for s in signals if s['symbol'] == exit_sig['symbol']), None)
        if existing:
            # If entry strategy also says SELL, boost with exit overlay strength
            if existing['action'] == 'SELL':
                boost = min(exit_sig['strength'] * 0.3, 15)
                existing['strength'] = min(existing['strength'] + boost, 100)
                existing['reason'] += f" | exit overlay confirms ({exit_sig['exit_type']})"
                logger.info(f"Exit overlay confirms SELL {exit_sig['symbol']} "
                           f"(+{boost:.0f} boost from {exit_sig['exit_type']})")
            # If entry strategy says BUY but exit overlay says SELL, 
            # the exit overlay wins (capital preservation > new entry)
            elif existing['action'] == 'BUY':
                logger.warning(f"Exit overlay conflicts with BUY signal for {exit_sig['symbol']} "
                              f"— exit overlay takes priority ({exit_sig['exit_type']})")
                signals.remove(existing)
                signals.append(exit_sig)
        else:
            # No entry strategy signal — exit overlay is the sole voice
            signals.append(exit_sig)
            logger.info(f"Exit overlay signal: SELL {exit_sig['symbol']} "
                       f"strength={exit_sig['strength']:.0f} ({exit_sig['exit_type']})")

    if exit_signals:
        logger.info(f"Exit overlay: {len(exit_signals)} exit signals generated")
```

Also initialize the exit overlay in `SignalTradingSystem.__init__()`:

```python
# Initialize exit overlay
try:
    from exit_overlay import ExitOverlay
    self.exit_overlay = ExitOverlay(db_path=db_path)
    logger.info(f"[{self.portfolio_name}] Exit overlay loaded")
except ImportError:
    self.exit_overlay = None
    logger.warning("Exit overlay not available")
```

---

### Part 4: Exit Overlay Diagnostics

Add exit overlay information to the `strategy-trace` CLI command.

After showing the entry strategy trace for each symbol, add a section for held positions:

```
======================================================================
  EXIT OVERLAY — Held Positions
======================================================================

  BNB-USD (held 6.4d, P&L: -5.6%)
    Momentum:      -8.2% 21d return → SELL (strength 72)
    Trend break:   price<SMA20<SMA50, ADX=28, MACD neg → SELL (strength 68)
    Volatility:    ATR expanded 1.2x (below 1.5x threshold) → HOLD
    Correlation:   BTC corr=0.74 (below 0.8 threshold) → HOLD
    Time decay:    held 6.4d < 14d max → HOLD
    >>> STRONGEST: momentum_exit (strength 72)

  UNI-USD (held 2.5d, P&L: -1.5%)
    Momentum:      -4.1% 21d return → SELL (strength 58)
    Trend break:   price<SMA20<SMA50, ADX=32, MACD neg → SELL (strength 65)
    Volatility:    ATR expanded 1.1x → HOLD
    Correlation:   BTC corr=0.62 → HOLD
    Time decay:    held 2.5d < 14d → HOLD
    >>> STRONGEST: trend_break (strength 65)

  ...

  Exit Overlay Summary:
  ┌──────────┬──────────┬───────────────────┬──────────┐
  │ Symbol   │ P&L%     │ Exit Condition    │ Strength │
  ├──────────┼──────────┼───────────────────┼──────────┤
  │ BNB-USD  │ -5.6%    │ momentum_exit     │ 72       │
  │ UNI-USD  │ -1.5%    │ trend_break       │ 65       │
  │ DOGE-USD │ -0.4%    │ (TSMOM_3M active) │ 100      │
  │ BTC-USD  │ +0.3%    │ HOLD              │ -        │
  │ BNKR-BASE│ -4.1%    │ momentum_exit     │ 68       │
  └──────────┴──────────┴───────────────────┴──────────┘
```

---

### Part 5: Add Exit Overlay to Strategy Signals Log

Exit overlay signals should be logged to `strategy_signals_log` with `strategy_name = "exit_overlay"` and the specific exit condition in the reason field. This feeds future LoRA training data — the model learns which exit conditions work.

Also log exit overlay decisions (including HOLD decisions) at DEBUG level so we have full audit trails.

---

### Part 6: Register Unrouted Held Positions

The audit showed BNB-USD is held but NOT in the routing table. Unrouted symbols only get legacy signal logic, which is primarily BUY-focused.

For symbols that are HELD but NOT ROUTED:
1. The exit overlay handles exit signals (this is the main fix)
2. Consider adding them to the routing table with TSMOM_3M (pure momentum — good for "should I still hold this?")
3. At minimum, log a warning: "BNB-USD is held but has no routed strategy — relying on exit overlay and risk manager only"

Check which held symbols are unrouted:

```python
# Get held symbols
held = set(portfolio.positions.keys())
# Get routed symbols
routed = set(strategy_routes.keys())
# Unrouted held symbols
unrouted_held = held - routed
if unrouted_held:
    logger.warning(f"Held but unrouted symbols (exit overlay + risk manager only): "
                   f"{', '.join(sorted(unrouted_held))}")
```

Add this check to `check_all_symbols()` and print the warning.

---

### Part 7: Update Documentation

Update `docs/TRADING_RULES.md` with a new section:

```markdown
## Exit Strategy

### Three-Layer Exit Architecture

Edoras uses three independent layers for exiting positions, from smartest to most mechanical:

1. **Entry Strategy Exits** — the routed strategy (BollingerReversion, TSMOM, etc.) 
   can generate SELL signals based on its own logic. This is the fastest but only 
   works when the strategy is active in the current regime.

2. **Exit Overlay** — runs on ALL held positions regardless of entry strategy. 
   Checks momentum, trend structure, volatility, correlation, and hold time. 
   Catches regime transitions that the entry strategy misses. Active when:
   - Momentum turns negative (TSMOM-style)
   - Trend structure breaks (price < SMA20 < SMA50 + ADX confirms)
   - Volatility spikes (ATR expands 1.5x from entry)
   - Correlation contagion (symbol correlates with losing BTC)
   - Position held too long without profit (14-day max)

3. **Risk Manager** — mechanical stop-loss at -10%, trailing stop after +5% gain, 
   take-profit scale-out at +15/20/25%. This is the last line of defense and 
   executes at the worst price.

The goal: exit at Layer 1 or 2 (at -2% to -5%) instead of waiting for Layer 3 
(at -10%). The exit overlay is the bridge between smart strategy exits and 
dumb mechanical stops.

### Exit Signal Priority

When multiple layers generate conflicting signals:
- EXIT always beats BUY (capital preservation)
- Exit overlay SELL + entry strategy SELL = boosted strength
- Exit overlay SELL + entry strategy BUY = exit wins, BUY suppressed
- Risk manager exits bypass everything (non-negotiable)
```

---

## Validation

After implementing everything:

```bash
# 1. Strategy trace with exit overlay — should show exit conditions for held positions
python3 cli.py strategy-trace

# 2. Signal test — should show exit overlay signals alongside entry strategy signals
python3 signal_trading.py --test

# 3. Verify pipeline integrity
python3 -c "from signal_trading import SignalTradingSystem; print('OK')"
python3 -c "from exit_overlay import ExitOverlay; print('OK')"

# 4. Verify exit overlay fires for current positions
python3 -c "
from exit_overlay import ExitOverlay
eo = ExitOverlay(db_path='crypto_data.db')
# Simulate with current positions
positions = {
    'BNB-USD': {'quantity': 0.307, 'avg_price': 644.7, 'entry_time': '2026-03-23T10:00:00'},
    'UNI-USD': {'quantity': 28.07, 'avg_price': 3.404, 'entry_time': '2026-03-27T10:00:00'},
}
signals = eo.check_all_exits(positions, 'crypto_data.db')
for s in signals:
    print(f'{s[\"symbol\"]}: {s[\"action\"]} strength={s[\"strength\"]:.0f} ({s[\"exit_type\"]})')
print(f'Total: {len(signals)} exit signals')
"
```

Expected: at least 2-3 exit signals for the currently losing positions, with clear reasons.

## Important Notes

- **Do NOT modify risk_manager.py.** The exit overlay is ABOVE the risk manager, not replacing it. Stop-losses remain the last line of defense.
- **Do NOT generate BUY signals from the exit overlay.** It only generates SELL signals.
- **Exit overlay SELL signals go through the same execution gating** as entry strategy signals — strength threshold, hold time check, position check all apply.
- **The exit overlay should be gracefully optional.** If it fails to import, the system runs without it (same as Polymarket overlay).
- **Log everything.** Every exit check (fired and not-fired) should be logged for future LoRA training data.
- **Start conservative with thresholds.** It's better to exit too late than to exit profitable positions due to overly sensitive triggers. We can tighten thresholds after observing behavior.
- **Commit before starting:** `git add -A && git commit -m "pre-exit-overlay snapshot"`
- **The Edoras project path is:** `~/.openclaw/workspace/projects/edoras/`
