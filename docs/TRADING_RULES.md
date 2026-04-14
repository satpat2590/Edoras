# Edoras Trading Philosophy

> Complete reference of strategies, thresholds, and decision rules governing all trade execution.
> Use this document to understand what to modify for trend-aware LLM trading (Priority 4).

---

## 1. Signal Generation

Three signal types form the base layer. Each produces a **strength score** (0–100) that gates downstream sizing and execution.

### Mean-Reversion

Bets that extreme RSI readings will revert toward the mean. Best in ranging markets (ADX < 25).

| Condition | Side | Strength Formula |
|-----------|------|------------------|
| RSI < 30 + MACD histogram > 0 | BUY | `(30 - RSI) × 3.33 + min(macd_hist × 100, 50)` |
| RSI > 70 + MACD histogram < 0 | SELL | `(RSI - 70) × 3.33 + min(|macd_hist| × 100, 50)` |
| RSI < 35 + MACD histogram > 0 | BUY (weak) | `(35 - RSI) × 2.0 + min(macd_hist × 100, 30)` |
| RSI > 65 + MACD histogram < 0 | SELL (weak) | `(RSI - 65) × 2.0 + min(|macd_hist| × 100, 30)` |

### Trend-Following

Identifies established trends via SMA alignment and enters on pullbacks/rallies.

| Condition | Side | Strength |
|-----------|------|----------|
| Uptrend (price > SMA50, SMA20 > SMA50, MACD > 0) + 38 ≤ RSI ≤ 62 | BUY | `35 + max(0, (55 - RSI)) × 0.5` (+8 if ADX > 20) |
| Downtrend (price < SMA50, SMA20 < SMA50, MACD < 0) + 40 ≤ RSI ≤ 62 | SELL | `35 + (RSI - 40) × 0.5` (+8 if ADX > 20) |

### Momentum Breakout

Catches breakouts confirmed by volume expansion. Requires price/SMA20 alignment.

| Condition | Side | Strength |
|-----------|------|----------|
| Price > SMA20, MACD > 0, vol_ratio > 0.8, 45 ≤ RSI ≤ 70 | BUY | `30 + min(vol_ratio × 10, 20) + min(macd_hist × 50, 15)` |
| Price < SMA20, MACD < 0, vol_ratio > 0.8, 30 ≤ RSI ≤ 55 | SELL | `30 + min(vol_ratio × 10, 20) + min(|macd_hist| × 50, 15)` |

---

## 2. RSI Thresholds by Asset Class

Equities use tighter bands because they are less volatile than crypto.

| Level | Crypto | Equity |
|-------|--------|--------|
| Strong oversold | < 30 | < 35 |
| Weak oversold | < 35 | < 40 |
| Weak overbought | > 65 | > 60 |
| Strong overbought | > 70 | > 65 |

---

## 3. Enhancement Pipeline

Raw signal strength passes through five sequential multipliers before execution.

### 3a. Sentiment

| Condition | Multiplier |
|-----------|-----------|
| Positive sentiment (score > 0.6) aligned with BUY | × 1.2 |
| Negative sentiment (score < 0.4) on BUY | × 0.5 |
| Negative sentiment (score < 0.4) aligned with SELL | × 1.2 |
| Positive sentiment (score > 0.6) on SELL | × 0.5 |

### 3b. ADX Regime

| Market State | Condition | Multiplier |
|--------------|-----------|-----------|
| Trending (ADX > 30) | Signal aligned with MACD direction | × 1.3 |
| Trending (ADX > 30) | Counter-trend signal | × 0.7 |
| Ranging (ADX ≤ 30) | Mean-reversion signal (RSI < 35 or RSI > 65) | × 1.2 |

### 3c. Volume Confirmation

| Condition | Multiplier |
|-----------|-----------|
| Volume ratio > 1.2 | × 1.2 |

### 3d. Multi-Timeframe Alignment (1h vs 4h)

Alignment score (0–1) is computed from four sub-scores (+0.25 each): price vs SMA20, SMA20 vs SMA50, MACD histogram sign, RSI zone.

| Alignment | Multiplier |
|-----------|-----------|
| ≥ 0.75 | × 1.3 |
| 0.50–0.75 | × 1.1 |
| 0.25–0.50 | × 0.8 |
| < 0.25 | × 0.5 |

### 3e. VIX Regime

