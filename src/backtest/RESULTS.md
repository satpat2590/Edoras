# Backtest Library — Strategy Catalogue Results

**Last updated:** 2026-03-17
**Period tested:** 2025-06-01 → 2026-03-01 (9 months)
**Initial capital:** $1,000
**Total backtests:** 143 (13 strategies × 11 symbols)

---

## Winners (Sharpe ≥ 0.5, Max DD ≥ -20%)

| Strategy              | Symbol   | Return | Sharpe | Sortino | Max DD   | Win Rate | Trades |
|-----------------------|----------|--------|--------|---------|----------|----------|--------|
| ScoreBasedRelaxed     | ETH-USD  | +6.53% | 0.63   | 0.47    | -10.36%  | 100%     | 2      |
| EnhancedScoreBased    | ETH-USD  | +5.08% | 0.61   | 0.45    | -8.22%   | 100%     | 2      |
| ScoreBased            | ETH-USD  | +3.89% | 0.60   | 0.44    | -6.41%   | 100%     | 2      |
| **RegimeAware**       | LTC-USD  | +6.17% | 0.57   | 0.59    | -14.93%  | 42.9%    | 7      |
| RegimeAware_Heuristic | LTC-USD  | +6.17% | 0.57   | 0.59    | -14.93%  | 42.9%    | 7      |

## Best Strategy Per Symbol

| Symbol   | Best Strategy         | Sharpe | Return | Max DD   |
|----------|-----------------------|--------|--------|----------|
| ETH-USD  | ScoreBasedRelaxed     | 0.63   | +6.53% | -10.36%  |
| LTC-USD  | RegimeAware           | 0.57   | +6.17% | -14.93%  |
| LINK-USD | MultiSignal           | 0.50   | +6.57% | -12.51%  |
| DOT-USD  | RegimeAware           | 0.38   | +4.56% | -19.36%  |
| AVAX-USD | TSMOM_3M              | 0.37   | +1.77% | -4.35%   |
| SOL-USD  | BollingerReversion    | 0.20   | +1.15% | -20.49%  |
| DOGE-USD | RegimeAware           | 0.19   | +1.17% | -9.85%   |
| ADA-USD  | MultiSignal           | 0.18   | +1.10% | -16.92%  |
| UNI-USD  | BollingerReversion    | 0.10   | -5.97% | -30.11%  |
| XRP-USD  | MultiSignal           | -0.12  | -1.66% | —        |
| BTC-USD  | PairsTrading_Agg.     | -0.29  | -3.85% | —        |

## Portfolio Template: Sweep_v1

**Sharpe-weighted allocation from best-per-symbol strategies.**

| Weight | Strategy              | Symbol   | Sharpe | Return |
|--------|-----------------------|----------|--------|--------|
| 17.5%  | ScoreBasedRelaxed     | ETH-USD  | 0.63   | +6.53% |
| 15.8%  | RegimeAware           | LTC-USD  | 0.57   | +6.17% |
| 15.8%  | RegimeAware_Heuristic | LTC-USD  | 0.57   | +6.17% |
| 13.8%  | MultiSignal           | LINK-USD | 0.50   | +6.57% |
| 10.7%  | RegimeAware           | DOT-USD  | 0.38   | +4.56% |
| 10.7%  | RegimeAware_Heuristic | DOT-USD  | 0.38   | +4.56% |
| 10.3%  | TSMOM_3M              | AVAX-USD | 0.37   | +1.77% |
| 5.5%   | BollingerReversion    | SOL-USD  | 0.20   | +1.15% |

**Expected:** Sharpe 0.49 | Return +5.22% | 8 positions

---

## Key Findings

### New Strategies Validated

1. **RegimeAware is the breakout performer.** Sharpe 0.57 on LTC and 0.38 on DOT — best strategy for 3 of 11 symbols. The bull/bear/sideways routing works: momentum in trends, mean-reversion in ranges, defensive in bears.

2. **TSMOM_3M works on AVAX.** Short-lookback (3-month) momentum with inverse-vol scaling found a clean edge on AVAX-USD (Sharpe 0.37, only -4.35% max DD). The conservative risk target keeps drawdown tight.

3. **PairsTrading shows promise on LTC and DOT.** Sharpe 0.32/0.27 with only 2 trades each — the z-score mean-reversion is selective but profitable when it triggers.

4. **Full TSMOM (12-month) doesn't work in this period.** The lookback covers the full BTC drawdown from $100K+ to $70K — the 12-month return is negative for most of the test window, so it correctly stays out but generates no alpha.

