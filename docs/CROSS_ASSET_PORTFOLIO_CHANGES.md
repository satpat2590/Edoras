# Cross-Asset Portfolio Support — Change Plan

## Objective

Transform portfolios from asset-class silos into **strategy repositories** that can
hold crypto, prediction markets, and equities simultaneously. A single portfolio
(e.g., Galadriel) should be able to hold BTC-USD, PM:FED-RATE-HOLD, and SPY if
the strategies recommend it.

## Guiding Principle

The database schema already supports cross-asset (`securities.asset_class`,
`securities.indicator_profile`, `exchanges.fee_model`). The gap is the execution
layer — `paper_trading.py`, `signal_trading.py`, `risk_manager.py`, and `config.py`
all hardcode crypto assumptions. This plan closes that gap by introducing a single
**asset-class profile lookup** that all modules resolve per-symbol.

---

## Change 1: Asset-Class Profile Registry (`config.py`)

**What:** Add `ASSET_CLASS_PROFILES` dict and a `get_asset_class_profile(symbol)`
resolver that returns the full parameter set for any symbol's asset class.

**Why:** Every downstream module needs the same set of per-asset parameters (fee %,
stop-loss %, position cap, etc.). Centralizing the lookup prevents scattered
`if asset_class == "crypto"` branches.

**Location:** `config.py` after the existing risk constants (line ~87).

**New code:**

```python
ASSET_CLASS_PROFILES = {
    "crypto": {
        "fee_pct": 0.001,              # 0.1% Coinbase maker
        "stop_loss_pct": 0.10,         # 10%
        "trailing_stop_activation": 0.05,
        "trailing_stop_pct": 0.05,
        "take_profit_levels": [(0.15, 0.33), (0.20, 0.33), (0.25, 1.00)],
        "max_position_pct": 0.25,      # 25% of portfolio per position
        "max_sector_pct": 0.40,
        "min_trade_usd": 10.0,
        "min_hold_hours": 12,
        "rsi_oversold": 30,
        "rsi_overbought": 70,
        "rsi_weak_oversold": 35,
        "rsi_weak_overbought": 65,
        "indicator_profile": "standard",
        "position_precision": 8,       # decimal places
    },
    "equity": {
        "fee_pct": 0.0,               # commission-free (most brokers)
        "stop_loss_pct": 0.07,         # 7% tighter
        "trailing_stop_activation": 0.05,
        "trailing_stop_pct": 0.04,
        "take_profit_levels": [(0.10, 0.33), (0.15, 0.33), (0.20, 1.00)],
        "max_position_pct": 0.15,      # 15% per position (more conservative)
        "max_sector_pct": 0.35,
        "min_trade_usd": 25.0,
        "min_hold_hours": 24,          # T+1 settlement
        "rsi_oversold": 35,
        "rsi_overbought": 65,
        "rsi_weak_oversold": 40,
        "rsi_weak_overbought": 60,
        "indicator_profile": "standard",
        "position_precision": 4,
    },
    "prediction": {
        "fee_pct": 0.02,              # 2% Polymarket spread
        "stop_loss_pct": 0.15,         # 15% (binary markets are volatile)
        "trailing_stop_activation": 0.10,
        "trailing_stop_pct": 0.08,
        "take_profit_levels": [(0.30, 0.50), (0.50, 1.00)],
        "max_position_pct": 0.10,      # 10% cap (binary risk)
        "max_sector_pct": 0.25,
        "min_trade_usd": 5.0,
        "min_hold_hours": 1,           # event-driven, fast turnover
        "rsi_oversold": None,          # not applicable (binary profile)
        "rsi_overbought": None,
        "rsi_weak_oversold": None,
        "rsi_weak_overbought": None,
        "indicator_profile": "binary",
        "position_precision": 2,
    },
    "index": {
        "fee_pct": 0.0,
        "stop_loss_pct": 0.05,
        "trailing_stop_activation": 0.03,
        "trailing_stop_pct": 0.03,
        "take_profit_levels": [(0.08, 0.33), (0.12, 0.50), (0.18, 1.00)],
        "max_position_pct": 0.20,
        "max_sector_pct": 0.50,
        "min_trade_usd": 50.0,
        "min_hold_hours": 24,
        "rsi_oversold": 35,
        "rsi_overbought": 65,
        "rsi_weak_oversold": 40,
        "rsi_weak_overbought": 60,
        "indicator_profile": "standard",
        "position_precision": 4,
    },
}


def get_asset_class_profile(symbol: str) -> dict:
    """Resolve the full parameter profile for a symbol's asset class.

    Queries securities.asset_class from the DB, falls back to get_asset_type()
    heuristic, then returns the matching profile from ASSET_CLASS_PROFILES.
    Defaults to 'crypto' if nothing matches.
    """
    asset_class = get_asset_type(symbol)
    return ASSET_CLASS_PROFILES.get(asset_class, ASSET_CLASS_PROFILES["crypto"]).copy()
```

**Result cache:** `get_asset_type()` already queries the DB. For hot-path usage
(risk checks on every cycle), results are cached in-memory within the calling
module. The profile dict is cheap to copy.

---

## Change 2: Dynamic Fee Model (`paper_trading.py`)

**What:** Replace hardcoded `self.transaction_cost = 0.001` with per-trade
fee resolution via `get_asset_class_profile(symbol)`.

**Where:** `paper_trading.py`
- Line 55: Remove hardcoded `self.transaction_cost = 0.001`
- `execute_buy()` (line ~314): Resolve fee from profile before applying
- `execute_sell()` (line ~385): Same