| Regime | VIX | BUY multiplier | SELL multiplier |
|--------|-----|-----------------|-----------------|
| Risk-on | < 20 | × 1.2 | × 0.8 |
| Neutral | 20–30 | × 1.0 | × 1.0 |
| Risk-off | > 30 | × 0.5 | × 1.3 |

---

## 4. Position Sizing

### Buy Sizing (after enhancement)

| Enhanced Strength | Allocation % | Label |
|-------------------|-------------|-------|
| < 50 | Skip | Too weak |
| 50–65 | 3–5% | Low conviction |
| 65–80 | 5–10% | Moderate conviction |
| 80–100 | 10–15% | High conviction |

Formulas:
- 80+: `0.10 + min((strength - 80) / 200, 0.05)`
- 65–80: `0.05 + (strength - 65) / 300`
- 50–65: `0.03 + (strength - 50) / 750`

Hard cap: 25% of portfolio (MAX_POSITION_PCT). Cash reserve: 5%.

### Sell Sizing

| Enhanced Strength | Sell % |
|-------------------|--------|
| ≥ 70 | 100% (full exit) |
| 50–70 | 50% |
| < 50 | 33% |

---

## 5. Risk Management

Risk thresholds are resolved **per asset class** via `config.ASSET_CLASS_PROFILES`.
Each symbol's asset class is looked up from the `securities` table. The tables below
show the defaults; the crypto column matches the legacy hardcoded values.

### Position-Level (per asset class)

| Rule | Crypto | Equity | Prediction | Index |
|------|--------|--------|------------|-------|
| Stop-loss | −10% | −7% | −15% | −5% |
| Trailing stop activation | +5% | +5% | +10% | +3% |
| Trailing stop distance | 2×ATR / 5% | 2×ATR / 4% | 2×ATR / 8% | 2×ATR / 3% |
| TP tier 1 | +15% → sell 33% | +10% → sell 33% | +30% → sell 50% | +8% → sell 33% |
| TP tier 2 | +20% → sell 33% | +15% → sell 33% | +50% → sell 100% | +12% → sell 50% |
| TP tier 3 | +25% → sell rest | +20% → sell rest | — | +18% → sell rest |
| Fee per trade | 0.1% | 0% | 2% | 0% |

### Portfolio-Level

| Rule | Crypto | Equity | Prediction | Index |
|------|--------|--------|------------|-------|
| Circuit breaker | −15% drawdown (auto-reset: 24h cooldown or ≥80% cash) | −15% drawdown | −15% drawdown | −15% drawdown |
| Max position pct | 25% | 15% | 10% | 20% |
| Max sector pct | 40% | 35% | 25% | 50% |

### Minimum Hold Period (per asset class)

| Asset Class | Min Hold | Rationale |
|-------------|----------|-----------|
| Crypto | 12h | Prevent fee-destroying churn on 4h timeframe |
| Equity | 24h | T+1 settlement, lower intraday vol |
| Prediction | 1h | Event-driven, fast turnover |
| Index | 24h | T+1 settlement |

Exceptions:
- **Risk-driven exits** (stop-loss, trailing stop, circuit breaker) always bypass.
- **Day-trading opportunities**: The LLM agent may request a shorter hold when justified by high-conviction short-term setups. Must include explicit reasoning in `decision_context`.

### Profile Resolution

All modules call `config.get_asset_class_profile(symbol)` which:
1. Queries `securities.asset_class` from the DB
2. Falls back to `get_asset_type()` heuristic (symbol pattern matching)
3. Returns the profile dict from `ASSET_CLASS_PROFILES`
4. Caches per symbol for the process lifetime

---

## Exit Strategy

### Three-Layer Exit Architecture

Edoras uses three independent layers for exiting positions, from smartest to most mechanical:

1. **Entry Strategy Exits** — the routed strategy (BollingerReversion, TSMOM, etc.) can generate SELL signals based on its own logic. This is the fastest but only works when the strategy is active in the current regime.

2. **Exit Overlay** (`exit_overlay.py`) — runs on ALL held positions regardless of entry strategy. Checks momentum, trend structure, volatility, correlation, and hold time. Catches regime transitions that the entry strategy misses. Active when:
   - Momentum turns negative (TSMOM-style 21d return < -2% + position losing)
   - Trend structure breaks (price < SMA20 < SMA50 + ADX > 20 + MACD negative)
   - Volatility spikes (ATR expands 1.5x from entry)
   - Correlation contagion (symbol correlates > 0.8 with losing BTC, or 3+ positions losing with this being worst)
   - Position held too long without profit (14-day max)