### Original Findings (Confirmed at Scale)

5. **Score-based strategies dominate on ETH** — all three variants are in the top 5. The RSI+MACD signal is well-suited to ETH's momentum characteristics.

6. **MultiSignal is the most versatile.** Positive Sharpe on LINK (+0.50), ADA (+0.18), and barely negative on others. The 5-signal consensus is stable if not spectacular.

7. **BTC is the hardest asset.** No strategy achieved positive Sharpe on BTC in this period. Best was PairsTrading_Aggressive at -0.29. The -39% drawdown overwhelms all signal-based approaches — only regime detection + early exit can help.

8. **Strategy-asset fit matters more than strategy quality.** ScoreBasedRelaxed is #1 on ETH but mediocre everywhere else. RegimeAware is strong on LTC/DOT but weak on BTC. No single strategy wins across all assets.

---

## Strategy Descriptions (13 registered)

### Original (7)
| Strategy | Type | Description |
|----------|------|-------------|
| ScoreBased | Momentum | RSI oversold/overbought + MACD histogram |
| ScoreBasedRelaxed | Momentum | Wider RSI bands (35/65), lower min strength |
| EnhancedScoreBased | Momentum | Score + ADX/volume/multi-timeframe/VIX overlays |
| MACDCross | Crossover | Pure MACD histogram sign change |
| ADXTrend | Trend | ADX > 25 + price/SMA alignment |
| BollingerReversion | Mean Reversion | BB position + RSI in ranging markets (ADX < 25) |
| MultiSignal | Consensus | 5 sub-signals (RSI, MACD, BB, ADX, volume) majority vote |

### New (6)
| Strategy | Type | Description |
|----------|------|-------------|
| TSMOM | Momentum | 12-month return, inverse-vol scaled, 15% risk target |
| TSMOM_3M | Momentum | 3-month lookback variant for faster adaptation |
| PairsTrading | Mean Reversion | Z-score on rolling 90d mean, entry at ±2σ, exit at ±0.5σ |
| PairsTrading_Aggressive | Mean Reversion | Tighter thresholds (60d, ±1.5σ entry) |
| RegimeAware | Adaptive | HMM/heuristic regime detection → momentum/reversion/defensive |
| RegimeAware_Heuristic | Adaptive | Heuristic-only regime detection (no HMM dependency) |

---

## Library Architecture

```
backtest/
├── __init__.py          # Public API: run_backtest(), compare_strategies(), generate_report()
├── engine.py            # Backtester class — bar-by-bar simulation with risk management
├── metrics.py           # 23 performance metrics, BacktestResult, Trade
├── compare.py           # Multi-strategy/symbol comparison runner
├── report.py            # PDF reports: equity curves, drawdown, heatmaps, trade logs
├── catalogue.py         # Strategy catalogue — persistent DB of results + portfolio templates
├── RESULTS.md           # This file
└── strategies/
    ├── __init__.py      # Strategy base class + registry (13 strategies)
    ├── score_based.py   # ScoreBased, ScoreBasedRelaxed, EnhancedScoreBased
    ├── macd_cross.py    # MACDCross
    ├── adx_trend.py     # ADXTrend
    ├── bollinger.py     # BollingerReversion
    ├── multi_signal.py  # MultiSignal
    ├── tsmom.py         # TSMOM, TSMOM_3M
    ├── pairs_trading.py # PairsTrading, PairsTrading_Aggressive
    └── regime_aware.py  # RegimeAware, RegimeAware_Heuristic
```

### Usage

```python
from backtest import run_backtest, compare_strategies, StrategyCatalogue, STRATEGY_REGISTRY

# Run all strategies on all symbols
comp = compare_strategies(["BTC-USD", "ETH-USD", "SOL-USD"])

# Catalogue results
cat = StrategyCatalogue()
cat.record_comparison(comp.results, tags="sweep_v2")

# Find winners
winners = cat.winners(min_sharpe=0.5, min_trades=3, max_drawdown=-0.20)

# Best strategy per symbol
best = cat.best_per_symbol(metric="sharpe_ratio")

# Build a portfolio from winners
template = cat.build_portfolio_template(
    name="Q2_2026",
    min_sharpe=0.0,
    max_positions=8,
    equal_weight=False,  # Sharpe-weighted
)

# Generate PDF reports
from backtest import generate_report, generate_comparison_report
generate_comparison_report(comp.results)
```
