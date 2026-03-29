# Edoras Strategy Catalog

Last updated: 2026-03-29

---

## 1. Strategy Catalog

### Momentum Strategies

**ScoreBased** (ID 1)
- Type: momentum | Regime fit: bull
- RSI extremes with MACD histogram confirmation. Buys when RSI < 30 and MACD_H > 0; sells when RSI > 70 and MACD_H < 0. Has a secondary weak-signal band at 35/65.
- Parameters: `rsi_oversold=30`, `rsi_overbought=70`, `min_strength=30`

**ScoreBasedRelaxed** (ID 2)
- Type: momentum | Regime fit: bull
- ScoreBased with wider RSI bands and lower minimum strength. Generates more signals.
- Parameters: `rsi_oversold=35`, `rsi_overbought=65`, `min_strength=20`

**MACDCross** (ID 5)
- Type: momentum | Regime fit: bull
- Pure MACD histogram sign-change strategy. Fires BUY on negative-to-positive crossover, SELL on positive-to-negative. Fixed weight of 0.5.
- Parameters: none (uses default MACD 12/26/9)

**EnhancedScoreBased** (ID 7)
- Type: momentum | Regime fit: bull
- Extends ScoreBased with four multipliers applied to signal strength: ADX trend confirmation (1.3x in confirmed trends), volume ratio (1.2x on high volume), multi-timeframe alignment (up to 1.3x when 1h and 4h agree), and VIX regime (0.5x BUY in high-VIX, 1.2x BUY in low-VIX).
- Parameters: `rsi_oversold=30`, `rsi_overbought=70`, `min_strength=30`, `db_path` (for cross-TF/VIX lookups)

**TSMOM** (ID 8)
- Type: momentum | Regime fit: bull
- Time-series momentum (Moskowitz et al. 2012). Goes long when 252-day cumulative return is positive; exits when negative. Position sized by inverse volatility targeting 15% annualized risk. MACD and RSI used as confirmation filters.
- Parameters: `lookback=252`, `vol_window=60`, `target_vol=0.15`, `signal_threshold=0.0`

**TSMOM_3M** (ID 9)
- Type: momentum | Regime fit: bull, ok in sideways
- Shorter-lookback TSMOM for faster regime adaptation. Requires a minimum 2% cumulative return to trigger.
- Parameters: `lookback=63`, `vol_window=30`, `target_vol=0.15`, `signal_threshold=0.02`

### Trend-Following Strategies

**ADXTrend** (ID 6)
- Type: trend_following | Regime fit: bull
- Enters confirmed trends when ADX > 25, price/SMA alignment confirms direction, and MACD agrees. Higher weight (0.7) when ADX > 35 (strong trend). Exits on ADX decline of 5+ points.
- Parameters: `adx_threshold=25`, `adx_strong=35`

### Mean-Reversion Strategies

**BollingerReversion** (ID 3)
- Type: mean_reversion | Regime fit: sideways, ok in bear
- Mean-reversion at Bollinger Band extremes in ranging markets (ADX < 25). Buys when BB position < 0.1 and RSI < 40; sells when BB position > 0.9 and RSI > 60. Volume confirmation adds 0.1 to weight.
- Parameters: `bb_threshold=0.05`, `adx_range_max=25`

**PairsTrading** (ID 10)
- Type: mean_reversion | Regime fit: sideways, ok in bear
- Spread z-score mean-reversion (Ornstein-Uhlenbeck proxy). Computes z-score over a 90-day rolling window; enters when z < -2.0 (buy) or z > 2.0 (sell); exits at |z| < 0.5. Validates mean-reversion quality via half-life estimation (skips if > 30 days). ADX filter rejects trades when ADX > 35.
- Parameters: `lookback=90`, `entry_z=2.0`, `exit_z=0.5`, `max_half_life=30`

**PairsTrading_Aggressive** (ID 11)
- Type: mean_reversion | Regime fit: sideways, ok in bear
- PairsTrading with tighter thresholds for more frequent trading.
- Parameters: `lookback=60`, `entry_z=1.5`, `exit_z=0.3`, `max_half_life=20`

### Adaptive Strategies

**RegimeAware** (ID 12)
- Type: adaptive | Regime fit: all regimes
- HMM-based regime detection (3-state GaussianHMM on returns + rolling volatility) with heuristic fallback. Routes to sub-strategies by regime:
  - Bull: TSMOM-style momentum (60-day return + MACD confirmation, inverse-vol sizing)
  - Sideways: Bollinger mean-reversion (BB position + RSI)
  - Bear: defensive (exit positions, only buy extreme oversold RSI < 22)
- Parameters: `use_hmm=true`

**RegimeAware_Heuristic** (ID 13)
- Type: adaptive | Regime fit: all regimes
- Same routing logic as RegimeAware but uses heuristic regime detection only (ADX + SMA slope + volatility). No hmmlearn dependency.
- Parameters: `use_hmm=false`

---

## 2. Current Galadriel Routing

All symbols run on the **4h** timeframe.

| Symbol | Strategy | ID |
|--------|----------|----|
| ADA-USD | MultiSignal | 4 |
| AVAX-USD | MultiSignal | 4 |
| BTC-USD | BollingerReversion | 3 |
| DOGE-USD | RegimeAware | 12 |
| UNI-USD | RegimeAware | 12 |
| XRP-USD | MultiSignal | 4 |

---

## 3. Regime-Strategy Matrix

| Strategy | Bull | Sideways | Bear |
|----------|------|----------|------|
| ScoreBased | **best** | - | - |
| ScoreBasedRelaxed | **best** | - | - |
| MACDCross | **best** | - | - |
| EnhancedScoreBased | **best** | - | - |
| TSMOM | **best** | - | - |
| TSMOM_3M | **best** | ok | - |
| ADXTrend | **best** | - | - |
| BollingerReversion | - | **best** | ok |
| PairsTrading | - | **best** | ok |
| PairsTrading_Aggressive | - | **best** | ok |
| MultiSignal | ok | **best** | ok |
| RegimeAware | **best** | **best** | **best** |
| RegimeAware_Heuristic | **best** | **best** | **best** |

Source: `regime_monitor.py` STRATEGY_REGIME_FIT mapping.

---

## 4. Backtest Performance Summary

- The **4h timeframe consistently outperforms 1d** across all strategies in out-of-sample testing. 4h is the primary timeframe for Galadriel.
- 4h candles are aggregated from 1h data (Coinbase does not provide native 4h).
- TSMOM and PairsTrading strategies benefit most from the higher-frequency data, as inverse-volatility scaling and z-score computations are more responsive at 4h granularity.
- MultiSignal performs well across regimes due to its consensus requirement (3+ of 5 signals), making it the default choice for assets without strong directional bias.
- RegimeAware is the only strategy that adapts automatically to regime shifts, but depends on hmmlearn for full HMM detection.