**Before:**
```python
self.transaction_cost = 0.001
...
cost = amount_usd * self.transaction_cost
```

**After:**
```python
from config import get_asset_class_profile
...
profile = get_asset_class_profile(symbol)
cost = amount_usd * profile["fee_pct"]
```

The `self.transaction_cost` field is kept as a fallback default but is no longer
the primary fee source.

---

## Change 3: Asset-Class-Aware Risk Manager (`risk_manager.py`)

**What:** Replace module-level constant imports with per-symbol profile lookups.

**Where:** `risk_manager.py`
- Line 19-28: Keep imports as fallback defaults
- `check_stop_loss()` (line ~156): Resolve `stop_loss_pct` from profile
- `check_trailing_stop()` (line ~184): Resolve activation and trail pct
- `check_take_profit()` (line ~224): Resolve take-profit levels
- `check_position_concentration()` (line ~280): Resolve max position pct

**Approach:** Add a `_get_profile(symbol)` helper method that calls
`get_asset_class_profile(symbol)` and caches per symbol for the check cycle.
Each check method reads its threshold from the profile instead of the module
constant. The module constants remain as fallback defaults.

**Before:**
```python
stop_price = entry * (1.0 - STOP_LOSS_PCT)
```

**After:**
```python
profile = self._get_profile(symbol)
stop_price = entry * (1.0 - profile["stop_loss_pct"])
```

---

## Change 4: Signal Indicator Dispatch (`signal_trading.py`)

**What:** Before generating signals for a symbol, check its `indicator_profile`
and use the appropriate indicator set. Prediction market symbols should use binary
indicators, not standard RSI/MACD.

**Where:** `signal_trading.py`
- `get_indicator_window()` (line ~253): Detect indicator profile and select
  columns accordingly
- `check_trading_signals()` (line ~520): Use profile-aware RSI thresholds
- `execute_paper_trades()` (line ~858): Use profile-aware min_trade_usd and
  min_hold_hours

**Key changes:**

1. In `get_indicator_window()`, detect profile and join the correct indicator columns:
   - `standard` → existing 17 indicator columns
   - `binary` → 16 binary indicator columns from `BINARY_INDICATOR_COLUMNS`

2. In `check_trading_signals()`, resolve RSI thresholds from profile:
   ```python
   profile = get_asset_class_profile(symbol)
   rsi_oversold = profile["rsi_oversold"] or 30
   ```

3. In `execute_paper_trades()`, resolve min trade and hold period:
   ```python
   profile = get_asset_class_profile(symbol)
   if buy_amount < profile["min_trade_usd"]: skip
   if held_hours < profile["min_hold_hours"]: skip
   ```

---

## Change 5: Position Sizing Caps (`signal_trading.py`)

**What:** Replace hardcoded `max_pct = 0.25` and `buy_amount < 10.0` with
profile-driven values.

**Where:** `signal_trading.py` `execute_paper_trades()` (lines ~870-976)

**Before:**
```python
max_pct = 0.25
alloc_pct = min(alloc_pct, max_pct)
...
if buy_amount < 10.0:
```

**After:**
```python
profile = get_asset_class_profile(symbol)
max_pct = profile["max_position_pct"]
alloc_pct = min(alloc_pct, max_pct)
...
if buy_amount < profile["min_trade_usd"]:
```

---

## Files Modified (summary)

| File | Change | Risk |
|------|--------|------|
| `config.py` | Add `ASSET_CLASS_PROFILES` dict + `get_asset_class_profile()` | None — additive only |
| `paper_trading.py` | Dynamic fee per symbol | Low — fallback to 0.1% if profile lookup fails |
| `risk_manager.py` | Per-symbol risk thresholds via profile | Low — falls back to existing constants |
| `signal_trading.py` | Profile-aware indicator dispatch, RSI thresholds, position sizing, min trade, min hold | Medium — touches signal generation path |

## Files NOT Modified

| File | Why |
|------|-----|
| `indicator_calculator.py` | Already has both `calculate_all_indicators()` and `calculate_binary_indicators()` — no change needed |
| `backtest/strategies/*` | Strategies receive a DataFrame — the caller is responsible for providing the right indicators. No strategy changes needed. |
| `risk_manager.py` structure | Same check methods, same ExitSignal format — only threshold sources change |
| Database schema | `securities.asset_class`, `exchanges.fee_model` already exist — no migration |

## Documentation Updates Required

| File | Action |
|------|--------|
| `docs/CROSS_ASSET_PORTFOLIO_CHANGES.md` | This document (created) |
| `docs/ARCHITECTURE.md` | Add cross-asset profile section |
| `docs/TRADING_PHILOSOPHY.md` | Add per-asset-class risk parameter table |
| Regi CLAUDE.md (`~/.openclaw/workspace-quant/CLAUDE.md`) | Add asset-class profile reference |
| Aleph CLAUDE.md (`~/.openclaw/workspace/CLAUDE.md`) | Add cross-asset awareness note |

## Revision History

| Rev | Date | Description |
|-----|------|-------------|
| 1.0 | 2026-03-22 | Initial plan — 5 changes across 4 files |
| 1.1 | 2026-03-22 | All 5 changes implemented and verified. Documentation updated: TRADING_PHILOSOPHY.md (per-asset risk tables), ARCHITECTURE.md (cross-asset section), Regi CLAUDE.md (risk table + PM overlay), Aleph CLAUDE.md (cross-asset + HMM + PM notes) |
