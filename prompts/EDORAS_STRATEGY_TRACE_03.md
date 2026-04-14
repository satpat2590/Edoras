# Claude Code Prompt — Edoras: Fix HMM Convergence & Silent Strategies

## Context

Edoras is a crypto trading system at `~/.openclaw/workspace/projects/edoras/`. After recent cleanup and signal blockage fixes, the pipeline runs without errors — but **every routed strategy is silent** and producing zero signals. Meanwhile the portfolio has $440 cash sitting idle.

Here's the current output from `python3 signal_trading.py --test`:

```
WARNING: Model is not converging. (ADA-USD, BTC-USD, UNI-USD, XRP-USD)
INFO: [AVAX-USD] HMM regime: sideways (conf=0.95)
INFO: [BTC-USD] HMM regime: sideways (conf=0.58)
INFO: [DOGE-USD] HMM regime: sideways (conf=1.00)
INFO: [UNI-USD] HMM regime: sideways (conf=1.00)
INFO: [XRP-USD] HMM regime: sideways (conf=1.00)
INFO: Ignoring legacy params for RegimeAware/DOGE-USD, using defaults
INFO: Routed strategy silent for ADA-USD — holding (no legacy fallback)
INFO: Routed strategy silent for AVAX-USD — holding (no legacy fallback)
INFO: Routed strategy silent for BTC-USD — holding (no legacy fallback)
INFO: Routed strategy silent for DOGE-USD — holding (no legacy fallback)
INFO: Routed strategy silent for UNI-USD — holding (no legacy fallback)
INFO: Routed strategy silent for XRP-USD — holding (no legacy fallback)

Galadriel (paper): No actionable signals.
```

**Two linked problems:**
1. HMM convergence fails on 4/6 symbols — regime detection is unreliable
2. All 6 routed strategies produce zero signals — portfolio is frozen

The current strategy routing:
```
DOGE-USD  → RegimeAware (4h)     — hmm params ignored, using defaults
AVAX-USD  → MultiSignal (4h)
BTC-USD   → BollingerReversion (4h)
ADA-USD   → MultiSignal (4h)
XRP-USD   → MultiSignal (4h)
UNI-USD   → RegimeAware (4h)
```

Current portfolio: 5 positions (BNB, BNKR, UNI, DOGE, BTC), $440 cash, $946 total value.

## Your Task

1. **Diagnose** why HMM isn't converging and whether it's affecting strategy signals
2. **Diagnose** why each strategy is silent (the core problem)
3. **Fix** both issues so strategies actually produce signals
4. **Add monitoring** so we can see strategy internals when they're silent

## Investigation Plan

### Investigation 1: Why are strategies silent?

This is the critical question. Each strategy's `generate_signals()` method takes a DataFrame of indicators and returns a list of signals (or empty list). We need to know WHY the list is empty for each one.

**Step 1: Trace BollingerReversion on BTC-USD**

BTC-USD is routed to BollingerReversion on the 4h timeframe. Sideways market is BollingerReversion's BEST regime — it should absolutely be generating signals. Something is wrong.

Find the BollingerReversion strategy class (likely in `backtest/strategies/`). Read its `generate_signals()` method and identify every condition that could cause it to return empty:

- Does it check ADX and require ADX < 25 for ranging? What is BTC's current 4h ADX?
- Does it check bb_position (price position within Bollinger Bands)? What is BTC's current bb_position?
- Does it require volume confirmation? What is the current volume_ratio?
- Does it have a minimum strength threshold?
- Does it check if we already hold a position?

**Run this diagnostic query to see BTC-USD's actual 4h indicators:**

```python
import sqlite3
import pandas as pd

conn = sqlite3.connect('crypto_data.db')
df = pd.read_sql_query("""
    SELECT c.timestamp, c.close, c.volume,
           i.rsi_14, i.adx_14, i.macd_histogram,
           i.bb_upper, i.bb_middle, i.bb_lower, i.bb_width,
           i.sma_20, i.sma_50, i.volume_ratio, i.atr_14
    FROM candlesticks c
    JOIN indicators i ON c.symbol=i.symbol AND c.timeframe=i.timeframe AND c.timestamp=i.timestamp
    WHERE c.symbol='BTC-USD' AND c.timeframe='4h'
    ORDER BY c.timestamp DESC LIMIT 10
""", conn)
conn.close()
print(df.to_string())
```

This tells us exactly what data BollingerReversion is seeing. Then manually walk through the strategy's conditions with these actual values to find where the signal gets blocked.

**Step 2: Trace MultiSignal on AVAX-USD, ADA-USD, XRP-USD**