3. **Risk Manager** — mechanical stop-loss at -10%, trailing stop after +5% gain, take-profit scale-out at +15/20/25%. This is the last line of defense and executes at the worst price.

The goal: exit at Layer 1 or 2 (at -2% to -5%) instead of waiting for Layer 3 (at -10%). The exit overlay is the bridge between smart strategy exits and dumb mechanical stops.

### Exit Signal Priority

When multiple layers generate conflicting signals:
- EXIT always beats BUY (capital preservation)
- Exit overlay SELL + entry strategy SELL = boosted strength
- Exit overlay SELL + entry strategy BUY = exit wins, BUY suppressed
- Risk manager exits bypass everything (non-negotiable)

### Exit Overlay Signal Pipeline Position

```
Regime detection → Entry strategy → EXIT OVERLAY → Polymarket overlay → Execute
```

The exit overlay runs AFTER entry strategies and BEFORE Polymarket overlay. It generates SELL-only signals with strength 50-100. Signals are logged to `strategy_signals_log` with `strategy_name="exit_overlay"`.

---

## 6. Execution Gating

Before any trade executes, it must pass these checks in order:

1. **Risk check**: If circuit breaker is active, attempt auto-reset (24h cooldown or ≥80% cash ratio). If still active, reject all BUYs.
2. **Risk exits**: Process stop-loss/trailing-stop/take-profit exits before new signals.
3. **Strategy routing**: If a backtested strategy is assigned, route through it. Fallback to legacy logic if the strategy produces no signal.
4. **Strength gate**: Reject signals with strength < 50 (post-enhancement).
5. **Symbol dedup**: Skip BUY if same symbol was bought in the last 60 seconds.
6. **Position check**: Skip BUY if already holding (no doubling down).
7. **Hold time check**: Skip SELL if held < 12 hours (unless risk-driven).
8. **Session dedup (LLM path)**: `acted_this_session` set prevents duplicate (symbol, action) pairs per LLM session.

---

## 7. LLM Trading Agent Guardrails

The LLM agent (Regi) operates within hard constraints that cannot be overridden by the model:

| Guardrail | Value |
|-----------|-------|
| Max trades per session | 3 |
| Cash reserve floor | 10% of portfolio |
| Max single allocation | 15% of portfolio (20% with high conviction + confirmed trend) |
| Minimum hold period | 6–24h (default 12h; shorter holds require explicit justification) |
| Conviction gating | LLM must state conviction level; low conviction is suppressed |
| Session dedup | One action per (symbol, side) per session |

---

## 8. Backtested Strategies

Thirteen strategies have been backtested (143+ total backtests). Each can be assigned to a portfolio via `strategy_registry`.

### ScoreBased (Base)
RSI oversold/overbought at 30/70. Min strength 30.

### ScoreBasedRelaxed
Wider RSI bands (35/65). Lower min strength (20). Generates more signals.

### EnhancedScoreBased
Full enhancement pipeline (sentiment, ADX, volume, multi-timeframe, VIX). Uses ADX threshold of 25 instead of 30 for trending detection.

### MACDCross
Pure MACD-based: enters on histogram sign changes, exits on reversal. No RSI filter.

### ADXTrend
Only trades when ADX > 25 (confirmed trend). Strong trend bonus at ADX > 35. RSI guard: won't buy above 65 or sell below 35.

### BollingerReversion
Trades mean-reversion off Bollinger Bands in ranging markets (ADX < 25). Buys near lower band (position < 0.1), sells near upper (position > 0.9). Volume confirmation at 1.2×.

### MultiSignal
Consensus-based: counts 5 sub-signals (price/SMA trend, MACD direction, RSI zone, Bollinger position, volume). Needs ≥ 2.5 aligned in trending markets, ≥ 3.0 in ranging.

---

## 9. Advanced Scoring Model

Used by `advanced_scorer.py` for daily asset scoring across three timeframes.

### Timeframe Weights
| Timeframe | Weight |
|-----------|--------|
| Daily (1d) | 0.40 |
| 4-Hour | 0.35 |
| 1-Hour | 0.25 |

### Component Weights
| Component | Weight | Key Inputs |
|-----------|--------|------------|
| Momentum | 0.40 | RSI score, MACD histogram/cross, MA alignment |
| Trend | 0.25 | ADX strength, MA slope, golden/death cross |
| Volatility | 0.15 | ATR ratio, Bollinger width |
| Volume | 0.10 | Volume ratio tiers, 5-day volume trend |
| Risk-adjusted | 0.10 | Sharpe ratio, max drawdown, VaR(95%) |

