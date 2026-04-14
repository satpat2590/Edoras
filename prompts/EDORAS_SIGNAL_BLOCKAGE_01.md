# Claude Code Prompt — Edoras: Signal Execution Blockage Fix

## Context

I run a crypto trading system called Edoras at `~/.openclaw/workspace/projects/edoras/`. It has 13 backtested strategies generating trading signals, but a system audit just revealed that **the vast majority of signals are being silently dropped and never executed.**

The evidence from the last 7 days:

```
| Strategy           | Signals | Executed | Exec% | Avg Strength |
|--------------------|---------|----------|-------|-------------|
| TSMOM_3M           | 4       | 0        | 0%    | 100.0       |
| MultiSignal        | 8       | 0        | 0%    | 50.0        |
| legacy_momentum    | 43      | 2        | 5%    | 62.4        |
| RegimeAware        | 23      | 3        | 13%   | 66.5        |
| BollingerReversion | 1       | 1        | 100%  | 92.8        |
| polymarket_overlay  | 1       | 0        | 0%    | 65.0        |
```

**TSMOM_3M is generating signals at strength 100 (maximum possible) and zero are executing.** MultiSignal generates 8 signals and none execute. legacy_momentum generates 43 signals and only 2 execute. Something in the execution gating logic is blocking nearly everything.

The system is net negative over 30 days: 46.7% win rate, -$1.32 P&L, profit factor 0.97 on a ~$954 portfolio. But the LLM agent (Argus) averaging +1.17% per trade suggests the signals themselves aren't bad — they're just not reaching execution.

## Your Task

1. **Diagnose** exactly why signals are being blocked
2. **Fix** the execution gating to let valid signals through
3. **Add logging** so we can see WHY each signal was skipped

Do NOT change the strategies themselves or the signal generation logic. The problem is in the execution pipeline, not the signal quality.

## Files to Investigate

The execution pipeline flows through these files in order:

### 1. `signal_trading.py` — Main orchestrator

This is the primary file. The flow is:

```
check_all_symbols()
  → For each symbol:
    → If routed: run_backtested_strategy(symbol)
    → If not routed: check_trading_signals(symbol, indicators) + enhance_signal()
  → Polymarket overlay (boost or add signals)
  → Returns: (signals, risk_exits, risk_report)

execute_paper_trades(signals, risk_exits)
  → Process risk exits first
  → For each signal:
    → Multiple gating checks (THIS IS WHERE SIGNALS DIE)
    → If BUY: position check, dedup, strength, sizing, cash reserve
    → If SELL: position check, hold time, sizing
```

**The gating checks in `execute_paper_trades()` are the prime suspects.** Look at every `continue` statement — each one is a signal being silently dropped.

### 2. `paper_trading.py` — Execution layer

Handles the actual portfolio operations: `execute_buy()`, `execute_sell()`, `execute_sell_all()`, `execute_partial_sell()`. Could be rejecting trades due to internal checks.

### 3. `risk_manager.py` — Risk checks

Runs before signal generation in `check_all_symbols()`. Could be setting `circuit_breaker_active = True` which suppresses all buys. Check if the circuit breaker is stuck on.

### 4. `config.py` — Configuration

Contains `PORTFOLIO_SYMBOLS`, risk parameters, and `get_active_portfolios()` which builds the strategy routing config.

### 5. `regime_monitor.py` — Strategy routing

The audit shows some symbols routed to RegimeAware with `hmm_available: False`. The regime monitor runs before signal generation and may be swapping strategies to ones that don't work.

## Specific Things to Check

### Check A: The `continue` Audit

In `signal_trading.py` `execute_paper_trades()`, find EVERY `continue` statement and identify what condition triggers it. For each one, determine:
- What is the condition?
- How often would it fire given the current portfolio state?
- Is it logging when it fires?

The known gates are:
1. `if symbol in self.portfolio.positions` — skip BUY if already holding
2. Dedup: same symbol bought in last 60 seconds
3. `if strength < 50` — skip weak signals
4. Cash reserve: `buy_amount < min_trade` after sizing
5. `if symbol not in self.portfolio.positions` — skip SELL if not holding
6. Hold time: skip SELL if held < min_hold_hours

**The most likely culprit for TSMOM_3M (strength 100, 0 executed):**
- Gate #1: we already hold the position (all 7 current positions are held)
- If the portfolio has 7 open positions and the signals are for symbols we already hold, every BUY gets skipped