Same approach. Find MultiSignal's `generate_signals()`. Identify all conditions. Run the diagnostic query for each symbol on 4h timeframe. Walk through the logic.

MultiSignal uses a consensus of 5 sub-signals and requires ≥2.5 aligned in trending markets or ≥3.0 in ranging. In a sideways market, the threshold is HIGHER (3.0), which means it's harder to generate signals in exactly the regime where most symbols currently sit. This might be the issue — the consensus threshold is too strict for current market conditions.

**Step 3: Trace RegimeAware on DOGE-USD, UNI-USD**

RegimeAware is supposed to detect the regime (bull/bear/sideways) and then delegate to the best sub-strategy for that regime. The log says "Ignoring legacy params for RegimeAware/DOGE-USD, using defaults" — check what the default behavior is:

- Does RegimeAware use HMM internally (separate from regime_monitor.py)?
- Or does it use the regime already detected by regime_monitor.py?
- What sub-strategy does it select for a "sideways" regime?
- If it selects BollingerReversion as the sub-strategy for sideways, we're back to the same BollingerReversion issue

**Step 4: Check the indicator data pipeline**

The strategies read from the `indicators` table for the 4h timeframe. Verify the data is actually there and fresh:

```sql
-- Most recent 4h indicators for each routed symbol
SELECT symbol, MAX(timestamp) as latest_ts,
       COUNT(*) as total_rows
FROM indicators
WHERE timeframe = '4h'
AND symbol IN ('BTC-USD', 'AVAX-USD', 'ADA-USD', 'XRP-USD', 'DOGE-USD', 'UNI-USD')
GROUP BY symbol;
```

If the latest timestamp is more than 8 hours old, the strategies might be seeing stale data. Also check that `bb_upper`, `bb_lower`, `bb_width` are not NULL — if Bollinger Band data is missing, BollingerReversion can't function.

```sql
-- Check for NULL indicators that strategies need
SELECT symbol,
       COUNT(*) as rows,
       SUM(CASE WHEN bb_upper IS NULL THEN 1 ELSE 0 END) as bb_null,
       SUM(CASE WHEN adx_14 IS NULL THEN 1 ELSE 0 END) as adx_null,
       SUM(CASE WHEN rsi_14 IS NULL THEN 1 ELSE 0 END) as rsi_null,
       SUM(CASE WHEN volume_ratio IS NULL THEN 1 ELSE 0 END) as vol_null
FROM indicators
WHERE timeframe = '4h'
AND symbol IN ('BTC-USD', 'AVAX-USD', 'ADA-USD', 'XRP-USD', 'DOGE-USD', 'UNI-USD')
AND timestamp > (SELECT MAX(timestamp) - 86400*7 FROM indicators WHERE timeframe='4h')
GROUP BY symbol;
```

### Investigation 2: Why is HMM not converging?

Find `regime_monitor.py` and look at `detect_regime_hmm()`. The warnings come from hmmlearn's GaussianHMM fit. Common causes:

**a) Too few data points:** The function uses 120-day lookback. After computing 20-day rolling features, that leaves ~100 usable rows. For a 3-state HMM, 100 samples can be marginal. Check: is the function getting enough data?

**b) Degenerate states:** If the market has been flat, two of the three HMM states might converge to the same distribution. This causes the optimizer to fail. Check: are the state means very close to each other?

**c) Random seed issues:** The HMM uses `random_state=42`. If the data has changed slightly since the model was last cached, the fit might diverge from a different starting point.

**d) Feature scaling:** The features are log returns, rolling volatility, and rolling mean return. If these have very different scales, the HMM covariance estimation can be unstable. Check if the features are standardized.

**The fix for HMM convergence is NOT to make it always converge** — sometimes the market genuinely doesn't have 3 distinct regimes. The fix is:

1. Make the heuristic fallback more robust (it already falls back, verify it works correctly)
2. Suppress the warning or downgrade it to DEBUG (it's noisy and not actionable for the user)
3. Consider reducing to 2 states (bull/not-bull) when 3 states won't converge
4. Increase `n_iter` from 100 to 200 to give more convergence attempts

### Investigation 3: What should `run_backtested_strategy()` actually do?

Read `signal_trading.py` → `run_backtested_strategy()`. This is the function that calls the strategy's `generate_signals()` and converts the result. Check:

- What DataFrame does it pass to the strategy? (from `get_indicator_window()`)
- How many rows does `get_indicator_window()` return? (lookback=60 by default)
- Does it pass the `portfolio_ctx` correctly? (capital, position_qty, entry_price)
- After `generate_signals()` returns, does it correctly convert the result?
- Is there any filtering or threshold AFTER `generate_signals()` but before returning?

The key line is:
```python
signals = strategy.generate_signals(df, portfolio_ctx)
```

If `signals` is empty, the strategy genuinely found nothing. But we need to know WHY — add debug logging INSIDE the strategy's generate_signals to trace each condition.

## Fixes to Implement

### Fix 1: Add Strategy Debug Logging

For EACH strategy class (BollingerReversion, MultiSignal, RegimeAware), add detailed logging inside `generate_signals()` that shows WHY a signal was or wasn't generated:

```python
# Example for BollingerReversion:
def generate_signals(self, df, portfolio_ctx):
    latest = df.iloc[-1]
    
    adx = latest.get('adx_14')
    bb_pos = self._calculate_bb_position(latest)
    volume_ok = latest.get('volume_ratio', 0) > self.volume_threshold
    
    logger.debug(f"[BollingerReversion/{portfolio_ctx['symbol']}] "
                 f"ADX={adx:.1f} ({'ranging' if adx < self.adx_range_max else 'trending'}), "
                 f"BB_pos={bb_pos:.3f} (need <{self.entry_threshold} for BUY or >{1-self.entry_threshold} for SELL), "
                 f"vol_ok={volume_ok}")
    
    # ... existing logic ...
    
    if not signals:
        logger.info(f"[BollingerReversion/{portfolio_ctx['symbol']}] Silent — "
                    f"ADX={'OK' if adx < self.adx_range_max else 'TOO_HIGH'}, "
                    f"BB_pos={'NEUTRAL' if self.entry_threshold <= bb_pos <= 1-self.entry_threshold else 'EXTREME'}, "
                    f"vol={'OK' if volume_ok else 'LOW'}")
    
    return signals
```

Do this for all three strategy types. The goal: when the strategy is silent, we see EXACTLY which condition prevented it from firing. Change the log level from DEBUG to INFO for now so it shows up in the test output.

### Fix 2: Fix the BollingerReversion Threshold

BollingerReversion's `bb_threshold` parameter controls how close to the bands price must be. The routing config shows `'params': {'bb_threshold': 0.05, 'adx_range_max': 25}`.

`bb_threshold: 0.05` means price must be within 5% of the lower band to BUY or within 5% of the upper band to SELL. That's extremely tight — in a sideways market with narrow bands, price might hover near the middle (bb_position ~0.4-0.6) and never reach the extremes.

Check the current bb_position for BTC-USD on 4h. If it's between 0.10 and 0.90, a threshold of 0.05 will never fire. Consider:
- Relaxing bb_threshold from 0.05 to 0.15 (price within 15% of band)
- Or using a dynamic threshold based on bb_width (narrower bands → wider threshold)

**Only change this if the diagnostic confirms bb_position is the reason BollingerReversion is silent.**

### Fix 3: Fix MultiSignal Consensus Threshold

MultiSignal requires ≥3.0 consensus in ranging markets (out of 5 sub-signals). If the market is genuinely mixed (2 bullish, 2 bearish, 1 neutral), consensus will be 2.0-2.5 and no signal fires.

Check: what consensus scores are MultiSignal computing for each symbol? Add logging:

```python
logger.info(f"[MultiSignal/{symbol}] Consensus: {consensus:.1f}/5.0 "
            f"(need ≥{threshold:.1f}), regime={'ranging' if adx < 25 else 'trending'}, "
            f"sub-signals: trend={trend_score}, macd={macd_score}, rsi={rsi_score}, "
            f"bb={bb_score}, vol={vol_score}")
```

If consensus is consistently 2.0-2.5 across all symbols, the threshold might be too strict for current market conditions. Options:
- Lower ranging threshold from 3.0 to 2.5
- Or add a "weak signal" mode that generates signals at lower strength (e.g., strength=40) for consensus 2.0-2.5

**Only change the threshold if the diagnostic confirms consensus is the blocker.**

### Fix 4: Fix RegimeAware Parameter Handling

The log shows "Ignoring legacy params for RegimeAware/DOGE-USD, using defaults" — this means the RegimeAware class constructor doesn't accept the params from the routing config. 

Find where this happens in `signal_trading.py` → `build_strategy_routes()`:

```python
try:
    instance = cls(**params) if params else cls()
except TypeError:
    logger.info(f"Ignoring legacy params for {strategy_name}/{symbol}, using defaults")
    instance = cls()
```

Check what params RegimeAware actually accepts. If `use_hmm` and `hmm_available` aren't valid constructor params, the strategy runs with defaults. What are those defaults? Does it do regime detection at all without these params?

Fix: either update the routing config to pass valid params, or update the RegimeAware class to accept and use `use_hmm`/`hmm_available`.

### Fix 5: Suppress HMM Convergence Warnings

The "Model is not converging" warnings come from hmmlearn's internal logging and clutter the output. The heuristic fallback already handles this case correctly. 

Options:
a) Set hmmlearn's log level to ERROR:
```python
import logging
logging.getLogger("hmmlearn").setLevel(logging.ERROR)
```

