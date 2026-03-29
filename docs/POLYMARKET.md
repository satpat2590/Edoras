# Polymarket → Coinbase Signal Integration

## Overview

The Polymarket signal pipeline monitors prediction market probability shifts and
generates supplementary trading signals for crypto assets on Coinbase. It operates
as an **overlay** on top of the existing backtested strategy and legacy signal
systems — it can boost existing signals or create new ones, but never overrides
risk management or strategy routing rules.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     signal_trading.py                           │
│                                                                 │
│  ┌─────────────────┐  ┌──────────────┐  ┌───────────────────┐  │
│  │ Backtested       │  │ Legacy       │  │ Polymarket        │  │
│  │ Strategies       │  │ Signals      │  │ Overlay           │  │
│  │ (routed symbols) │  │ (unrouted)   │  │ (all symbols)     │  │
│  └────────┬─────────┘  └──────┬───────┘  └────────┬──────────┘  │
│           │                   │                    │             │
│           └───────────┬───────┘                    │             │
│                       │                            │             │
│                       ▼                            │             │
│              ┌────────────────┐                    │             │
│              │ Signal List    │◄───────────────────┘             │
│              │ (merged)       │  boost existing / add new       │
│              └───────┬────────┘                                  │
│                      │                                          │
│                      ▼                                          │
│              ┌────────────────┐                                  │
│              │ execute_paper_ │                                  │
│              │ trades()       │                                  │
│              └────────────────┘                                  │
└─────────────────────────────────────────────────────────────────┘
```

## Data Flow

### 1. Ingestion (already running)

Polymarket data enters the system via two paths:

- **REST provider** (`providers/polymarket.py`): Discovers markets via Gamma API,
  syncs metadata into the `securities` table (exchange_id=3), and fetches
  historical CLOB prices into `candlesticks`.
- **WebSocket** (`realtime/ingest/polymarket_websocket.py`): Streams real-time
  `price_change` events. The base websocket class buffers ticks into 5-minute
  candles, which roll up into 1h and 4h timeframes.

All Polymarket symbols are prefixed `PM:` (e.g., `PM:BITCOIN-REACH-150K-IN-MARCH`).
Prices represent probabilities in the `[0, 1]` range.

### 2. Probability Shift Computation (`polymarket_signals.py`)

The `PolymarketSignalGenerator` class computes probability deltas over configurable
lookback windows (default: 1h, 4h, 24h).

For each active Polymarket market:

```
delta = latest_1h_close - close_at_start_of_window
```

Only markets where `|delta| >= min_probability_delta` (default 5%) are considered.

### 3. Market-to-Crypto Mapping

Each Polymarket market name is matched against a keyword → crypto symbol mapping
table (`DEFAULT_MARKET_CRYPTO_MAP`). Keywords are matched case-insensitively against
the market name.

Each mapping carries a **correlation weight** in `[-1.0, 1.0]`:

| Keyword | Crypto Symbols | Weight | Meaning |
|---------|---------------|--------|---------|
| `bitcoin`, `btc` | BTC-USD | +1.0 | Direct: probability up → bullish |
| `ethereum`, `eth` | ETH-USD | +1.0 | Direct |
| `solana`, `sol` | SOL-USD | +1.0 | Direct |
| `crypto` | BTC-USD, ETH-USD | +0.7, +0.5 | Broad crypto sentiment |
| `fed rate` | BTC-USD, ETH-USD | -0.5, -0.4 | Inverse: rate hike probability up → bearish |
| `fed decrease` | BTC-USD, ETH-USD | +0.6, +0.5 | Rate cut → bullish |
| `recession` | BTC-USD, ETH-USD | -0.4, -0.4 | Inverse |
| `tariff` | BTC-USD, ETH-USD | -0.3, -0.3 | Inverse |
| `etf approval` | BTC-USD, ETH-USD | +0.8, +0.5 | ETF catalyst |
| `regulation` | BTC-USD, ETH-USD | -0.3, -0.3 | Regulatory risk |

The **effective delta** for a crypto symbol is:

```
effective_delta = probability_delta × correlation_weight
```

- Positive effective delta → BUY signal
- Negative effective delta → SELL signal

### 4. Signal Strength Calculation

Strength is scaled linearly from effective delta to a 0–100 range:

```
raw_strength = min(|effective_delta| / 0.20, 1.0) × 100
strength = max(raw_strength, 20)
```

So a 10% effective delta → strength ~50, a 20% effective delta → strength ~100.

If multiple Polymarket markets produce signals for the same (symbol, action) pair,
only the strongest is kept (deduplication).

### 5. Integration into Signal Pipeline (`signal_trading.py`)

The overlay runs **after** both backtested and legacy signals have been generated.
It operates in two modes:

#### Mode A: Agreement Boost

If a Polymarket signal matches an existing signal's `(symbol, action)`:

```
boost = min(pm_strength × 0.25, 15)
existing_signal.strength += boost
```

The boost is capped at +15 points. This means a max-strength Polymarket signal
(strength=100, i.e. a 20%+ probability swing) adds +15 to the existing signal.

**Rationale**: Prediction market agreement confirms the technical signal. The cap
prevents Polymarket alone from pushing a weak technical signal into high-conviction
territory.

#### Mode B: Standalone Signal

If no existing signal matches the Polymarket signal's `(symbol, action)`:

```
pm_signal.strength = min(pm_signal.strength, 65)
```

The signal is added to the pipeline with a **hard cap of 65** (moderate conviction).
It must still pass the `strength >= 35` threshold to be actionable, and the standard
position sizing rules apply:

- Strength 50–65 → 3–5% of portfolio
- Strength 65 (capped) → ~5% of portfolio

**Rationale**: Polymarket shifts alone, without corroborating technical signals,
should not trigger high-conviction trades. The 65 cap keeps position sizes
conservative.

### 6. Logging and Attribution

All Polymarket signals are logged to `strategy_signals_log` with:

- `strategy_name = "polymarket_overlay"`
- `timeframe = "event"` (not a traditional candlestick timeframe)
- Full reason string including the originating PM market name and probability delta

Boost-mode modifications append to the existing signal's reason string:
```
[TSMOM_3M/4h] momentum entry z=1.2 | PM boost +12 (Polymarket: 'Bitcoin $150K' +8.5% ...)
```

## Configuration

### Threshold Tuning

In `signal_trading.py` `__init__`:

```python
self.polymarket_generator = PolymarketSignalGenerator(
    db_path=db_path,
    min_probability_delta=0.05,  # 5% — adjust for sensitivity
)
```

- **Lower threshold (e.g. 0.03)**: More signals, more noise. Useful if Polymarket
  has high-volume crypto markets with frequent small shifts.
- **Higher threshold (e.g. 0.10)**: Fewer signals, higher quality. Better for
  macro events (fed rate decisions) where only large moves matter.

### Adding New Keyword Mappings

Edit `DEFAULT_MARKET_CRYPTO_MAP` in `polymarket_signals.py`:

```python
DEFAULT_MARKET_CRYPTO_MAP = {
    "bitcoin": [("BTC-USD", 1.0)],
    # Add new mappings:
    "ethereum etf": [("ETH-USD", 0.9)],
    "stablecoin": [("USDC-BASE", -0.3)],  # depeg risk
}
```

### Override via Constructor

```python
gen = PolymarketSignalGenerator(
    market_crypto_map={
        "bitcoin": [("BTC-USD", 1.0)],
        "my custom keyword": [("SOL-USD", 0.5)],
    },
    windows_hours=[4, 24, 72],  # custom lookback windows
)
```

## Safety Properties

1. **Never overrides risk management**: Polymarket signals go through the same
   `execute_paper_trades()` pipeline — stop-loss, trailing stop, circuit breaker,
   and position limits all apply.

2. **Never bypasses strategy routing**: Routed symbols use their backtested
   strategy's signal. Polymarket can only boost or supplement, not replace.

3. **Standalone signals are capped**: A Polymarket-only signal maxes out at
   strength 65, limiting position size to ~5% of portfolio.

4. **Boost is bounded**: Agreement boost is capped at +15, preventing
   a Polymarket shift from turning a marginal signal into a max-conviction trade.

5. **Portfolio-scoped**: Only signals for symbols in the portfolio's `PORTFOLIO_SYMBOLS`
   list are emitted. Polymarket markets referencing assets not in the portfolio
   are silently ignored.

6. **Graceful degradation**: If `polymarket_signals.py` fails to import or
   `generate_signals()` throws, the signal pipeline continues without it.

## CLI Testing

```bash
# View probability shifts and generated signals
python3 polymarket_signals.py

