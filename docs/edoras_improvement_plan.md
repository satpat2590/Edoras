# Edoras Trading System — Implementation Log
*Last Updated: 2026-04-05*
*Author: Satya (Research Partner)*
*Status: All 6 phases complete*

---

## Overview

This document is the implementation record for the Edoras system improvement project.
It covers what was wrong, what was found during analysis, what was changed, and how to
verify each change is working. Everything listed here has been built and tested.

---

## What the System Looked Like Before

### Architecture (pre-improvement)
- **13 backtested strategies** registered, but 4 were redundant re-parameterisations
- **Signal engine had a legacy RSI/MACD fallback** (~366 lines) that ran for any symbol not
  explicitly routed to a backtested strategy — effectively a 5th copy of the same logic
- **93.5% of trades** (signal engine + rebalancer) had **zero LLM oversight**
- **Equity 4h data was ~20 days stale** — `equity_data_collector.py` had no 4h aggregation
- **4 Telegram alert systems were silently disabled** via early-return stubs
- **Risk guardian only ran 07:00–22:30 EDT** — 8.5h overnight gap for a 24/7 crypto system
- **No database backup** — 276MB SQLite was a single point of failure
- **WebSocket zombie connections** (connected but no data flowing) were not detected
- **No data freshness monitoring** — stale feeds produced signals silently
- **Scoring model** was 65% pro-cyclical (momentum 40% + trend 25%) with no quality factor
- **Smart rebalancer** had no category caps — meme coins could freely inflate to high allocations

### Key Corrections from Initial Plan
The original improvement plan had several factual errors that were fixed during codebase analysis:

| Claim in original plan | Reality found |
|------------------------|---------------|
| "4h crypto data is 18 days stale" | Crypto 4h was current (WebSocket rolls up 1h→4h). It was **equity 4h** that was stale. |
| "8.8% LLM trade ratio" | Had dropped to 6.5% (12/185 trades) as automated trades accumulated |
| "Single execution path, intercept with LLM" | System has **4 independent paths**: Signal Engine, Trading Agent, Risk Guardian, Rebalancer |
| "Telegram alerts missing" | Alert code existed but had `return` stubs disabling it |

---

## Phase 1 — Data Infrastructure ✅
*Completed: 2026-04-05*

### Problem
Equity 4h data was ~20 days stale because `equity_data_collector.py` never built 4h candles
from its hourly data. The signal engine applies 35% weight to 4h equity timeframe
(`TIMEFRAME_WEIGHTS_EQUITY` in config), so it was silently weighting stale data.
There was also no automated monitoring to catch feed staleness.

### What Was Changed