### Key Scoring Rules

**Momentum — RSI sub-score:**
- 30 ≤ RSI ≤ 70: 80 (healthy range)
- RSI < 30: 60 + (30 − RSI) × 0.67 (oversold = opportunity)
- RSI > 70: 60 − (RSI − 70) × 0.67 (overbought = caution)

**Trend — ADX sub-score:**
- ADX > 50: 90
- ADX 25–50: 70 + (ADX − 25) × 0.8
- ADX 20–25: 50 + (ADX − 20) × 4
- ADX ≤ 20: ADX × 2.5

**Volume — ratio tiers:**
- \> 2.0×: 90 | 1.5–2.0×: 80 | 1.0–1.5×: 70 | 0.5–1.0×: 40 | < 0.5×: 20

---

## 10. Correlation & Regime Detection

### BTC-Equity Correlation
Tracked pairs: BTC/SPY, BTC/QQQ, BTC/ETH at 30, 60, 90-day windows. Minimum 30 days of data.

### Diversification Signals
- |correlation| < 0.3: Decorrelated asset — diversification opportunity
- Portfolio beta vs BTC > 1.5: Warning — portfolio amplifies BTC moves
- Portfolio beta vs BTC < 0.5: Good diversification

---

## 11. Paper vs Live Execution Limits

| Parameter | Paper | Live |
|-----------|-------|------|
| Initial capital | $1,000 | — |
| Transaction cost | 0.1% | Exchange fee schedule |
| Min trade size | $10 | $10 |
| Max single order | — | $50 |
| Max daily volume | — | $200 |
| Max open orders | — | 5 |
| Min order interval | — | 60 seconds |

---

## 12. Trend-Aware LLM Trading (P4 — Implemented)

The LLM agent (Regi) now operates with structured trend awareness and adjustable bounds.

### What the LLM sees
- Per-symbol trend classification: `uptrend`/`downtrend`/`ranging` with `strong`/`moderate`/`weak` strength
- Computed from SMA alignment (price vs SMA20 vs SMA50 vs SMA200) + ADX + MACD direction
- Full trend data included in the LLM prompt alongside signals, scores, regime, and journal history

### What the LLM can adjust (within bounds)

| Parameter | Range | Default | Condition for max |
|-----------|-------|---------|-------------------|
| Allocation % | 3–20% | 15% cap | High conviction + confirmed trend (ADX > 20) unlocks 20% |
| Hold period | 6–24h | 12h | Must justify shorter holds in reasoning |
| Sell sizing | 25–100% | Conviction-based | LLM specifies `sell_pct` directly |

### What remains hard rules
- Stop-loss (−10%), circuit breaker (−15%), position cap (25%), sector cap (40%)
- Session trade cap (3), cash reserve (10%), min trade ($10)
- Conviction gating: LOW blocked, MEDIUM needs signal confirmation, HIGH unconditional

### Structured reasoning (required)
Every LLM trade must include:
```json
{
  "thesis": "core trading thesis",
  "trend_regime": "uptrend|downtrend|ranging",
  "supporting": ["indicator1", "signal2"],
  "contradicting": ["counter_signal1"],
  "regime_consideration": "how VIX/macro affects this",
  "similar_past_outcome": "journal precedent or null",
  "risk_note": "downside scenario and mitigation"
}
```
Trades without reasoning are rejected. Full reasoning is stored in `decision_context` (trades table).

### Guardrail audit trail
Every executed trade records:
- What the LLM requested (allocation, hold period, sell_pct)
- What the guardrails modified (clamped values, raised/lowered caps)
- The full reasoning object
- Market regime, trend classification, and VIX at time of trade

Queryable via `edoras trades -v` or `SELECT decision_context FROM trades`.

### Trader IDs

Every trade must be attributed to a source via `trader_id`:

| trader_id | Source | Description |
|-----------|--------|-------------|
| 1 | Aleph | Main agent — DEX trading via Bankr |
| 2 | Regi (LLM Agent) | `trading_agent.py` — AI-powered strategic decisions |
| 3 | Signal Engine | `signal_trading.py` — quantitative signal-driven |
| 4 | Risk Engine | Automated risk management exits |
| 5 | Satyam | Human — manual trades and oversight |
| 6 | Weekly Rebalancer | `paper_rebalancing.py` — systematic weekly rebalance |

Trades without `trader_id` or `decision_context` are considered defective and indicate a code path that bypasses the audit system.