**Verify this:** Cross-reference the TSMOM_3M signal symbols against the current open positions. If they match, that's the blockage — we're refusing to add to winning positions.

### Check B: Cash Available

The portfolio has $176 cash out of $954 total. The cash reserve is 5% ($47.70). Available cash for trading = $176 - $47.70 = $128.30. 

But position sizing for strength 50-65 is only 3-5% of portfolio = $28-$47. For strength 100: 10-15% = $95-$143. So a strength-100 signal could try to buy $143 but only $128 is available. Check if the sizing math results in amounts below the $10 minimum trade after all the caps and reserves.

### Check C: Strategy Route Mismatch

The audit shows:
```
DOGE-USD → RegimeAware (hmm_available: False)
DOT-USD → RegimeAware (hmm_available: False)
UNI-USD → RegimeAware (hmm_available: False, weight: 0.0)
```

If `hmm_available: False`, what does RegimeAware actually do? Does it fall back to heuristic detection, or does it produce no signals at all? Check `regime_monitor.py` and the RegimeAware strategy implementation.

UNI-USD has `weight: 0.0` — that likely means zero allocation. Check if this causes division by zero or zero-sized trades.

### Check D: Signal Strength After Enhancement

The audit shows MultiSignal at avg strength 50.0 — exactly at the threshold. After the enhancement pipeline (sentiment, ADX, volume, multi-timeframe, VIX regime), the strength might drop below 50 and get filtered out. But this shouldn't affect TSMOM_3M at 100.

Check: do backtested strategy signals go through `enhance_signal()`? Or do they bypass it? If they bypass it, the raw strength is what matters. If they go through it, the multipliers might reduce a strong signal below threshold.

### Check E: The `check_all_symbols()` Loop

```python
if symbol in self.strategy_routes:
    bt_signal = self.run_backtested_strategy(symbol)
    if bt_signal:
        if bt_signal['strength'] >= 35:
            signals.append(bt_signal)
    continue  # routed symbol: strategy decides, never fall back to legacy
```

The `continue` after the routed check means legacy signals only fire for unrouted symbols. But TSMOM_3M and MultiSignal ARE backtested strategies — are they actually routed? Check the strategy_routes_json for the Galadriel portfolio. If TSMOM_3M isn't in the routing table, its signals might only appear in `strategy_signals_log` as audit entries but never feed into the execution pipeline.

**This could be the entire problem:** strategies are logging signals to the signals table but they're not in the routing table, so `run_backtested_strategy()` never runs them.

### Check F: Circuit Breaker State

```python
if self.risk_manager.circuit_breaker_active:
    logger.warning("Circuit breaker active — suppressing all buy signals")
    return [], risk_exit_signals, risk_report
```

Is the circuit breaker stuck on? Check the risk state file:
```bash
cat ~/.openclaw/workspace/projects/edoras/risk_state.json
```

If `circuit_breaker_active: true`, that blocks ALL buys regardless of signal strength.

## What to Fix

Based on the diagnosis, implement these fixes:

### Fix 1: Add Detailed Skip Logging

Every `continue` in `execute_paper_trades()` must log WHY it's skipping. Change every silent skip to a logged skip:

```python
# BEFORE (silent):
if symbol in self.portfolio.positions:
    continue

# AFTER (logged):
if symbol in self.portfolio.positions:
    logger.info(f"SKIP BUY {symbol}: already holding position "
                f"(qty={self.portfolio.positions[symbol].get('quantity', 0):.6g})")
    # Also log to strategy_signals_log if we have a signal_id
    if self.strategy_tracker and '_signal_id' in sig:
        self.strategy_tracker.update_signal_skip_reason(
            sig['_signal_id'], "position_held")
    continue
```

Do this for EVERY skip path:
- "position_held" — already holding
- "dedup_window" — same symbol bought recently
- "strength_too_low" — below 50 threshold
- "insufficient_cash" — buy amount below minimum after sizing
- "no_position_to_sell" — SELL but not holding
- "min_hold_period" — held less than minimum hours
- "circuit_breaker" — circuit breaker active

### Fix 2: Add Skip Reason to strategy_signals_log

Check if `strategy_signals_log` has a column for skip reason. If not, add one:

```sql
-- Check current schema
.schema strategy_signals_log

-- If no skip_reason column, add it:
ALTER TABLE strategy_signals_log ADD COLUMN skip_reason TEXT;
```

Then update the strategy tracker to record skip reasons when signals aren't executed.

### Fix 3: Fix the Strategy Routing Gap