b) Catch the ConvergenceMonitor warning in `detect_regime_hmm()` and log it as DEBUG instead

c) Add `verbose=False` to the GaussianHMM constructor if supported

Implement whichever is cleanest. The regime detection should still log its own INFO message about fallback, just not the noisy hmmlearn warnings.

### Fix 6: Add a Strategy Trace CLI Command

Add a new CLI command to `cli.py`:

```bash
python3 cli.py strategy-trace              # Trace all routed symbols
python3 cli.py strategy-trace BTC-USD      # Trace one symbol
```

This should:
1. Load the strategy routing config
2. For each routed symbol:
   - Show the assigned strategy and timeframe
   - Load the indicator window (same as run_backtested_strategy)
   - Print the last 3 rows of key indicators (ADX, RSI, BB position, MACD, volume_ratio)
   - Run generate_signals() and show the result
   - If no signal: show WHY based on the strategy's debug logging
   - If signal: show action, strength, reason
3. Print a summary table:

```
Strategy Trace (4h timeframe)
┌──────────┬────────────────────┬──────┬──────┬───────┬────────────────────────────┐
│ Symbol   │ Strategy           │ ADX  │ RSI  │ BB%   │ Result                     │
├──────────┼────────────────────┼──────┼──────┼───────┼────────────────────────────┤
│ BTC-USD  │ BollingerReversion │ 18.2 │ 48.3 │ 0.52  │ SILENT: BB position neutral│
│ AVAX-USD │ MultiSignal        │ 22.1 │ 41.7 │ 0.31  │ SILENT: consensus 2.0 < 3.0│
│ DOGE-USD │ RegimeAware        │ 15.8 │ 50.2 │ 0.45  │ SILENT: sub-strategy silent│
│ ADA-USD  │ MultiSignal        │ 24.3 │ 38.9 │ 0.22  │ BUY strength=65            │
└──────────┴────────────────────┴──────┴──────┴───────┴────────────────────────────┘
```

This is the single most useful debugging tool for the trading system. When strategies are silent, this tells you exactly why.

### Fix 7: Consider Adding TSMOM_3M to Routing

The signal blockage fix earlier identified that TSMOM_3M generates strength-100 signals but has no execution path. If any symbols would benefit from a momentum strategy (especially in sideways-to-bullish transitions), consider adding TSMOM_3M to the routing table for 1-2 symbols.

Check TSMOM_3M backtest results:
```sql
SELECT symbol, sharpe_ratio, win_rate, total_return, total_trades
FROM strategy_performance
WHERE strategy_name = 'TSMOM_3M' AND total_trades >= 3
ORDER BY sharpe_ratio DESC;
```

If any symbols show Sharpe > 0.5 with TSMOM_3M, consider routing them there — especially symbols where the current strategy is consistently silent.

**Only do this if the diagnostic confirms the current strategy is fundamentally unsuited for the current market.**

## Validation

After all fixes, run:

```bash
# 1. Strategy trace — should show WHY each strategy fires or doesn't
python3 cli.py strategy-trace

# 2. Full signal test — should show at least some strategy activity
python3 signal_trading.py --test

# 3. Verify no broken imports
python3 -c "from signal_trading import SignalTradingSystem; print('OK')"
python3 -c "from risk_manager import RiskManager; print('OK')"

# 4. Check that HMM warnings are suppressed
python3 signal_trading.py --test 2>&1 | grep -c "not converging"
# Should be 0
```

The goal: `strategy-trace` shows clear reasons for each symbol, and at least 1-2 strategies are producing actionable signals instead of universal silence.

## Important Notes

- **Do NOT change risk management parameters** (stop-loss, circuit breaker, position limits).
- **Do NOT change the signal strength threshold** (minimum 50 for execution). The strategies should generate stronger signals, not lower the bar.
- **Do NOT remove any strategy from the routing** — add alternatives if needed, don't delete.
- **All strategy changes should be parameter adjustments** (thresholds, lookback windows), not algorithmic rewrites.
- **Log everything.** The debug logging added here becomes training data for the future LoRA adapter — it shows how strategies reason about market conditions.
- **The Edoras project path is:** `~/.openclaw/workspace/projects/edoras/`
- **Commit the current state before making changes:** `git add -A && git commit -m "pre-strategy-fix snapshot"`