**`equity_data_collector.py`**
- Added `aggregate_4h_candles(symbol, lookback_days)` method — builds 4h candles from 1h
  data using UTC 4h boundaries (same algorithm as crypto, but `min_count=1` since equity
  markets aren't 24/7). Now produces 1,100–1,741 4h candles per equity symbol.
- `collect_all()` and `update_latest()` now call `aggregate_4h_candles()` + `calculate_indicators()`
  for the 4h timeframe after fetching 1h data.
- `--validate` output updated to include 4h in its coverage check.

**`crypto_data_collector.py`** — `run_daily_collection()`
- Removed `'4h'` from the REST fetch loop `['1d', '4h', '1h']` (Coinbase REST doesn't
  serve 4h natively — this was a no-op that wasted an API call per symbol per run).
- Added explicit `aggregate_4h_candles()` call after 1h collection, with its own
  `calculate_indicators()` pass.

**`data_freshness_monitor.py`** *(new file, ~464 lines)*
- Per-symbol, per-timeframe staleness checks against configurable thresholds:
  `1h: 3h`, `4h: 10h`, `1d: 28h` for crypto; wider for equity (covers weekends/holidays).
- Gap detection: identifies missing consecutive candle intervals within the last 7 days.
- Telegram alerting on any stale or missing feed.
- CLI: `python3 data_freshness_monitor.py --report --gaps --no-alert`
- Deployed as `data-freshness-monitor.timer` (every 15 minutes).

**`signal_trading.py`** — `SignalTradingSystem`
- Added `_FRESHNESS_THRESHOLDS` dict and `_is_data_fresh(symbol, timeframe)` method.
- In `check_all_symbols()`, each symbol is now checked for data freshness before signal
  generation. Stale symbols are skipped with a warning log.

**`realtime/ingest/base_websocket.py`**
- Added constants: `LIVENESS_INTERVAL = 300`, `SYMBOL_IDLE_WARN = 300`, `FEED_IDLE_CRITICAL = 600`.
- Added `_check_liveness()` method — warns on per-symbol idle >5 min; forces reconnect if
  entire feed silent for 10 min (zombie connection detection).
- `_message_loop()` calls `_check_liveness()` every `LIVENESS_INTERVAL` seconds. If zombie
  detected, breaks out of the loop and raises `ConnectionError` to trigger the supervisor's
  reconnect logic.

### Verification
```bash
python3 data_freshness_monitor.py --report --gaps --no-alert
# Should show: Checked: 93 feeds | OK: 93 | STALE: 0

python3 -c "
import sqlite3, time
conn = sqlite3.connect('crypto_data.db')
for sym in ['AAPL', 'MSFT', 'SPY']:
    r = conn.execute('SELECT COUNT(*), MAX(timestamp) FROM candlesticks WHERE symbol=? AND timeframe=?', (sym, '4h')).fetchone()
    age_h = (time.time() - r[1]) / 3600 if r[1] else 999
    print(f'{sym} 4h: {r[0]} candles, {age_h:.1f}h old')
"
```

---

## Phase 2 — Alerting & 24/7 Coverage ✅
*Completed: 2026-04-05*

### Problem
Four separate Telegram alert systems all had working send logic that was disabled via
`return` stubs (silencing them during testing and never re-enabling). Risk guardian
only ran during daytime hours. No database backup existed.

### What Was Changed

**`risk_guardian.py`** — `_send_alert()`
- Removed early `return` statement. Stop-loss, trailing stop, take-profit, and
  circuit breaker exits now send immediate Telegram notifications.

**`price_alerts.py`** — `send_alert_via_telegram()`
- Removed early `return` statement. Price threshold breach alerts now deliver.

**`signal_alerts.py`** — `send_telegram_alert()`
- Removed early `return` statement. Signal strength alerts now deliver.

**`realtime/risk/real_time_risk.py`**
- Added `_send_telegram(message, critical=False)` helper method using `subprocess.run(curl ...)`.
- Replaced three `# TODO: Send Telegram alert` placeholders:
  - After `trigger_exit()`: sends exit type, symbol, quantity, price, reason.
  - After `trigger_partial_exit()`: sends take-profit details.
  - After `check_circuit_breaker()`: sends critical alert with drawdown % and position count.

**`~/.config/systemd/user/risk-guardian.timer`**
- Changed `OnCalendar` from `*-*-* 07..22:00,30:00` (daytime only) to `*:00,30`
  (every 30 minutes, all hours, every day). Crypto doesn't have market hours.

**`scripts/backup_db.sh`** *(new file)*
- Uses `sqlite3 "$DB_PATH" ".backup '$BACKUP_FILE'"` — safe hot backup API that works
  while the database is in active use.
- Retains 7 daily backups in `~/edoras/backups/`, prunes older ones with `find -mtime +7`.

**`~/.config/systemd/user/db-backup.timer`** + **`db-backup.service`** *(new files)*
- Runs `backup_db.sh` daily at 03:00 UTC. `SuccessExitStatus=0 1` so informational
  exit codes don't trigger service failure alerts.

### Verification
```bash
# Check all timers are active
systemctl --user list-timers | grep -E "risk-guardian|db-backup|freshness"

# Test backup manually
bash scripts/backup_db.sh
ls -lh backups/

# Confirm alert stubs are removed
grep -n "return$\|# TODO.*Telegram" risk_guardian.py price_alerts.py signal_alerts.py
# Should return no results
```

---

## Phase 3 — LLM Governance Layer ✅
*Completed: 2026-04-06*

### Problem
The signal engine (35% of all trades) and weekly rebalancer (6%) had zero LLM oversight.
The trading agent's 5-tier LLM fallback chain was duplicated in one 120-line method.
No systematic validation happened between "strategy generated a signal" and "trade executes".

### What Was Changed

**`llm_chain.py`** *(new file, ~290 lines)*

Shared LLM service used by both the trading agent and the gatekeeper:
- **5-tier provider fallback**: DeepSeek → Nous Research → Claude Sonnet → GPT-4o → MLX (local).
  Each provider resolved from env vars at init (`DEEPSEEK_API_KEY`, `NOUS_RESEARCH_API_KEY`,
  `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `MLX_BASE_URL`).
- **Per-call timeout** (configurable; 15s for gatekeeper, 30s for trading agent).
- **TTL-based response cache** keyed on SHA-256 of prompt (LRU, 64-entry max).
- **Per-provider rate limiter** (token bucket, configured RPM per provider).
- **JSON parser** that strips markdown fences and falls back to substring extraction.
- **Guaranteed fallback**: `call()` never returns `None`; `call_with_parse()` never raises.
  Total provider failure returns a safe "hold all positions" JSON.

Key API:
```python
chain = LLMChain(system_prompt="...", timeout=15, cache_ttl=300)
response: str  = chain.call(prompt)
response: dict = chain.call_with_parse(prompt)   # parsed JSON, fallback on failure
chain.available_providers()  # ['DeepSeek', 'Nous Research', 'Claude', 'OpenAI', 'MLX']
```

**`llm_gatekeeper.py`** *(new file, ~250 lines)*

Validates BUY signals from the signal engine before execution:
- **Batch validation**: all BUY signals for a cycle sent in one LLM call (not per-signal).
  The prompt is lightweight — ~20 lines vs the trading agent's 100+ line context.
- **SELL bypass**: SELL signals (exit overlay, risk exits, stop-loss) are never sent to the
  gatekeeper. They must execute without LLM latency.
- **Decisions**: APPROVE / REJECT / MODIFY. MODIFY reduces signal strength to LLM confidence.
- **Fail-open** (`timeout_passthrough=True`): any exception, timeout, or parse failure causes
  all signals to pass through with `gatekeeper_decision = "approve"`. The system degrades
  to pre-gatekeeper behaviour; no trades are blocked.
- **5-minute result cache** keyed on `(symbol, action, strength_bucket)` — repeated identical
  signals within a 5-min window reuse the prior verdict.
- **Audit trail**: every validated signal gets `gatekeeper_decision`, `gatekeeper_reasoning`,
  `gatekeeper_confidence` fields added, which flow into `decision_context` in the trades table.

**`signal_trading.py`** — `SignalTradingSystem.__init__()` + `run_portfolio()`
- Gatekeeper initialised in `__init__()` with graceful try/except (logs warning if import fails).
- In `run_portfolio()`, between `check_all_symbols()` and `execute_paper_trades()`:
  - BUY signals separated from SELL signals.
  - Portfolio state snapshot built for gatekeeper context.
  - Regime label fetched from `correlation_tracker`.
  - `gatekeeper.validate_signals(buy_signals, pf_state, regime)` called.
  - Approved BUYs rejoined with SELLs before execution.

**`smart_rebalancer.py`** — `SmartRebalancer.__init__()` + `execute_rebalance()`
- Added optional `gatekeeper=None` parameter to `__init__()`.
- Before executing BUY rebalance trades, converts them to signal-like dicts and calls
  `gatekeeper.validate_signals()`. Rejected symbols are skipped; approved proceed.
- Fail-safe: gatekeeper exception causes all BUYs to proceed (never blocks a rebalance).

**`trading_agent.py`**
- Removed the entire 120-line `_call_llm()` provider cascade and `_parse_llm_response()`.
- Added `self._llm_chain = _LLMChain(...)` in `_init_components()`.
- `_call_llm(prompt)` now delegates to `self._llm_chain.call(prompt)`.
- `_parse_llm_response(response)` now delegates to `_LLMChain._parse_json(response)`.
- Provider env var constants (`DEEPSEEK_API_KEY`, etc.) removed from module level.

### Verification
```bash
# Verify imports and fail-open behaviour
python3 -c "
from llm_chain import LLMChain
from llm_gatekeeper import LLMGatekeeper

# Short timeout → all providers fail → fail-open
chain = LLMChain(timeout=1, cache_ttl=0, fallback_json={'decisions': []})
gk = LLMGatekeeper(chain=chain)
sigs = [{'symbol': 'BTC-USD', 'action': 'BUY', 'strength': 65,
         'reason': 'test', '_strategy_name': 'EnhancedScoreBased', '_timeframe': '4h'}]
result = gk.validate_signals(sigs, {}, 'risk-on')
print('Decision:', result[0]['gatekeeper_decision'])  # approve (fail-open)

# Verify trading agent uses shared chain
from trading_agent import TradingAgent, _LLMChain
ta = TradingAgent.__new__(TradingAgent)
ta._init_components()
print('Chain providers:', ta._llm_chain.available_providers())
"
```

---

## Phase 4 — Strategy Consolidation ✅
*Completed: 2026-04-06*

### Problem
- 13 strategies registered, but 4 were redundant (trivial re-parameterisations or subsets)
- `signal_trading.py` had a legacy RSI+MACD signal path (366 lines across 4 methods) that
  ran for any symbol not explicitly routed — a 5th copy of the same logic
- No strategy existed for bear market regimes — `STRATEGY_REGIME_FIT` had no `best: "bear"` entry

### What Was Changed

**`backtest/strategies/score_based.py`**
- Removed `@register_strategy` from `ScoreBasedStrategy` (superseded by `EnhancedScoreBased`).
- Removed `register_strategy(ScoreBasedRelaxedStrategy)` call (trivial re-parameterisation).
- Both classes kept in file — `EnhancedScoreBasedStrategy` inherits from `ScoreBasedStrategy`,
  and the catalogue may reference them.

**`backtest/strategies/macd_cross.py`**
- Removed `@register_strategy` from `MACDCrossStrategy` (MACD is already a component of
  every other strategy; no standalone edge).

**`backtest/strategies/pairs_trading.py`**
- Removed `@register_strategy` from `PairsTradingAggressiveStrategy` (trivial
  re-parameterisation of `PairsTrading` with tighter thresholds).

**`backtest/strategies/bear_defensive.py`** *(new file, ~130 lines)*
- `BearDefensiveStrategy` — tighter mean-reversion for bear/risk-off regimes:
  - **Entry**: BB position < 0.05 AND RSI < 25 (deeper oversold than BollingerReversion's 0.15/30)
  - **Exit**: BB position > 0.30 OR RSI > 40 (exits early — doesn't wait for full recovery)
  - **Max signal weight**: 0.60 (conservative position sizing)
  - **ADX guard**: skips if ADX > 60 (possible flash crash — avoid catching a falling knife)
  - Philosophy: silence = stay in cash (correct bear-market posture)

**`backtest/strategies/__init__.py`**
- Added `bear_defensive` to the import list to trigger registration.

**`signal_trading.py`**
- Removed 4 legacy methods (366 lines): `check_trading_signals()`, `enhance_signal()`,
  `multi_timeframe_alignment()`, `get_latest_sentiment()`.
- Removed the legacy fallback branch in `check_all_symbols()` that called these methods for
  unrouted symbols.
- `build_strategy_routes()` now accepts `portfolio_symbols` and `default_timeframe` params.
  Any symbol in the portfolio not explicitly configured in `strategy_routes_json` is
  automatically assigned `MultiSignal` at init time. No symbol falls through to legacy logic.
- `SignalTradingSystem.__init__()` passes `portfolio_symbols=self.PORTFOLIO_SYMBOLS` to
  `build_strategy_routes()`.

**`regime_monitor.py`** — `STRATEGY_REGIME_FIT`
- Added `"BearDefensive": {"best": "bear", "ok": ["sideways"]}` — first entry with `best: "bear"`.
- Added retirement comments to the 4 unregistered strategies' entries.

### Final Registered Strategy Registry (10 strategies)

| Strategy | Type | Best Regime |
|----------|------|-------------|
| `EnhancedScoreBased` | Mean-reversion | bull/sideways |
| `ADXTrend` | Trend-following | bull |
| `TSMOM` | Momentum + vol-sizing | bull |
| `TSMOM_3M` | Momentum (3-month) | bull/sideways |
| `BollingerReversion` | Mean-reversion | sideways/bear |
| `PairsTrading` | Stat-arb | sideways |
| `MultiSignal` | Consensus (5 sub-signals) | all (default) |
| `RegimeAware` | Adaptive (HMM-driven) | all |
| `RegimeAware_Heuristic` | Adaptive (no HMM) | all |
| `BearDefensive` | Tight mean-reversion | **bear** |

### Verification
```bash
python3 -c "
from backtest.strategies import STRATEGY_REGISTRY
print('Count:', len(STRATEGY_REGISTRY))           # 10
print('Has BearDefensive:', 'BearDefensive' in STRATEGY_REGISTRY)   # True
print('ScoreBased gone:', 'ScoreBased' not in STRATEGY_REGISTRY)    # True
print('MACDCross gone:', 'MACDCross' not in STRATEGY_REGISTRY)      # True

from signal_trading import SignalTradingSystem
import inspect
src = inspect.getsource(SignalTradingSystem)
print('Legacy removed:', 'check_trading_signals' not in src)        # True
print('Default routing:', 'MultiSignal' in src)                     # True
"
```

---

## Phase 5 — Portfolio Management ✅
*Completed: 2026-04-06*

### Problem
- Scoring model was 65% pro-cyclical (momentum 40% + trend 25%), causing the rebalancer to
  overweight recent winners and accumulate meme coins during bull runs
- No quality factor — a meme coin with strong recent momentum scored identically to BTC
- Smart rebalancer had no category caps — meme allocation was unbounded
- Rebalance trades executed regardless of whether fees exceeded the drift correction benefit

### What Was Changed

**`config.py`**

Added three new exported dicts:

```python
SCORER_WEIGHTS = {
    "momentum":      0.25,  # was 0.40
    "trend":         0.15,  # was 0.25
    "risk_adjusted": 0.25,  # was 0.10
    "volatility":    0.15,  # unchanged
    "volume":        0.10,  # unchanged
    "quality":       0.10,  # new
}

SYMBOL_TIERS = {
    "BTC-USD": "large", "ETH-USD": "large", ...  # 43 symbols
    "BONK-USD": "meme", "PEPE-USD": "meme", ...
}

TIER_QUALITY_SCORES = {"large": 90, "mid": 70, "small": 50, "meme": 30}
```

Weights are now configurable without touching scorer code. They must sum to 1.0.

**`advanced_scorer.py`**

- `__init__()` now reads `self.component_weights = dict(SCORER_WEIGHTS)` from config
  instead of hardcoded values.
- Added `calculate_quality_score(symbol) → float` method:
  - **Tier score** (50% weight): looks up `SYMBOL_TIERS[symbol]`, maps to
    `TIER_QUALITY_SCORES` (large=90, mid=70, small=50, meme=30).
  - **30-day Sharpe** (30% weight): fetches last 30 days of daily closes, computes
    annualised Sharpe. Mapped to 0–100: Sharpe=0 → 50, Sharpe=2 → 90, Sharpe=-1 → 25.
  - **Volume ratio** (20% weight): reads latest `volume_ratio` from indicators table.
    Maps 0.5x → 35, 1.0x → 50, 2.0x → 70.
- `calculate_total_score()` now calls `calculate_quality_score()` and includes `quality`
  in its returned dict. Handles missing `quality` key in `component_weights` gracefully.
- `score_multiple_symbols()` includes `quality` column in output DataFrame.

**`smart_rebalancer.py`**

`compute_target_weights()` completely rewritten with four ordered constraint layers
(applied on progressively normalised weights so later caps aren't undone by normalisation):

1. **Per-position cap** (`MAX_POSITION_PCT`): same as before.
2. **Per-sector cap** (`MAX_SECTOR_PCT`): same as before.
3. **Normalise** (then apply category constraints on normalised weights).
4. **Category caps** with redistribution:
   - `CATEGORY_CAPS = {"meme": 0.10, "small": 0.15}`
   - Freed weight redistributed proportionally to non-capped symbols (total stays 1.0).
5. **Large-cap floor** (`LARGE_CAP_FLOOR = 0.40` for BTC+ETH+BNB+SOL combined):
   - If large-cap total < 40%, deficit taken from non-large-cap proportionally and
     given to large-cap proportionally.

**Quality gate** (applied before weight computation):
- Symbols with `TIER_QUALITY_SCORES[tier] < QUALITY_GATE_MIN_SCORE (35)` are excluded
  from the eligible universe entirely. This excludes meme tier (score=30) by default.
- Falls back to all symbols if quality gate leaves fewer than `min_positions`.

`execute_rebalance()` — **transaction cost gate**:
- Before executing any trade (BUY or SELL), checks:
  `estimated_fee > 0.50 × drift_benefit`
- If true, skips the trade with a log message. Prevents micro-corrections where the fee
  exceeds half the value of the drift.

### Scoring Weight Comparison

| Component | Before | After | Effect |
|-----------|--------|-------|--------|
| Momentum | 40% | 25% | Less bias toward recent winners |
| Trend | 25% | 15% | Reduces overlap with momentum |
| Risk-Adjusted | 10% | 25% | Filters low-quality assets |
| Volatility | 15% | 15% | Unchanged |
| Volume | 10% | 10% | Unchanged |
| Quality | 0% | 10% | New: penalises meme tier |

### Verification
```bash
python3 -c "
from config import SCORER_WEIGHTS, SYMBOL_TIERS
assert abs(sum(SCORER_WEIGHTS.values()) - 1.0) < 0.001
print('Weights sum to 1.0 ✓')
print('BTC tier:', SYMBOL_TIERS['BTC-USD'])    # large
print('BONK tier:', SYMBOL_TIERS['BONK-USD'])  # meme

from advanced_scorer import AdvancedScoringModel
s = AdvancedScoringModel()
print('Quality in weights:', 'quality' in s.component_weights)  # True
print('BTC-USD quality score:', s.calculate_quality_score('BTC-USD'))

from smart_rebalancer import SmartRebalancer
sr = SmartRebalancer()
# BONK and PEPE have high momentum scores — category cap must hold them to 10%
scores = {'BTC-USD': 80, 'ETH-USD': 75, 'BONK-USD': 90, 'PEPE-USD': 88, 'SOL-USD': 70}
w = sr.compute_target_weights(scores)
meme = sum(v for k, v in w.items() if k in {'BONK-USD', 'PEPE-USD'})
print(f'Meme allocation: {meme:.1%} (must be ≤ 10%)')  # 10.0%
"
```

---

## Complete File Change Log

| File | Change | Phase |
|------|--------|-------|
| `equity_data_collector.py` | Added `aggregate_4h_candles()` method; wired into `collect_all()` + `update_latest()` | 1 |
| `crypto_data_collector.py` | Removed `'4h'` from REST fetch loop; added explicit 4h aggregation step | 1 |
| `data_freshness_monitor.py` | **New file** — staleness + gap detection + Telegram alerts | 1 |
| `run_data_freshness_monitor.sh` | **New file** — shell wrapper for systemd | 1 |
| `~/.config/systemd/user/data-freshness-monitor.{service,timer}` | **New files** — runs every 15 min | 1 |
| `signal_trading.py` | Added `_is_data_fresh()` + freshness gate in `check_all_symbols()` | 1 |
| `realtime/ingest/base_websocket.py` | Added `_check_liveness()` + zombie reconnect logic | 1 |
| `risk_guardian.py` | Removed early `return` in `_send_alert()` | 2 |
| `price_alerts.py` | Removed early `return` in `send_alert_via_telegram()` | 2 |
| `signal_alerts.py` | Removed early `return` in `send_telegram_alert()` | 2 |
| `realtime/risk/real_time_risk.py` | Added `_send_telegram()`; replaced 3× `# TODO` with real alerts | 2 |
| `~/.config/systemd/user/risk-guardian.timer` | `OnCalendar` → `*:00,30` (24/7) | 2 |
| `scripts/backup_db.sh` | **New file** — hot SQLite backup, 7-day retention | 2 |
| `~/.config/systemd/user/db-backup.{service,timer}` | **New files** — daily 03:00 UTC | 2 |
| `llm_chain.py` | **New file** — shared 5-tier LLM service with caching + rate limiting | 3 |
| `llm_gatekeeper.py` | **New file** — fail-open BUY signal validator | 3 |
| `signal_trading.py` | Gatekeeper init in `__init__()` + gate in `run_portfolio()` | 3 |
| `smart_rebalancer.py` | Added `gatekeeper` param + BUY validation in `execute_rebalance()` | 3 |
| `trading_agent.py` | Replaced `_call_llm()` + `_parse_llm_response()` with shared `LLMChain` | 3 |
| `backtest/strategies/score_based.py` | Removed `@register_strategy` from `ScoreBased`, `ScoreBasedRelaxed` | 4 |
| `backtest/strategies/macd_cross.py` | Removed `@register_strategy` from `MACDCross` | 4 |
| `backtest/strategies/pairs_trading.py` | Removed `@register_strategy` from `PairsTrading_Aggressive` | 4 |
| `backtest/strategies/bear_defensive.py` | **New file** — bear-market strategy | 4 |
| `backtest/strategies/__init__.py` | Added `bear_defensive` import | 4 |
| `signal_trading.py` | Removed 366 lines of legacy signal methods; default MultiSignal routing | 4 |
| `regime_monitor.py` | Added `BearDefensive` to `STRATEGY_REGIME_FIT` | 4 |
| `config.py` | Added `SCORER_WEIGHTS`, `SYMBOL_TIERS`, `TIER_QUALITY_SCORES` | 5 |
| `advanced_scorer.py` | Weights from config; added `calculate_quality_score()`; updated `calculate_total_score()` | 5 |
| `smart_rebalancer.py` | Category caps + quality gate + transaction cost gate in `compute_target_weights()` + `execute_rebalance()` | 5 |

**New files created: 9**
**Files modified: 20**
**Lines removed (legacy code): ~550**
**Lines added (new functionality): ~1,400**

---

## Phase 6 — Modularity & Improvements ✅
*Completed: 2026-04-05*

### What Was Changed

#### 6.1 Dead Code Cleanup — `trading_agent.py`

- Removed 22 lines of unreachable old JSON parsing code after `return` in `_parse_llm_response` (leftover from Phase 3 refactor).
- Removed the dead `optimizer` lazy property (`self._optimizer = None`, `@property optimizer`, and its `EnhancedPortfolioOptimizer` import) — the property was defined but never called anywhere in the file.

**Net**: ~30 lines removed, no behaviour change.

#### 6.2 BearDefensive Walk-Forward Validation

Ran `anchored_walk_forward()` + `holdout_gate()` from `backtest/validation.py` on BTC-USD, ETH-USD, SOL-USD.

**Results (BTC-USD, 4-fold walk-forward 2023-06-01 → 2026-03-01):**

| Fold | IS period | OOS period | IS Sharpe | OOS Sharpe | OOS trades | Passed |
|------|-----------|------------|-----------|------------|------------|--------|
| 1 | 2023-06 → 2025-03 | 2025-03 → 2025-06 | 1.48 | **4.35** | 1 | ✗ (trades < 5) |
| 2 | 2023-06 → 2025-06 | 2025-06 → 2025-09 | 1.42 | **0.00** | 0 | ✗ (trades = 0) |
| 3 | 2023-06 → 2025-09 | 2025-09 → 2025-12 | 1.50 | **0.49** | 1 | ✗ (trades < 5) |
| 4 | 2023-06 → 2025-12 | 2025-12 → 2026-03 | 1.42 | **-3.87** | 2 | ✗ (Sharpe < 0) |

**Average OOS Sharpe: 0.24. Holdout gate: 0/3 symbols approved.**

**Failure mode**: Fold 4 OOS (Dec 2025 – Mar 2026) coincided with the Feb 2026 BTC flash crash: -14% in a single day. The strategy entered twice on RSI<25 signals and was stopped out both times within 5 days. The `max_adx=60` guard did not trigger because ADX lagged the price move.

**In-sample performance is strong**: Sharpe 1.42–1.50, win rate 80–85%, max DD 5.7%, profit factor 6.5–9.0 across the full 2.5-year IS period. The strategy concept is sound for gradual bear markets.

**Decision**: BearDefensive remains registered but is NOT routed to any live symbol. Re-validation is required after one of:
- Lowering `max_adx` to 35 (more aggressive crash avoidance)
- Adding a 1-day realised vol filter: skip entry if `ATR_14 / close > 5%`

#### 6.3 True Portfolio Optimizer — `enhanced_optimizer.py`

Replaced the scoring-only `EnhancedPortfolioOptimizer` with a true mean-variance `PortfolioOptimizer`. `EnhancedPortfolioOptimizer` is kept as a class alias so existing callers (`paper_rebalancing.py`, `paper_trading.py`) require no changes.

**New optimization methods** (all use `scipy.optimize.minimize` SLSQP with graceful fallback):

| Method | Algorithm | Fallback chain |
|--------|-----------|----------------|
| `max_sharpe` | Max Sharpe SLSQP | → analytical cov⁻¹@(μ−rf) → inverse-vol |
| `min_variance` | Min global variance SLSQP | → inverse-vol |
| `risk_parity` | Inverse-volatility | (no fallback needed) |

**Constraints** (all methods): long-only, 30% per-position cap, 10% meme cap, 40% large-cap floor (BTC+ETH+BNB+SOL).

**Public API**:
```python
from edoras.scoring.enhanced_optimizer import PortfolioOptimizer
opt = PortfolioOptimizer()
weights = opt.get_optimal_weights(method="max_sharpe", symbols=["BTC-USD", ...])
# -> {"BTC-USD": 0.32, "ETH-USD": 0.28, ...}  — always sums to 1.0
```

Also added `covariance_matrix()` method to `CorrelationTracker` (annualised daily return covariance matrix). Added `scipy>=1.11` to `requirements.txt`.

#### 6.4 Project Packaging & Modularity

The project is now an installable Python package. Every module lives in a proper subpackage. All 47 `sys.path.insert` hacks in the old flat layout are eliminated in the package versions.

**New structure:**
```
edoras/                          ← git root
├── pyproject.toml               ← NEW: setuptools src-layout
├── src/
│   └── edoras/                  ← Python package
│       ├── __init__.py          ← v0.2.0
│       ├── config.py            ← BASE_DIR resolves to project root
│       ├── core/                ← signal_trading, paper_trading, risk_*, exit_*, smart_rebalancer, regime_monitor
│       ├── data/                ← indicator_calculator, correlation_tracker, crypto_data_collector, ...
│       ├── llm/                 ← llm_chain, llm_gatekeeper, trading_agent, market_intelligence, ...
│       ├── dex/                 ← dex_executor, dex_trading_agent, dex_risk_rules, bankr_client
│       ├── scoring/             ← advanced_scorer, enhanced_optimizer (PortfolioOptimizer), strategy_tracker
│       ├── reports/             ← report_engine, telegram_fmt, trade_journal, price_alerts, signal_alerts
│       ├── cli/                 ← cli, dashboard
│       ├── backtest/            ← (moved from root; already a proper package)
│       └── realtime/            ← (moved from root; added __init__.py to realtime/risk/)
├── tests/
│   ├── __init__.py              ← NEW
│   └── conftest.py              ← NEW: pytest fixtures, no sys.path hacks
└── *.py                         ← Root shims (forwards to src/edoras/)
```

**Installation:**
```bash
pip install -e .          # installs edoras package + entry points
pip install -e ".[dev]"   # also installs pytest, ruff, mypy
```

**Import style (new code):**
```python
from edoras.core.signal_trading import SignalTradingSystem
from edoras.llm.llm_chain import LLMChain
from edoras.scoring.enhanced_optimizer import PortfolioOptimizer
from edoras.data.correlation_tracker import CorrelationTracker
```

**Root shims** (backwards compat for shell scripts): Root-level `.py` files are thin shims:
```python
# signal_trading.py (shim)
import importlib, sys
_mod = importlib.import_module("edoras.core.signal_trading")
sys.modules[__name__] = _mod
if __name__ == "__main__":
    import runpy
    runpy.run_module("edoras.core.signal_trading", run_name="__main__", alter_sys=True)
```
Shell scripts (`python3 signal_trading.py --check`) continue to work unchanged. Systemd timers require no modification.

**Entry points** (via `pyproject.toml`):
```bash
edoras             # → edoras.cli.cli:main
edoras-dashboard   # → edoras.cli.dashboard:main
```

### File Change Log — Phase 6

| File | Change |
|------|--------|
| `trading_agent.py` (root shim) | Removed dead `_optimizer` property + unreachable `_parse_llm_response` code |
| `src/edoras/llm/trading_agent.py` | Same cleanup applied to package version |
| `src/edoras/scoring/enhanced_optimizer.py` | **Rewritten**: `PortfolioOptimizer` with max-Sharpe/min-variance/risk-parity; `EnhancedPortfolioOptimizer` alias kept |
| `enhanced_optimizer.py` (root shim) | Shim → `edoras.scoring.enhanced_optimizer` |
| `src/edoras/data/correlation_tracker.py` | Added `covariance_matrix()` method |
| `requirements.txt` | Added `scipy>=1.11` |
| `pyproject.toml` | **New**: setuptools src-layout, entry points, dev extras |
| `src/edoras/__init__.py` | **New**: package v0.2.0 |
| `src/edoras/{core,data,llm,dex,scoring,reports,cli}/__init__.py` | **New**: subpackage stubs |
| `src/edoras/config.py` | **New**: `BASE_DIR` fixed to resolve to project root (`../../` from `src/edoras/`) |
| `src/edoras/core/*.py` | **New**: 10 core modules migrated, imports updated |
| `src/edoras/data/*.py` | **New**: 10 data modules migrated |
| `src/edoras/llm/*.py` | **New**: 7 LLM modules migrated |
| `src/edoras/dex/*.py` | **New**: 5 DEX modules migrated |
| `src/edoras/scoring/*.py` | **New**: 4 scoring modules migrated |
| `src/edoras/reports/*.py` | **New**: 7 report/alert modules migrated |
| `src/edoras/cli/*.py` | **New**: cli + dashboard migrated |
| `src/edoras/backtest/` | **New**: backtest package moved into src |
| `src/edoras/realtime/` | **New**: realtime package moved into src; added `risk/__init__.py` |
| `tests/__init__.py` | **New** |
| `tests/conftest.py` | **New**: pytest fixtures, no sys.path hacks |
| Root `*.py` shims (16 files) | **New**: importlib shims for shell-script invocation backwards compat |

**New files created: 50+ (src/edoras/ tree)**
**Root shims created: 16**
**Lines removed (sys.path hacks, dead code): ~100**

---

## System State After All Phases

### Signal Flow (current)
```
Regime detection (HMM/heuristic)
  → Strategy routing (10 strategies; MultiSignal default for unrouted)
    → Data freshness gate (skip stale symbols)
      → Backtested strategy generates signal
        → Exit overlay (5 conditions on all held positions)
          → Polymarket overlay (boost/create from prediction markets)
            → Risk manager circuit breaker gate
              → LLM Gatekeeper (validates BUY signals; SELL always passes)
                → execute_paper_trades() (regime gate, strength gate, dedup gate)
                  → DB + state persistence + Telegram alert on risk exits
```

### What Now Runs Every Cycle
| Trigger | What happens |
|---------|-------------|
| Every 15 min | Freshness monitor checks 93 feeds, alerts on stale |
| Every 30 min (24/7) | Risk guardian checks all positions against stop-loss, trailing stop, take-profit, circuit breaker |
| Every 4h | Signal engine: 10 strategies, freshness gate, exit overlay, Polymarket overlay, LLM gatekeeper, execution |
| Daily 03:00 UTC | Database backup (hot, 7-day retention) |
| Daily 08:45 | Trading agent: full 9-source context → LLM → trade execution |
| Monday 09:00 | Smart rebalancer: drift check → category caps → quality gate → LLM gatekeeper → optimizer weights → execution |

### Risk Coverage
- **Alerts**: All 4 previously-disabled alert systems now active (risk guardian exits, price alerts, signal alerts, real-time risk exits)
- **Overnight**: Risk guardian runs 24/7 (was: 7AM–11PM only)
- **Data**: 15-minute automated staleness monitoring with Telegram alerts
- **Backup**: Daily database backup, 7 copies retained

---

## Known Limitations & Future Work

| Item | Notes |
|------|-------|
| BearDefensive needs re-validation | Registered but not live. Fails holdout gate due to Feb 2026 flash crash behaviour. Fix: lower `max_adx` to 35 OR add 1-day realised vol filter. Re-run `holdout_gate()` before routing. |
| LLM gatekeeper monitoring | No dashboard panel showing gatekeeper approval rates over time. Worth adding to `dashboard.py` once enough data accumulates. |
| `SYMBOL_TIERS` coverage | 43 symbols classified. Any new symbol added to the portfolio without a tier entry defaults to "small" (50 pts). Should be updated whenever the portfolio changes. |