If TSMOM_3M and MultiSignal are generating signals via the signal logging system but aren't in the portfolio's strategy_routes_json, they'll never execute. 

Check the routing table:
```python
# In the Galadriel portfolio's strategy_routes_json:
# Which symbols are routed to which strategies?
```

If TSMOM_3M signals are for symbols that are routed to OTHER strategies (e.g., BTC-USD is routed to BollingerReversion, so TSMOM_3M's BTC signal gets ignored), then the signals are legitimate but there's no execution path.

Options:
a) Route additional symbols to TSMOM_3M where backtest shows it performs well
b) Allow secondary strategy signals to execute if the primary strategy is silent
c) Create a multi-strategy consensus mode where signals from multiple strategies strengthen each other

Recommend option (a) or (b) based on what you find.

### Fix 4: Fix RegimeAware with Missing HMM

Three symbols (DOGE-USD, DOT-USD, UNI-USD) are routed to RegimeAware with `hmm_available: False`. 

Check what happens in `regime_monitor.py` → `detect_regime()` when HMM is unavailable:
- Does it fall back to heuristic detection? (OK)
- Does it return None or an empty result? (BAD — means no signals)
- Does the strategy produce signals with heuristic fallback?

If HMM unavailability causes the strategy to produce zero signals, reroute these symbols to strategies that work without HMM:
- DOGE-USD: backtest shows MultiSignal had 1.16 Sharpe on DOGE at 4h
- DOT-USD: REMOVE FROM PORTFOLIO (0 wins, -$16 in 30 days)
- UNI-USD: weight is 0.0, probably should be removed or rerouted

### Fix 5: Review Position Concentration

With 7 open positions and ~$128 available cash:
- Average position value: ($954 - $176) / 7 = $111 per position
- New position at 10% allocation: $95
- That's feasible with $128 cash

But if the signals are all for symbols we already hold (BTC, ETH, SOL, etc.), the "already holding" gate blocks everything. The current logic prevents adding to positions entirely.

Consider: should we allow adding to winning positions? The current code says:
```python
if symbol in self.portfolio.positions:
    logger.info(f"Already hold {symbol} — skipping BUY signal")
    continue
```

This is overly conservative for a portfolio that holds 7 out of 8 symbols. Options:
a) Allow position increases up to the max_position_pct limit
b) Allow position increases only for high-conviction signals (strength > 80)
c) Keep the restriction but ensure SELL signals can reduce positions to make room for new BUY signals

Recommend (a) or (b) with appropriate guard rails.

### Fix 6: Prune DOT-USD

DOT-USD: 3 trades, 0 wins, -$16.07, routed to RegimeAware with broken HMM.

Remove it from the portfolio:
1. Close the position if one is open
2. Remove from `PORTFOLIO_SYMBOLS` in config
3. Remove from the strategy_routes_json

## Validation

After fixes, create `scripts/verify_signal_flow.py`:

```python
#!/usr/bin/env python3
"""
Verify that signals flow through to execution correctly.
Runs the signal pipeline in test mode and traces every signal's path.
"""

# For each portfolio symbol:
# 1. Check if it's in strategy_routes — print the route
# 2. Run the backtested strategy in test mode — print signal or "silent"
# 3. Check all gates:
#    - Already holding? Print yes/no + quantity
#    - Cash available? Print amount
#    - Strength meets threshold? Print strength
#    - Dedup would block? Print last buy time
#    - Hold time would block sell? Print held hours
# 4. Verdict: WOULD EXECUTE / BLOCKED BY [reason]

# This gives a clear picture of what would happen right now if signals fired.
```

Also add a CLI command:
```bash
python3 cli.py signal-trace         # Show why each symbol's signals are/aren't executing
python3 cli.py signal-trace BTC-USD  # Trace for a specific symbol
```

## Important Notes

- Do NOT change the strategies or signal generation logic. Fix the execution pipeline only.
- Do NOT lower the strength threshold below 50 — that's a risk guardrail.
- Do NOT disable the circuit breaker — but DO check if it's stuck on.
- DO add logging for every skip. Silent drops are the enemy — we need to see every decision.
- The fix should be backward-compatible — existing behavior is preserved except where it's clearly broken.
- Test the fix by running `python3 signal_trading.py --test` and verifying signals flow through.
- After fixing, run `python3 scripts/verify_signal_flow.py` to confirm the pipeline is unblocked.
- The Edoras project path is: `~/.openclaw/workspace/projects/edoras/`
