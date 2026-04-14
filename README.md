# Edoras — Data Collection Pipeline

> Optimized multi-source market data ingestion with WebSocket-first architecture

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    DATA COLLECTION LAYER                     │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  PRIMARY: WebSocket (real-time, always on)                  │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ coinbase-websocket.service (ACTIVE)                  │   │
│  │ → Ticks → 5m buffer → flush to DB → 1h → 4h rollup │   │
│  │ → Incremental indicator updates (new candles only)  │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
│  FALLBACK: REST collectors (gap-filling)                    │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ crypto_data_collector.py (daily/weekly backfill)     │   │
│  │ → Batch INSERT via executemany()                     │   │
│  │ → Incremental indicators (only new candles)          │   │
│  │ → Runs only when WS data is stale (>2h gap)          │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

## Performance

| Metric | Before | After | Improvement |
|--------|--------|-------|-------------|
| Intraday update (8 symbols) | ~5 min | ~4 sec | **75x faster** |
| Indicator calculation | Full history recalc | Delta-based (last 250 candles) | **20x faster** |
| Candle inserts | Row-by-row (1 SQL per candle) | Batch executemany() | **10-50x faster** |
| DB commits | After every row | Once per batch | **100x fewer disk syncs** |

## Data Flow

### WebSocket Pipeline (Primary)
1. **Ticks** received from Coinbase WebSocket (`wss://ws-feed.exchange.coinbase.com`)
2. **5m candles** built in-memory from individual price ticks
3. **Flush** completed 5m candles to SQLite every 60 seconds
4. **Rollup** 5m → 1h → 4h candles at UTC boundaries
5. **Indicators** computed incrementally (only for new candles + 250-candle warmup)
6. **WAL mode** enables concurrent reads during writes

### REST Pipeline (Gap-Filler)
1. **Check** if WebSocket data is recent (< 2 hours old)
2. **Skip** REST fetch if data is fresh
3. **Fetch** missing candles via Coinbase REST API only when gaps detected
4. **Batch insert** all new candles in a single `executemany()` call
5. **Compute indicators** only for new candles (delta-based)
6. **Aggregate** 1h → 4h candles via pandas groupby + batch insert

## Key Files

| File | Purpose |
|------|---------|
| `src/realtime/ingest/base_websocket.py` | WebSocket base class — tick ingestion, candle building, rollup, incremental indicators |
| `src/realtime/ingest/coinbase_websocket.py` | Coinbase-specific WebSocket client |
| `src/realtime/supervisor.py` | Runs multiple WS clients concurrently (Coinbase + Polymarket) |
| `src/data/crypto_data_collector.py` | REST collector — gap-filling, daily backfill, batch inserts |
| `src/data/intraday_update.py` | Lightweight intraday updater — gap-filler mode |
| `src/data/equity_data_collector.py` | Equity data via yfinance — batch inserts |
| `src/data/indicator_calculator.py` | Shared indicator computation (17 standard + 16 binary) |

## Systemd Services

| Service | Role | Schedule |
|---------|------|----------|
| `coinbase-websocket.service` | Real-time WS data ingestion | Persistent (always on) |
| `crypto-intraday-update.service` | Gap-filler REST updates | Every 2 hours |
| `crypto-daily-analysis.service` | Daily backfill + reports | Daily at 6 AM |

## Database

- **Engine:** SQLite with WAL mode
- **Path:** `crypto_data.db`
- **Tables:** `candlesticks` (968K+ rows), `indicators` (682K+ rows)
- **Indexes:** `idx_candlesticks_sym_tf`, `idx_candlesticks_sym_tf_ts`, `idx_indicators_sym_tf`, `idx_indicators_sym_tf_ts`
- **Timeframes:** `5m` (WS only), `1h`, `4h`, `1d`

## Optimizations Applied

1. **Batch inserts** — `executemany()` replaces row-by-row `execute()` loops
2. **Single commit** — `conn.commit()` moved outside loops (was after every row)
3. **Delta-based indicators** — Only computes for new candles + 250-candle warmup (was full history)
4. **Gap-filler mode** — REST only fetches when WS data is stale (>2h)
5. **WAL mode** — All DB connections use `PRAGMA journal_mode=WAL` for concurrent read/write
6. **Database indexes** — Composite indexes on `(symbol, timeframe)` and `(symbol, timeframe, timestamp DESC)`

## Running

```bash
# WebSocket service (persistent)
systemctl --user start coinbase-websocket.service

# Gap-filler update (runs automatically via timer)
systemctl --user start crypto-intraday-update.service

# Manual test
cd /home/satyamini/edoras
PYTHONPATH=src python3 -m data.intraday_update

# Single symbol test
PYTHONPATH=src python3 -m data.intraday_update --test --symbol BTC-USD
```

## Scaling

The architecture is designed to scale to 100+ symbols:
- WebSocket handles unlimited symbols (single connection, multiple subscriptions)
- REST gap-filler uses parallel API calls (ThreadPoolExecutor)
- Batch inserts handle hundreds of candles per call
- Delta-based indicators only process new data, not full history