# Lower threshold to see more potential signals
python3 polymarket_signals.py --threshold 0.02

# Check a specific window
python3 polymarket_signals.py --window 24

# Show all markets, not just crypto/macro-relevant
python3 polymarket_signals.py --all-shifts

# Full signal pipeline dry run (shows Polymarket overlay in action)
python3 signal_trading.py --test
```

## Files

| File | Role |
|------|------|
| `polymarket_signals.py` | Probability shift computation + keyword mapping + signal generation |
| `signal_trading.py` | Integration point: `_get_polymarket_signals()` method + overlay merge in `check_all_symbols()` |
| `providers/polymarket.py` | REST data provider (market discovery + historical prices) |
| `realtime/ingest/polymarket_websocket.py` | Real-time WebSocket price feed |
| `strategy_tracker.py` | Signal logging (`strategy_name="polymarket_overlay"`) |

## Future Work

- **Weighted keyword scoring**: Replace binary keyword matching with TF-IDF or
  embedding similarity for more nuanced market→crypto mapping.
- **Volume-weighted deltas**: Weight probability shifts by Polymarket market volume
  (currently volume data is not provided by the WebSocket feed).
- **Cross-market confluence**: If multiple Polymarket markets shift in the same
  direction for the same crypto asset, combine their signals before mapping.
- **Adaptive thresholds**: Adjust `min_probability_delta` dynamically based on
  recent Polymarket volatility (high-vol periods → raise threshold).
