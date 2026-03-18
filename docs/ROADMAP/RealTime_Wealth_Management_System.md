# Real‑Time Wealth Management System: Engineering Roadmap

*For finance‑focused software engineers upgrading from scheduled batch processing to low‑latency, event‑driven trading.*  
*Based on existing `projects/coinbase‑analysis/` architecture. Target: Full paper‑trading system by Friday.*

---

## **Executive Summary**

We're migrating from a **batch‑oriented system** (4‑hour crypto candles, daily equity data) to a **real‑time event‑driven architecture** with WebSocket feeds, streaming indicators, and sub‑second risk checks. This unlocks alpha through lower latency, better risk management, and adaptive strategy execution.

**Current State (March 2026):**
- **Database:** SQLite (`crypto_data.db`) – crypto + equity OHLCV + indicators
- **Data collection:** REST APIs + systemd timers (4‑hour crypto, daily equity)
- **Processing:** Batch indicator calculation, scoring, optimization
- **Execution:** Paper‑trading simulation with $1,000 virtual capital
- **Risk management:** Stop‑loss (10%), trailing stop, take‑profit scale‑out, circuit‑breaker (15% drawdown)
- **Reporting:** Telegram alerts, daily portfolio snapshots

**Target State (This Week):**
- **Real‑time data:** Coinbase WebSocket + Polygon.io WebSocket (equities)
- **Stream processing:** Stateful indicators (RSI, MACD, SMA) computed per tick
- **Event‑driven risk:** Stop‑loss/take‑profit evaluation per price update
- **Unified audit trail:** Complete trade/position/portfolio history in TimescaleDB
- **Paper‑trading ready:** By Friday, March 13

---

## **Phase 0: Assessment & Foundation (Today)**

### **1.1 Current Database Schema Analysis**

```sql
-- Existing tables in crypto_data.db
candlesticks           -- OHLCV for all assets (crypto, equity, index)
indicators             -- 17 technical indicators per symbol/timeframe  
portfolio_analysis     -- signal classifications
collection_log         -- data fetch tracking
sentiment_scores       -- LLM‑analyzed news sentiment
correlations           -- cross‑asset correlation snapshots
market_regime          -- VIX level, regime label
market_memory          -- vector store for market intelligence
paper_trades           -- trade records (timestamp, side, symbol, quantity, price)
paper_snapshots        -- daily portfolio snapshots
```

**Gaps identified:**
1. No **continuous position tracking** (only daily snapshots)
2. No **portfolio‑level performance metrics** over time
3. No **order book data** for liquidity assessment
4. No **trade‑by‑trade audit trail** with decision context
5. No **real‑time risk event logging**

### **1.2 Enhanced Database Schema**

We'll migrate from SQLite to **TimescaleDB** (PostgreSQL extension) for:
- Time‑series optimizations (hypertables, continuous aggregates)
- Larger datasets (ticks, candles, indicators)
- Better concurrent access
- PGVector integration for embeddings

**New schema (`schema/timescale_schema.sql`):**

```sql
-- 1. Market data (hypertables)
CREATE TABLE ticks (
    time TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    price DECIMAL(18,8),
    volume DECIMAL(18,8),
    exchange TEXT,
    bid DECIMAL(18,8),
    ask DECIMAL(18,8),
    PRIMARY KEY (time, symbol)
);
SELECT create_hypertable('ticks', 'time');

CREATE TABLE candles (
    time TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,  -- '1s', '1m', '5m', '1h', '4h', '1d'
    open DECIMAL(18,8),
    high DECIMAL(18,8),
    low DECIMAL(18,8),
    close DECIMAL(18,8),
    volume DECIMAL(18,8),
    PRIMARY KEY (time, symbol, timeframe)
);
SELECT create_hypertable('candles', 'time');

-- 2. Portfolio & positions
CREATE TABLE portfolios (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    initial_capital DECIMAL(18,2),
    currency TEXT DEFAULT 'USD',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE positions (
    id SERIAL PRIMARY KEY,
    portfolio_id INTEGER REFERENCES portfolios(id),
    symbol TEXT NOT NULL,
    quantity DECIMAL(18,8),
    entry_price DECIMAL(18,8),
    entry_time TIMESTAMPTZ,
    exit_price DECIMAL(18,8),
    exit_time TIMESTAMPTZ,
    status TEXT CHECK (status IN ('open', 'closed', 'partial')),
    pnl DECIMAL(18,2),
    pnl_percent DECIMAL(8,4),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE trades (
    id SERIAL PRIMARY KEY,
    portfolio_id INTEGER REFERENCES portfolios(id),
    symbol TEXT NOT NULL,
    side TEXT CHECK (side IN ('BUY', 'SELL')),
    quantity DECIMAL(18,8),
    price DECIMAL(18,8),
    amount_usd DECIMAL(18,2),
    fee DECIMAL(18,2),
    order_type TEXT,
    status TEXT CHECK (status IN ('filled', 'partial', 'cancelled')),
    decision_context JSONB,  -- LLM reasoning, signals, risk checks
    created_at TIMESTAMPTZ DEFAULT NOW(),
    INDEX trades_symbol_time_idx (symbol, created_at)
);

-- 3. Risk events
CREATE TABLE risk_events (
    id SERIAL PRIMARY KEY,
    portfolio_id INTEGER REFERENCES portfolios(id),
    symbol TEXT,
    event_type TEXT,  -- 'stop_loss', 'trailing_stop', 'take_profit', 'circuit_breaker'
    trigger_price DECIMAL(18,8),
    current_price DECIMAL(18,8),
    quantity DECIMAL(18,8),
    action_taken TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 4. Performance metrics (continuous aggregates)
CREATE TABLE portfolio_performance (
    time TIMESTAMPTZ NOT NULL,
    portfolio_id INTEGER REFERENCES portfolios(id),
    total_value DECIMAL(18,2),
    cash DECIMAL(18,2),
    invested DECIMAL(18,2),
    daily_pnl DECIMAL(18,2),
    daily_return DECIMAL(8,4),
    sharpe_30d DECIMAL(8,4),
    max_drawdown_30d DECIMAL(8,4),
    volatility_30d DECIMAL(8,4),
    PRIMARY KEY (time, portfolio_id)
);
SELECT create_hypertable('portfolio_performance', 'time');
```

### **1.3 Migration Strategy**

**Dual‑write approach:**
1. New real‑time system writes to TimescaleDB
2. Legacy code continues using SQLite
3. Migration script copies historical data
4. Feature flags control which symbols use new system

**Migration script (`migration/historical_migration.py`):**
```python
def migrate_candlesticks():
    """Copy candlesticks from SQLite to TimescaleDB"""
    sqlite_conn = sqlite3.connect('crypto_data.db')
    pg_conn = psycopg2.connect(DATABASE_URL)
    
    batch_size = 10000
    offset = 0
    
    while True:
        df = pd.read_sql_query(f"""
            SELECT * FROM candlesticks 
            LIMIT {batch_size} OFFSET {offset}
        """, sqlite_conn)
        
        if df.empty:
            break
            
        df.to_sql('candles', pg_conn, if_exists='append', index=False)
        offset += batch_size
```

---

## **Phase 1: Real‑Time Data Layer (Day 1‑2)**

### **2.1 WebSocket Infrastructure**

**Directory structure:**
```
realtime/
├── ingest/
│   ├── coinbase_websocket.py
│   ├── polygon_websocket.py
│   └── unified_stream.py
├── processing/
│   ├── candle_aggregator.py
│   ├── streaming_indicators.py
│   └── event_bus.py
├── storage/
│   ├── timescale_writer.py
│   └── redis_cache.py
└── config/
    └── realtime_config.yaml
```

### **2.2 Coinbase WebSocket Client**

**File:** `realtime/ingest/coinbase_websocket.py`

```python
import asyncio
import json
import logging
from dataclasses import dataclass
from typing import List, Callable
import websockets
from datetime import datetime

logger = logging.getLogger(__name__)

@dataclass
class MarketTick:
    symbol: str
    price: float
    volume: float
    timestamp: datetime
    exchange: str = "coinbase"

class CoinbaseWebSocketClient:
    """Production‑grade WebSocket client with reconnection, backpressure, and fault tolerance"""
    
    def __init__(
        self,
        symbols: List[str],
        on_tick: Callable[[MarketTick], None],
        on_candle: Callable[[dict], None],
        heartbeat_interval: int = 30
    ):
        self.symbols = symbols
        self.on_tick = on_tick
        self.on_candle = on_candle
        self.heartbeat_interval = heartbeat_interval
        self.ws_url = "wss://ws-feed.exchange.coinbase.com"
        self.connection = None
        self.reconnect_delay = 1
        self.max_reconnect_delay = 60
        self.running = False
        
        # Candle aggregation buffers
        self.candle_buffers = {}  # symbol -> {timeframe -> buffer}
        
    async def connect(self):
        """Main connection loop with exponential backoff"""
        self.running = True
        
        while self.running:
            try:
                logger.info(f"Connecting to Coinbase WebSocket for {len(self.symbols)} symbols")
                
                async with websockets.connect(
                    self.ws_url,
                    ping_interval=self.heartbeat_interval,
                    ping_timeout=10,
                    close_timeout=10
                ) as websocket:
                    self.connection = websocket
                    self.reconnect_delay = 1  # Reset on successful connection
                    
                    # Subscribe to ticker channel
                    await self._subscribe(websocket)
                    
                    # Start heartbeat monitor
                    asyncio.create_task(self._heartbeat_monitor())
                    
                    # Main message loop
                    await self._message_loop(websocket)
                    
            except (websockets.ConnectionClosed, ConnectionError) as e:
                logger.warning(f"WebSocket disconnected: {e}")
                await self._handle_disconnection()
            except Exception as e:
                logger.error(f"Unexpected error: {e}")
                await self._handle_disconnection()
    
    async def _subscribe(self, websocket):
        """Send subscription message"""
        subscription_msg = {
            "type": "subscribe",
            "product_ids": self.symbols,
            "channels": [
                {"name": "ticker", "product_ids": self.symbols},
                {"name": "heartbeat", "product_ids": self.symbols}
            ]
        }
        await websocket.send(json.dumps(subscription_msg))
        logger.info(f"Subscribed to {len(self.symbols)} symbols")
    
    async def _message_loop(self, websocket):
        """Process incoming messages"""
        async for message in websocket:
            try:
                data = json.loads(message)
                
                # Handle different message types
                if data.get("type") == "ticker":
                    await self._process_ticker(data)
                elif data.get("type") == "heartbeat":
                    await self._process_heartbeat(data)
                elif data.get("type") == "error":
                    logger.error(f"WebSocket error: {data}")
                elif data.get("type") == "subscriptions":
                    logger.info(f"Subscription confirmed: {data}")
                    
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse message: {e}")
            except Exception as e:
                logger.error(f"Error processing message: {e}")
    
    async def _process_ticker(self, data: dict):
        """Convert ticker message to MarketTick and trigger downstream processing"""
        try:
            tick = MarketTick(
                symbol=data["product_id"],
                price=float(data["price"]),
                volume=float(data.get("volume_24h", 0)),
                timestamp=datetime.fromisoformat(data["time"].replace("Z", "+00:00")),
                exchange="coinbase"
            )
            
            # Call tick handler
            if self.on_tick:
                await self.on_tick(tick)
            
            # Aggregate to candles
            await self._aggregate_to_candles(tick)
            
        except (KeyError, ValueError) as e:
            logger.error(f"Malformed ticker data: {data}, error: {e}")
    
    async def _aggregate_to_candles(self, tick: MarketTick):
        """Aggregate ticks to 1s, 1m, 5m, 1h candles"""
        import pandas as pd
        
        for timeframe, seconds in [("1s", 1), ("1m", 60), ("5m", 300), ("1h", 3600)]:
            # Round timestamp to timeframe boundary
            rounded_ts = self._round_timestamp(tick.timestamp, seconds)
            
            # Initialize buffer if needed
            if tick.symbol not in self.candle_buffers:
                self.candle_buffers[tick.symbol] = {}
            if timeframe not in self.candle_buffers[tick.symbol]:
                self.candle_buffers[tick.symbol][timeframe] = {
                    "time": rounded_ts,
                    "open": tick.price,
                    "high": tick.price,
                    "low": tick.price,
                    "close": tick.price,
                    "volume": tick.volume
                }
            
            buffer = self.candle_buffers[tick.symbol][timeframe]
            
            # If we're still in same candle period, update
            if rounded_ts == buffer["time"]:
                buffer["high"] = max(buffer["high"], tick.price)
                buffer["low"] = min(buffer["low"], tick.price)
                buffer["close"] = tick.price
                buffer["volume"] += tick.volume
            else:
                # Emit completed candle
                candle = buffer.copy()
                if self.on_candle:
                    await self.on_candle({
                        "symbol": tick.symbol,
                        "timeframe": timeframe,
                        **candle
                    })
                
                # Start new candle
                self.candle_buffers[tick.symbol][timeframe] = {
                    "time": rounded_ts,
                    "open": tick.price,
                    "high": tick.price,
                    "low": tick.price,
                    "close": tick.price,
                    "volume": tick.volume
                }
    
    async def _heartbeat_monitor(self):
        """Monitor connection health"""
        while self.running and self.connection:
            await asyncio.sleep(self.heartbeat_interval)
            if self.connection.closed:
                logger.warning("Heartbeat detected closed connection")
                break
    
    async def _handle_disconnection(self):
        """Handle disconnection with exponential backoff"""
        if not self.running:
            return
            
        logger.info(f"Reconnecting in {self.reconnect_delay} seconds...")
        await asyncio.sleep(self.reconnect_delay)
        
        # Exponential backoff with cap
        self.reconnect_delay = min(self.reconnect_delay * 2, self.max_reconnect_delay)
    
    async def disconnect(self):
        """Graceful shutdown"""
        self.running = False
        if self.connection:
            await self.connection.close()
    
    @staticmethod
    def _round_timestamp(dt: datetime, seconds: int) -> datetime:
        """Round datetime to nearest timeframe boundary"""
        from datetime import timedelta
        rounded = dt - timedelta(
            seconds=dt.second % seconds,
            microseconds=dt.microsecond
        )
        return rounded.replace(second=dt.second // seconds * seconds)
```

### **2.3 Event Bus for Internal Communication**

**File:** `realtime/processing/event_bus.py`

```python
import asyncio
import json
import redis.asyncio as redis
from typing import Callable, Dict, List
import logging

logger = logging.getLogger(__name__)

class EventBus:
    """Redis‑based event bus for real‑time system communication"""
    
    def __init__(self, redis_url: str = "redis://localhost:6379"):
        self.redis_url = redis_url
        self.redis = None
        self.subscriptions: Dict[str, List[Callable]] = {}
        self.pubsub = None
        
    async def connect(self):
        """Connect to Redis"""
        self.redis = await redis.from_url(self.redis_url, decode_responses=True)
        self.pubsub = self.redis.pubsub()
        logger.info("EventBus connected to Redis")
    
    async def publish(self, channel: str, event: dict):
        """Publish event to channel"""
        if not self.redis:
            await self.connect()
        
        await self.redis.publish(channel, json.dumps(event))
        
        # Also call local subscribers (zero‑copy for same process)
        if channel in self.subscriptions:
            for callback in self.subscriptions[channel]:
                try:
                    await callback(event)
                except Exception as e:
                    logger.error(f"Event subscriber error: {e}")
    
    async def subscribe(self, channel: str, callback: Callable):
        """Subscribe to channel with callback"""
        if not self.redis:
            await self.connect()
        
        # Register local callback
        if channel not in self.subscriptions:
            self.subscriptions[channel] = []
        self.subscriptions[channel].append(callback)
        
        # Subscribe via Redis Pub/Sub (for cross‑process communication)
        await self.pubsub.subscribe(channel)
        logger.info(f"Subscribed to channel: {channel}")
    
    async def run_consumer(self):
        """Process messages from Redis Pub/Sub"""
        async for message in self.pubsub.listen():
            if message["type"] == "message":
                channel = message["channel"]
                event = json.loads(message["data"])
                
                # Dispatch to local subscribers
                if channel in self.subscriptions:
                    for callback in self.subscriptions[channel]:
                        try:
                            await callback(event)
                        except Exception as e:
                            logger.error(f"Pub/Sub subscriber error: {e}")
    
    async def close(self):
        """Cleanup connections"""
        if self.pubsub:
            await self.pubsub.unsubscribe()
            await self.pubsub.close()
        if self.redis:
            await self.redis.close()
```

### **2.4 TimescaleDB Writer**

**File:** `realtime/storage/timescale_writer.py`

```python
import asyncio
import psycopg2
from psycopg2.extras import execute_values
from datetime import datetime
from typing import List
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class Candle:
    symbol: str
    timeframe: str
    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

class TimescaleWriter:
    """Batch writer for TimescaleDB with connection pooling"""
    
    def __init__(self, dsn: str, batch_size: int = 1000, flush_interval: float = 1.0):
        self.dsn = dsn
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self.conn = None
        self.cursor = None
        
        # Buffers
        self.ticks_buffer: List[tuple] = []
        self.candles_buffer: List[Candle] = []
        
        # Locks
        self.ticks_lock = asyncio.Lock()
        self.candles_lock = asyncio.Lock()
        
        # Flush task
        self.flush_task = None
        self.running = False
    
    async def connect(self):
        """Establish database connection"""
        self.conn = psycopg2.connect(self.dsn)
        self.cursor = self.conn.cursor()
        
        # Start periodic flush
        self.running = True
        self.flush_task = asyncio.create_task(self._periodic_flush())
        
        logger.info("TimescaleWriter connected")
    
    async def add_tick(self, symbol: str, price: float, volume: float, time: datetime):
        """Add tick to buffer"""
        async with self.ticks_lock:
            self.ticks_buffer.append((
                time, symbol, price, volume, 'coinbase',
                price * 0.999, price * 1.001  # Simple bid/ask
            ))
            
            # Flush if buffer full
            if len(self.ticks_buffer) >= self.batch_size:
                await self._flush_ticks()
    
    async def add_candle(self, candle: Candle):
        """Add candle to buffer"""
        async with self.candles_lock:
            self.candles_buffer.append(candle)
            
            if len(self.candles_buffer) >= self.batch_size:
                await self._flush_candles()
    
    async def _flush_ticks(self):
        """Flush ticks buffer to database"""
        if not self.ticks_buffer:
            return
            
        async with self.ticks_lock:
            buffer = self.ticks_buffer.copy()
            self.ticks_buffer.clear()
        
        try:
            execute_values(
                self.cursor,
                """
                INSERT INTO ticks (time, symbol, price, volume, exchange, bid, ask)
                VALUES %s
                ON CONFLICT (time, symbol) DO NOTHING
                """,
                buffer
            )
            self.conn.commit()
            logger.debug(f"Flushed {len(buffer)} ticks")
        except Exception as e:
            logger.error(f"Failed to flush ticks: {e}")
            # Re-add to buffer
            async with self.ticks_lock:
                self.ticks_buffer.extend(buffer)
    
    async def _flush_candles(self):
        """Flush candles buffer to database"""
        if not self.candles_buffer:
            return
            
        async with self.candles_lock:
            buffer = self.candles_buffer.copy()
            self.candles_buffer.clear()
        
        try:
            values = [
                (c.time, c.symbol, c.timeframe, c.open, c.high, c.low, c.close, c.volume)
                for c in buffer
            ]
            
            execute_values(
                self.cursor,
                """
                INSERT INTO candles (time, symbol, timeframe, open, high, low, close, volume)
                VALUES %s
                ON CONFLICT (time, symbol, timeframe) DO UPDATE SET
                    high = GREATEST(candles.high, EXCLUDED.high),
                    low = LEAST(candles.low, EXCLUDED.low),
                    close = EXCLUDED.close,
                    volume = candles.volume + EXCLUDED.volume
                """,
                values
            )
            self.conn.commit()
            logger.debug(f"Flushed {len(buffer)} candles")
        except Exception as e:
            logger.error(f"Failed to flush candles: {e}")
            async with self.candles_lock:
                self.candles_buffer.extend(buffer)
    
    async def _periodic_flush(self):
        """Periodic flush for partial buffers"""
        while self.running:
            await asyncio.sleep(self.flush_interval)
            await self._flush_ticks()
            await self._flush_candles()
    
    async def close(self):
        """Flush remaining buffers and close connection"""
        self.running = False
        if self.flush_task:
            self.flush_task.cancel()
            try:
                await self.flush_task
            except asyncio.CancelledError:
                pass
        
        # Final flush
        await self._flush_ticks()
        await self._flush_candles()
        
        if self.cursor:
            self.cursor.close()
        if self.conn:
            self.conn.close()
        
        logger.info("TimescaleWriter closed")
```

### **2.5 Main Real‑Time Service**

**File:** `realtime/main.py`

```python
#!/usr/bin/env python3
"""
Main real‑time service orchestrating WebSocket ingest, processing, and storage.
"""

import asyncio
import logging
import signal
import sys
from datetime import datetime

from ingest.coinbase_websocket import CoinbaseWebSocketClient, MarketTick
from processing.event_bus import EventBus
from storage.timescale_writer import TimescaleWriter, Candle
import config

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class RealTimeService:
    """Orchestrates all real‑time components"""
    
    def __init__(self):
        self.event_bus = EventBus()
        self.timescale_writer = TimescaleWriter(config.TIMESCALE_DSN)
        
        # Initialize WebSocket clients
        self.coinbase_client = CoinbaseWebSocketClient(
            symbols=config.CRYPTO_SYMBOLS,
            on_tick=self.handle_tick,
            on_candle=self.handle_candle
        )
        
        self.running = False
        
    async def handle_tick(self, tick: MarketTick):
        """Process incoming tick"""
        # 1. Store in TimescaleDB
        await self.timescale_writer.add_tick(
            tick.symbol, tick.price, tick.volume, tick.timestamp
        )
        
        # 2. Publish to event bus
        await self.event_bus.publish("market:tick", {
            "symbol": tick.symbol,
            "price": tick.price,
            "volume": tick.volume,
            "timestamp": tick.timestamp.isoformat(),
            "exchange": tick.exchange
        })
        
        # 3. Trigger immediate risk check for positions
        await self.event_bus.publish("risk:tick", {
            "symbol": tick.symbol,
            "price": tick.price,
            "timestamp": tick.timestamp.isoformat()
        })
    
    async def handle_candle(self, candle_data: dict):
        """Process aggregated candle"""
        candle = Candle(
            symbol=candle_data["symbol"],
            timeframe=candle_data["timeframe"],
            time=candle_data["time"],
            open=candle_data["open"],
            high=candle_data["high"],
            low=candle_data["low"],
            close=candle_data["close"],
            volume=candle_data["volume"]
        )
        
        # 1. Store in TimescaleDB
        await self.timescale_writer.add_candle(candle)
        
        # 2. Publish to event bus
        await self.event_bus.publish(f"candle:{candle.timeframe}", {
            "symbol": candle.symbol,
            "timeframe": candle.timeframe,
            "open": candle.open,
            "high": candle.high,
            "low": candle.low,
            "close": candle.close,
            "volume": candle.volume,
            "time": candle.time.isoformat()
        })
        
        # 3. Trigger indicator calculation
        await self.event_bus.publish("indicator:trigger", {
            "symbol": candle.symbol,
            "timeframe": candle.timeframe,
            "close": candle.close,
            "volume": candle.volume,
            "time": candle.time.isoformat()
        })
    
    async def start(self):
        """Start all components"""
        logger.info("Starting RealTimeService...")
        
        # Connect to dependencies
        await self.event_bus.connect()
        await self.timescale_writer.connect()
        
        # Start event bus consumer
        asyncio.create_task(self.event_bus.run_consumer())
        
        # Start WebSocket clients
        self.running = True
        
        # Register signal handlers
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self.stop)
        
        # Start Coinbase WebSocket
        await self.coinbase_client.connect()
    
    def stop(self):
        """Graceful shutdown"""
        logger.info("Shutting down RealTimeService...")
        self.running = False
        
        # This will trigger disconnection in the WebSocket client
        asyncio.create_task(self.coinbase_client.disconnect())
        
        # Close connections
        asyncio.create_task(self.timescale_writer.close())
        asyncio.create_task(self.event_bus.close())

async def main():
    """Entry point"""
    service = RealTimeService()
    
    try:
        await service.start()
        
        # Keep service running
        while service.running:
            await asyncio.sleep(1)
            
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
    except Exception as e:
        logger.error(f"Service crashed: {e}", exc_info=True)
    finally:
        service.stop()
        await asyncio.sleep(1)  # Allow cleanup

if __name__ == "__main__":
    asyncio.run(main())
```

### **2.6 Configuration**

**File:** `realtime/config/__init__.py`

```python
import os
from typing import List

# Database
TIMESCALE_DSN = os.getenv(
    "TIMESCALE_DSN",
    "postgresql://postgres:password@localhost:5432/trading"
)

# Redis
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

# Symbols
CRYPTO_SYMBOLS = [
    "BTC-USD", "ETH-USD", "BNB-USD", "SOL-USD",
    "XRP-USD", "ADA-USD", "AVAX-USD", "DOGE-USD"
]

EQUITY_SYMBOLS = [
    "SPY", "QQQ", "^VIX", "AAPL", "MSFT", "GOOGL"
]

# WebSocket settings
WEBSOCKET_RECONNECT_DELAY = 1
WEBSOCKET_MAX_RECONNECT_DELAY = 60
WEBSOCKET_HEARTBEAT_INTERVAL = 30

# Batch sizes
BATCH_SIZE_TICKS = 1000
BATCH_SIZE_CANDLES = 500
FLUSH_INTERVAL_SECONDS = 1.0
```

---

## **Phase 2: Stream Processing & Indicators (Day 2‑3)**

### **3.1 Streaming Indicator Calculator**

**File:** `realtime/processing/streaming_indicators.py`

```python
import numpy as np
from typing import Dict, Optional
from collections import deque
import logging

logger = logging.getLogger(__name__)

class StreamingRSI:
    """Stateful RSI calculator using Wilder's smoothing"""
    
    def __init__(self, period: int = 14):
        self.period = period
        self.gain_ema = 0.0
        self.loss_ema = 0.0
        self.prev_price: Optional[float] = None
        self.initialized = False
        self.count = 0
        
    def update(self, price: float) -> float:
        """Update RSI with new price, return RSI value"""
        if self.prev_price is None:
            self.prev_price = price
            return 50.0
        
        delta = price - self.prev_price
        gain = max(delta, 0.0)
        loss = max(-delta, 0.0)
        
        if not self.initialized:
            # Simple average for first period
            self.gain_ema += gain
            self.loss_ema += loss
            self.count += 1
            
            if self.count >= self.period:
                self.gain_ema /= self.period
                self.loss_ema /= self.period
                self.initialized = True
                self.count = self.period
                
            rsi = 50.0  # Neutral during initialization
        else:
            # Wilder's smoothing (RSI standard)
            self.gain_ema = (self.gain_ema * (self.period - 1) + gain) / self.period
            self.loss_ema = (self.loss_ema * (self.period - 1) + loss) / self.period
        
        self.prev_price = price
        
        if self.initialized:
            if self.loss_ema == 0:
                rsi = 100.0 if self.gain_ema > 0 else 50.0
            else:
                rs = self.gain_ema / self.loss_ema
                rsi = 100 - (100 / (1 + rs))
        else:
            rsi = 50.0
            
        return rsi

class StreamingMACD:
    """Stateful MACD calculator"""
    
    def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9):
        self.fast_period = fast
        self.slow_period = slow
        self.signal_period = signal
        
        self.fast_ema = 0.0
        self.slow_ema = 0.0
        self.signal_ema = 0.0
        
        self.initialized_fast = False
        self.initialized_slow = False
        self.initialized_signal = False
        
        self.fast_count = 0
        self.slow_count = 0
        self.signal_count = 0
        
    def update(self, price: float) -> Dict[str, float]:
        """Update MACD with new price, return MACD, signal, histogram"""
        # Fast EMA
        if not self.initialized_fast:
            self.fast_ema += price
            self.fast_count += 1
            
            if self.fast_count >= self.fast_period:
                self.fast_ema /= self.fast_period
                self.initialized_fast = True
                fast_ema = self.fast_ema
            else:
                fast_ema = price
        else:
            alpha = 2 / (self.fast_period + 1)
            self.fast_ema = price * alpha + self.fast_ema * (1 - alpha)
            fast_ema = self.fast_ema
        
        # Slow EMA
        if not self.initialized_slow:
            self.slow_ema += price
            self.slow_count += 1
            
            if self.slow_count >= self.slow_period:
                self.slow_ema /= self.slow_period
                self.initialized_slow = True
                slow_ema = self.slow_ema
            else:
                slow_ema = price
        else:
            alpha = 2 / (self.slow_period + 1)
            self.slow_ema = price * alpha + self.slow_ema * (1 - alpha)
            slow_ema = self.slow_ema
        
        # MACD line
        macd = fast_ema - slow_ema
        
        # Signal line
        if not self.initialized_signal:
            self.signal_ema += macd
            self.signal_count += 1
            
            if self.signal_count >= self.signal_period:
                self.signal_ema /= self.signal_period
                self.initialized_signal = True
                signal = self.signal_ema
            else:
                signal = macd
        else:
            alpha = 2 / (self.signal_period + 1)
            self.signal_ema = macd * alpha + self.signal_ema * (1 - alpha)
            signal = self.signal_ema
        
        # Histogram
        histogram = macd - signal
        
        return {
            "macd": macd,
            "signal": signal,
            "histogram": histogram
        }

class IndicatorEngine:
    """Manages indicator calculations for multiple symbols/timeframes"""
    
    def __init__(self):
        self.indicators: Dict[str, Dict[str, dict]] = {}
        # Structure: {symbol: {timeframe: {rsi: StreamingRSI, macd: StreamingMACD}}}
        
    def update(self, symbol: str, timeframe: str, price: float, volume: float):
        """Update all indicators for symbol/timeframe"""
        if symbol not in self.indicators:
            self.indicators[symbol] = {}
        if timeframe not in self.indicators[symbol]:
            self.indicators[symbol][timeframe] = {
                "rsi": StreamingRSI(period=14),
                "macd": StreamingMACD(fast=12, slow=26, signal=9),
                "sma20": deque(maxlen=20),
                "sma50": deque(maxlen=50),
                "atr": StreamingATR(period=14),
            }
        
        indicators = self.indicators[symbol][timeframe]
        results = {}
        
        # RSI
        results["rsi"] = indicators["rsi"].update(price)
        
        # MACD
        macd_results = indicators["macd"].update(price)
        results.update(macd_results)
        
        # SMA
        indicators["sma20"].append(price)
        if len(indicators["sma20"]) == 20:
            results["sma20"] = sum(indicators["sma20"]) / 20
        
        indicators["sma50"].append(price)
        if len(indicators["sma50"]) == 50:
            results["sma50"] = sum(indicators["sma50"]) / 50
        
        # ATR (requires high/low data - simplified)
        # In real implementation, you'd need candle data
        
        return results
```

### **3.2 Indicator Consumer Service**

**File:** `realtime/processing/indicator_consumer.py`

```python
import asyncio
import logging
from typing import Dict
import json

from event_bus import EventBus
from streaming_indicators import IndicatorEngine
from storage.timescale_writer import TimescaleWriter

logger = logging.getLogger(__name__)

class IndicatorConsumer:
    """Consumes candle events, calculates indicators, stores results"""
    
    def __init__(self, event_bus: EventBus, timescale_writer: TimescaleWriter):
        self.event_bus = event_bus
        self.timescale_writer = timescale_writer
        self.engine = IndicatorEngine()
        
    async def start(self):
        """Subscribe to candle events"""
        await self.event_bus.subscribe("candle:1m", self.handle_1m_candle)
        await self.event_bus.subscribe("candle:5m", self.handle_5m_candle)
        await self.event_bus.subscribe("candle:1h", self.handle_1h_candle)
        
        logger.info("IndicatorConsumer started")
    
    async def handle_1m_candle(self, event: Dict):
        """Process 1-minute candle"""
        await self._process_candle(event, "1m")
    
    async def handle_5m_candle(self, event: Dict):
        """Process 5-minute candle"""
        await self._process_candle(event, "5m")
    
    async def handle_1h_candle(self, event: Dict):
        """Process 1-hour candle"""
        await self._process_candle(event, "1h")
    
    async def _process_candle(self, event: Dict, timeframe: str):
        """Calculate indicators for candle and store results"""
        try:
            symbol = event["symbol"]
            close = event["close"]
            volume = event["volume"]
            
            # Calculate indicators
            indicators = self.engine.update(symbol, timeframe, close, volume)
            
            # Store in TimescaleDB
            await self.timescale_writer.add_indicators(
                symbol=symbol,
                timeframe=timeframe,
                timestamp=event["time"],
                indicators=indicators
            )
            
            # Publish to event bus for downstream consumers
            await self.event_bus.publish("indicator:updated", {
                "symbol": symbol,
                "timeframe": timeframe,
                "timestamp": event["time"],
                "indicators": indicators
            })
            
            # Log for debugging
            if "rsi" in indicators:
                logger.debug(f"{symbol} {timeframe} RSI: {indicators['rsi']:.2f}")
                
        except Exception as e:
            logger.error(f"Error processing candle: {e}")
```

---

## **Phase 3: Real‑Time Risk & Execution (Day 3‑4)**

### **4.1 Real‑Time Risk Engine**

**File:** `realtime/risk/real_time_risk.py`

```python
import asyncio
from typing import Dict, List, Optional
from dataclasses import dataclass
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

@dataclass
class Position:
    symbol: str
    quantity: float
    entry_price: float
    entry_time: datetime
    current_price: float = 0.0
    trailing_stop: Optional[float] = None
    take_profit_levels: Dict[float, bool] = None  # level -> triggered
    stop_loss: Optional[float] = None
    
    def __post_init__(self):
        if self.take_profit_levels is None:
            self.take_profit_levels = {0.15: False, 0.20: False, 0.25: False}
        if self.stop_loss is None:
            self.stop_loss = self.entry_price * 0.90  # 10% stop-loss

class RealTimeRiskEngine:
    """Evaluates risk per tick, not per batch"""
    
    def __init__(self, event_bus, portfolio_db, telegram_client):
        self.event_bus = event_bus
        self.portfolio_db = portfolio_db
        self.telegram = telegram_client
        
        self.positions: Dict[str, Position] = {}
        self.circuit_breaker_active = False
        self.portfolio_peak = 0.0
        self.portfolio_value = 0.0
        
        # Risk parameters
        self.stop_loss_pct = 0.10
        self.trailing_activation_pct = 0.05
        self.trailing_stop_pct = 0.05
        self.take_profit_levels = [(0.15, 0.33), (0.20, 0.33), (0.25, 1.00)]
        self.circuit_breaker_pct = 0.15
        
    async def start(self):
        """Subscribe to tick events"""
        await self.event_bus.subscribe("market:tick", self.on_tick)
        await self.event_bus.subscribe("portfolio:update", self.on_portfolio_update)
        
        # Load existing positions
        await self.load_positions()
        
        logger.info("RealTimeRiskEngine started")
    
    async def load_positions(self):
        """Load positions from database"""
        # This would query your positions table
        # For now, placeholder
        pass
    
    async def on_tick(self, event: Dict):
        """Evaluate risk for each tick"""
        symbol = event["symbol"]
        price = event["price"]
        timestamp = datetime.fromisoformat(event["timestamp"])
        
        if symbol not in self.positions:
            return
        
        position = self.positions[symbol]
        position.current_price = price
        
        # 1. Stop‑loss (10% below entry)
        if price <= position.stop_loss:
            await self._trigger_exit(
                position, 
                "stop_loss", 
                f"Price ${price:.2f} ≤ stop‑loss ${position.stop_loss:.2f}"
            )
            return
        
        # 2. Trailing stop (activate after 5% gain)
        gain_pct = (price / position.entry_price) - 1
        
        if gain_pct >= self.trailing_activation_pct:
            if position.trailing_stop is None:
                position.trailing_stop = price * (1 - self.trailing_stop_pct)
            else:
                position.trailing_stop = max(
                    position.trailing_stop,
                    price * (1 - self.trailing_stop_pct)
                )
            
            # Ensure trailing stop never goes below entry (breakeven floor)
            position.trailing_stop = max(position.trailing_stop, position.entry_price)
            
            if price <= position.trailing_stop:
                await self._trigger_exit(
                    position,
                    "trailing_stop",
                    f"Price ${price:.2f} ≤ trailing stop ${position.trailing_stop:.2f}"
                )
                return
        
        # 3. Take‑profit scale‑out
        for level, sell_pct in self.take_profit_levels:
            if gain_pct >= level and not position.take_profit_levels.get(level, False):
                await self._partial_exit(
                    position,
                    sell_pct,
                    f"Take‑profit at +{level*100:.0f}% (gain: +{gain_pct*100:.1f}%)"
                )
                position.take_profit_levels[level] = True
        
        # 4. Update portfolio value
        await self._update_portfolio_value()
        
        # 5. Circuit breaker check
        await self._check_circuit_breaker()
    
    async def _trigger_exit(self, position: Position, exit_type: str, reason: str):
        """Exit entire position"""
        logger.info(f"Exiting {position.symbol}: {reason}")
        
        # Publish exit signal
        await self.event_bus.publish("risk:exit", {
            "symbol": position.symbol,
            "exit_type": exit_type,
            "quantity": position.quantity,
            "price": position.current_price,
            "reason": reason,
            "timestamp": datetime.utcnow().isoformat()
        })
        
        # Send Telegram alert
        if self.telegram:
            await self.telegram.send_message(
                f"🚨 {exit_type.replace('_', ' ').title()}: {position.symbol}\n"
                f"Quantity: {position.quantity:.6f}\n"
                f"Price: ${position.current_price:.2f}\n"
                f"Reason: {reason}"
            )
        
        # Remove position
        del self.positions[position.symbol]
        
        # Record in database
        await self._record_risk_event(position, exit_type, reason)
    
    async def _partial_exit(self, position: Position, sell_pct: float, reason: str):
        """Sell partial position"""
        sell_qty = position.quantity * sell_pct
        
        logger.info(f"Partial exit {position.symbol}: {sell_qty:.6f} ({sell_pct*100:.0f}%) - {reason}")
        
        # Publish partial exit
        await self.event_bus.publish("risk:partial_exit", {
            "symbol": position.symbol,
            "exit_type": "take_profit",
            "quantity": sell_qty,
            "price": position.current_price,
            "sell_pct": sell_pct,
            "reason": reason,
            "timestamp": datetime.utcnow().isoformat()
        })
        
        # Update position quantity
        position.quantity -= sell_qty
        
        # If position is now negligible, remove it
        if position.quantity < 0.000001:
            del self.positions[position.symbol]
            await self._record_risk_event(position, "take_profit_full", reason)
        else:
            await self._record_risk_event(position, "take_profit_partial", reason)
    
    async def _update_portfolio_value(self):
        """Recalculate portfolio value"""
        total = 0.0
        
        for position in self.positions.values():
            total += position.quantity * position.current_price
        
        self.portfolio_value = total
        
        # Update peak
        self.portfolio_peak = max(self.portfolio_peak, self.portfolio_value)
    
    async def _check_circuit_breaker(self):
        """Check for portfolio‑wide circuit breaker"""
        if self.portfolio_peak == 0:
            return
        
        drawdown = (self.portfolio_peak - self.portfolio_value) / self.portfolio_peak
        
        if drawdown >= self.circuit_breaker_pct and not self.circuit_breaker_active:
            self.circuit_breaker_active = True
            
            logger.warning(f"Circuit breaker triggered: {drawdown*100:.1f}% drawdown")
            
            # Liquidate all positions
            for symbol, position in list(self.positions.items()):
                await self._trigger_exit(
                    position,
                    "circuit_breaker",
                    f"Portfolio drawdown {drawdown*100:.1f}% ≥ {self.circuit_breaker_pct*100:.0f}%"
                )
            
            # Send critical alert
            if self.telegram:
                await self.telegram.send_message(
                    f"🔴 CIRCUIT BREAKER ACTIVATED\n"
                    f"Drawdown: {drawdown*100:.1f}%\n"
                    f"All positions liquidated\n"
                    f"Trading halted until manual reset"
                )
    
    async def _record_risk_event(self, position: Position, event_type: str, reason: str):
        """Record risk event in database"""
        # This would insert into risk_events table
        pass
```

### **4.2 Execution Engine**

**File:** `realtime/execution/execution_engine.py`

```python
import asyncio
from typing import Dict, Optional
from dataclasses import dataclass
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

@dataclass
class Order:
    symbol: str
    side: str  # 'BUY' or 'SELL'
    quantity: float
    price: float
    order_type: str  # 'market', 'limit'
    portfolio_id: int
    decision_context: Dict  # LLM reasoning, signals
    
class ExecutionEngine:
    """Handles order execution with safety limits"""
    
    def __init__(self, event_bus, portfolio_db, mode: str = "paper"):
        self.event_bus = event_bus
        self.portfolio_db = portfolio_db
        self.mode = mode  # 'paper', 'dry', 'live'
        
        # Safety limits
        self.max_order_usd = 50.0
        self.max_daily_usd = 200.0
        self.cooldown_seconds = 60
        
        # State
        self.daily_total = 0.0
        self.last_order_time: Optional[datetime] = None
        self.orders_today = 0
        
    async def start(self):
        """Subscribe to execution events"""
        await self.event_bus.subscribe("execution:buy", self.handle_buy)
        await self.event_bus.subscribe("execution:sell", self.handle_sell)
        await self.event_bus.subscribe("risk:exit", self.handle_risk_exit)
        await self.event_bus.subscribe("risk:partial_exit", self.handle_partial_exit)
        
        # Reset daily totals at midnight
        asyncio.create_task(self.daily_reset_task())
        
        logger.info(f"ExecutionEngine started in {self.mode} mode")
    
    async def handle_buy(self, event: Dict):
        """Process buy order"""
        # Safety checks
        if not await self._safety_checks(event):
            return
        
        order = Order(
            symbol=event["symbol"],
            side="BUY",
            quantity=event.get("quantity"),
            price=event.get("price"),
            order_type=event.get("order_type", "market"),
            portfolio_id=event.get("portfolio_id", 1),
            decision_context=event.get("decision_context", {})
        )
        
        # Execute based on mode
        if self.mode == "paper":
            await self._execute_paper(order)
        elif self.mode == "dry":
            await self._execute_dry(order)
        elif self.mode == "live":
            await self._execute_live(order)
        
        # Update state
        self._update_order_stats(order)
    
    async def handle_sell(self, event: Dict):
        """Process sell order"""
        # Safety checks
        if not await self._safety_checks(event):
            return
        
        order = Order(
            symbol=event["symbol"],
            side="SELL",
            quantity=event.get("quantity"),
            price=event.get("price"),
            order_type=event.get("order_type", "market"),
            portfolio_id=event.get("portfolio_id", 1),
            decision_context=event.get("decision_context", {})
        )
        
        # Execute based on mode
        if self.mode == "paper":
            await self._execute_paper(order)
        elif self.mode == "dry":
            await self._execute_dry(order)
        elif self.mode == "live":
            await self._execute_live(order)
        
        # Update state
        self._update_order_stats(order)
    
    async def handle_risk_exit(self, event: Dict):
        """Handle risk‑triggered exit"""
        order = Order(
            symbol=event["symbol"],
            side="SELL",
            quantity=event["quantity"],
            price=event["price"],
            order_type="market",  # Always market for risk exits
            portfolio_id=1,
            decision_context={
                "risk_event": event["exit_type"],
                "reason": event["reason"]
            }
        )
        
        # Bypass safety checks for risk exits
        if self.mode == "paper":
            await self._execute_paper(order)
        elif self.mode == "dry":
            await self._execute_dry(order)
        elif self.mode == "live":
            await self._execute_live(order)
    
    async def handle_partial_exit(self, event: Dict):
        """Handle partial exit (take‑profit)"""
        order = Order(
            symbol=event["symbol"],
            side="SELL",
            quantity=event["quantity"],
            price=event["price"],
            order_type="market",
            portfolio_id=1,
            decision_context={
                "risk_event": "take_profit",
                "sell_pct": event["sell_pct"],
                "reason": event["reason"]
            }
        )
        
        if self.mode == "paper":
            await self._execute_paper(order)
        elif self.mode == "dry":
            await self._execute_dry(order)
        elif self.mode == "live":
            await self._execute_live(order)
    
    async def _safety_checks(self, event: Dict) -> bool:
        """Validate order against safety limits"""
        # 1. Cooldown check
        if self.last_order_time:
            elapsed = (datetime.utcnow() - self.last_order_time).total_seconds()
            if elapsed < self.cooldown_seconds:
                logger.warning(f"Order rejected: cooldown ({elapsed:.0f}s < {self.cooldown_seconds}s)")
                return False
        
        # 2. Max order size
        order_value = event.get("quantity", 0) * event.get("price", 0)
        if order_value > self.max_order_usd:
            logger.warning(f"Order rejected: ${order_value:.2f} > ${self.max_order_usd:.2f} limit")
            return False
        
        # 3. Daily limit
        if self.daily_total + order_value > self.max_daily_usd:
            logger.warning(f"Order rejected: daily total would be ${self.daily_total + order_value:.2f} > ${self.max_daily_usd:.2f}")
            return False
        
        return True
    
    async def _execute_paper(self, order: Order):
        """Execute paper trade (update portfolio, no real money)"""
        logger.info(f"PAPER EXECUTION: {order.side} {order.quantity:.6f} {order.symbol} @ ${order.price:.2f}")
        
        # Update portfolio in database
        await self.portfolio_db.execute_trade(order)
        
        # Publish trade executed event
        await self.event_bus.publish("trade:executed", {
            "symbol": order.symbol,
            "side": order.side,
            "quantity": order.quantity,
            "price": order.price,
            "amount_usd": order.quantity * order.price,
            "mode": "paper",
            "timestamp": datetime.utcnow().isoformat(),
            "decision_context": order.decision_context
        })
    
    async def _execute_dry(self, order: Order):
        """Dry run (check if order would succeed, but don't execute)"""
        logger.info(f"DRY RUN: {order.side} {order.quantity:.6f} {order.symbol} @ ${order.price:.2f}")
        
        # Check with exchange API if order would be valid
        # For now, just log
        
        await self.event_bus.publish("trade:dry_executed", {
            "symbol": order.symbol,
            "side": order.side,
            "quantity": order.quantity,
            "price": order.price,
            "mode": "dry",
            "timestamp": datetime.utcnow().isoformat()
        })
    
    async def _execute_live(self, order: Order):
        """Live execution (real money)"""
        logger.warning(f"LIVE EXECUTION: {order.side} {order.quantity:.6f} {order.symbol} @ ${order.price:.2f}")
        
        # This would call the actual exchange API
        # For now, placeholder
        
        await self.event_bus.publish("trade:live_executed", {
            "symbol": order.symbol,
            "side": order.side,
            "quantity": order.quantity,
            "price": order.price,
            "mode": "live",
            "timestamp": datetime.utcnow().isoformat()
        })
    
    def _update_order_stats(self, order: Order):
        """Update order statistics"""
        self.last_order_time = datetime.utcnow()
        order_value = order.quantity * order.price
        self.daily_total += order_value
        self.orders_today += 1
    
    async def daily_reset_task(self):
        """Reset daily totals at midnight UTC"""
        while True:
            now = datetime.utcnow()
            
            # Calculate seconds until midnight
            tomorrow = now.replace(hour=0, minute=0, second=0, microsecond=0)
            tomorrow += timedelta(days=1)
            wait_seconds = (tomorrow - now).total_seconds()
            
            await asyncio.sleep(wait_seconds)
            
            # Reset
            self.daily_total = 0.0
            self.orders_today = 0
            logger.info("Daily execution limits reset")
```

---

## **Phase 4: Integration & Deployment (Day 4‑5)**

### **5.1 Docker Compose Setup**

**File:** `docker-compose.yml`

```yaml
version: '3.8'

services:
  # Database
  timescaledb:
    image: timescale/timescaledb:latest-pg16
    environment:
      POSTGRES_DB: trading
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: password
    ports:
      - "5432:5432"
    volumes:
      - timescale_data:/var/lib/postgresql/data
      - ./schema:/docker-entrypoint-initdb.d
    command: >
      postgres
      -c shared_preload_libraries=timescaledb
      -c max_connections=200
      -c shared_buffers=256MB
  
  # Cache & Message Bus
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    command: redis-server --appendonly yes
    volumes:
      - redis_data:/data
  
  # Stream Processing
  realtime-service:
    build: ./realtime
    depends_on:
      - timescaledb
      - redis
    environment:
      - TIMESCALE_DSN=postgresql://postgres:password@timescaledb:5432/trading
      - REDIS_URL=redis://redis:6379
      - MODE=paper
    volumes:
      - ./realtime:/app
    command: python main.py
    restart: unless-stopped
  
  # Legacy system adapter (runs existing batch jobs)
  legacy-adapter:
    build: .
    depends_on:
      - timescaledb
    environment:
      - DB_DSN=postgresql://postgres:password@timescaledb:5432/trading
      - SQLITE_PATH=./crypto_data.db
    volumes:
      - .:/app
    command: python migration/legacy_adapter.py
    restart: unless-stopped
  
  # Monitoring
  grafana:
    image: grafana/grafana:latest
    ports:
      - "3000:3000"
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=admin
    volumes:
      - grafana_data:/var/lib/grafana
      - ./monitoring/dashboards:/etc/grafana/provisioning/dashboards
  
  prometheus:
    image: prom/prometheus:latest
    ports:
      - "9090:9090"
    volumes:
      - ./monitoring/prometheus.yml:/etc/prometheus/prometheus.yml
      - prometheus_data:/prometheus

volumes:
  timescale_data:
  redis_data:
  grafana_data:
  prometheus_data:
```

### **5.2 Legacy System Adapter**

**File:** `migration/legacy_adapter.py`

```python
"""
Adapter that runs legacy batch jobs while writing to new TimescaleDB.
Ensures backward compatibility during migration.
"""

import asyncio
import sqlite3
import psycopg2
import pandas as pd
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

class LegacyAdapter:
    """Runs existing batch jobs with dual‑write to TimescaleDB"""
    
    def __init__(self):
        self.sqlite_conn = sqlite3.connect('crypto_data.db')
        self.pg_conn = psycopg2.connect(
            "postgresql://postgres:password@localhost:5432/trading"
        )
        
        # Import legacy modules
        sys.path.insert(0, '.')
        from crypto_data_collector import CryptoDataCollector
        from equity_data_collector import EquityDataCollector
        from correlation_tracker import CorrelationTracker
        
        self.crypto_collector = CryptoDataCollector()
        self.equity_collector = EquityDataCollector()
        self.correlation_tracker = CorrelationTracker()
    
    async def run_daily_jobs(self):
        """Run all daily batch jobs with dual‑write"""
        logger.info("Starting daily batch jobs...")
        
        # 1. Crypto data collection
        await self._run_with_dual_write(
            self.crypto_collector.collect_daily_data,
            table_mapping={'candlesticks': 'candles', 'indicators': 'indicators'}
        )
        
        # 2. Equity data collection
        await self._run_with_dual_write(
            self.equity_collector.collect_daily_data,
            table_mapping={'candlesticks': 'candles'}
        )
        
        # 3. Correlation snapshot
        await self._run_with_dual_write(
            self.correlation_tracker.calculate_correlations,
            table_mapping={'correlations': 'correlations', 'market_regime': 'market_regime'}
        )
        
        logger.info("Daily batch jobs completed")
    
    async def _run_with_dual_write(self, legacy_function, table_mapping: dict):
        """Run legacy function and copy results to TimescaleDB"""
        try:
            # Run legacy function (writes to SQLite)
            result = legacy_function()
            
            # Copy data to TimescaleDB
            for sqlite_table, pg_table in table_mapping.items():
                df = pd.read_sql_query(f"SELECT * FROM {sqlite_table}", self.sqlite_conn)
                
                if not df.empty:
                    df.to_sql(pg_table, self.pg_conn, if_exists='append', index=False)
                    logger.info(f"Copied {len(df)} rows from {sqlite_table} to {pg_table}")
                    
        except Exception as e:
            logger.error(f"Dual‑write failed: {e}")
    
    async def run_intraday_jobs(self):
        """Run intraday jobs (every 4 hours)"""
        # Similar pattern for intraday updates
        pass

async def main():
    adapter = LegacyAdapter()
    
    # Schedule jobs
    while True:
        now = datetime.now()
        
        # Daily jobs at 8:30 AM
        if now.hour == 8 and now.minute == 30:
            await adapter.run_daily_jobs()
        
        # Intraday jobs every 4 hours
        if now.hour % 4 == 0 and now.minute == 0:
            await adapter.run_intraday_jobs()
        
        await asyncio.sleep(60)  # Check every minute

if __name__ == "__main__":
    asyncio.run(main())
```

### **5.3 Monitoring Dashboard**

**File:** `monitoring/dashboards/trading.json`

```json
{
  "dashboard": {
    "title": "Real‑Time Trading System",
    "panels": [
      {
        "title": "WebSocket Latency",
        "targets": [
          {"expr": "tick_latency_seconds", "legendFormat": "{{symbol}}"}
        ],
        "type": "graph"
      },
      {
        "title": "Portfolio Value",
        "targets": [
          {"expr": "portfolio_value_usd", "legendFormat": "Total"}
        ],
        "type": "graph"
      },
      {
        "title": "Risk Events",
        "targets": [
          {"expr": "rate(risk_events_total[5m])", "legendFormat": "Events/min"}
        ],
        "type": "stat"
      },
      {
        "title": "System Health",
        "targets": [
          {"expr": "up", "legendFormat": "{{job}}"}
        ],
        "type": "singlestat"
      }
    ]
  }
}
```

---

## **Getting Started: First 24 Hours**

### **Step 1: Set up TimescaleDB**
```bash
# Install Docker if not present
sudo apt install docker.io docker-compose

# Clone and set up
cd /home/satyamini/.openclaw/workspace/projects/edoras
mkdir -p realtime migration monitoring

# Start database
docker-compose up -d timescaledb redis

# Initialize schema
psql -h localhost -U postgres -d trading -f schema/timescale_schema.sql
```

### **Step 2: Implement Coinbase WebSocket**
```bash
# Create directory structure
mkdir -p realtime/{ingest,processing,storage,config,risk,execution}

# Write the WebSocket client
vim realtime/ingest/coinbase_websocket.py

# Test connection
python -m realtime.ingest.coinbase_websocket
```

### **Step 3: Run in Shadow Mode**
```python
# Test with single symbol first
CRYPTO_SYMBOLS = ["BTC-USD"]

# Run real‑time system alongside legacy
# Compare outputs for 24 hours
```

### **Step 4: Gradual Cut‑over**
1. **Day 1:** BTC‑USD only, paper trades on real‑time system
2. **Day 2:** Add ETH‑USD, compare signals with legacy
3. **Day 3:** Add remaining crypto symbols
4. **Day 4:** Add equity symbols (SPY, QQQ)
5. **Day 5:** Full cut‑over, legacy system becomes backup

---

## **Success Metrics**

| Metric | Target | Measurement |
|--------|--------|-------------|
| **End‑to‑end latency** | < 100ms from tick to risk check | Prometheus `tick_to_risk_latency` |
| **Data completeness** | 99.9% of ticks captured | Compare with Coinbase historical |
| **System uptime** | 99.95% (≤ 4h downtime/month) | Grafana + alerting |
| **Backtest parity** | P&L within 0.1% of batch system | Walk‑forward validation |
| **Risk response time** | < 50ms from trigger to exit order | `risk_response_latency` |

---

## **Risk Mitigation**

### **Technical Risks**
1. **WebSocket disconnections:** Exponential backoff, multiple connections
2. **Database latency:** Connection pooling, write batching, read replicas
3. **Memory leaks:** Regular profiling, container memory limits
4. **Network partitions:** Circuit breakers, graceful degradation

### **Trading Risks**
1. **Erroneous orders:** Multiple safety checks, daily limits
2. **Market impact:** Small position sizes ($50 max/order)
3. **Flash crashes:** Circuit breakers, volatility filters
4. **Regime changes:** Correlation monitoring, VIX‑based adjustments

### **Operational Risks**
1. **Deployment errors:** Blue‑green deployment, feature flags
2. **Monitoring gaps:** Comprehensive metrics, alerting
3. **Team knowledge:** Documentation, runbooks, pair programming

---

## **Next Steps After MVP**

1. **Machine Learning Integration**
   - Reinforcement learning for dynamic position sizing
   - LSTM for price prediction
   - Anomaly detection for market manipulation

2. **Multi‑Exchange Support**
   - Binance, Kraken, FTX (if returns)
   - Arbitrage opportunities
   - Liquidity aggregation

3. **Advanced Risk Models**
   - Value‑at‑Risk (VaR) in real‑time
   - Stress testing with historical scenarios
   - Monte Carlo simulation for tail risk

4. **Regulatory Compliance**
   - SEC/FINRA audit trail
   - Tax reporting automation
   - Compliance dashboard for manual review

---

## **Team Requirements**

| Role | Skills | Time Commitment |
|------|--------|-----------------|
| **Backend Engineer** | Python async, WebSockets, TimescaleDB | Full‑time |
| **Data Engineer** | Stream processing, ETL, monitoring | 50% |
| **Quant Developer** | Financial mathematics, backtesting | 50% |
| **DevOps Engineer** | Docker, Kubernetes, monitoring | 25% |
| **Product Manager** | Requirements, prioritization, metrics | 25% |

**Estimated timeline:** 4‑6 weeks to production‑ready MVP.

---

## **Conclusion**

This roadmap transforms our batch‑oriented system into a **real‑time wealth management platform** capable of capturing alpha through speed, better risk management, and adaptive execution.

**Key philosophy:** Start small, validate thoroughly, scale incrementally. The market rewards robustness over complexity.

**Deliverable by Friday:** Working paper‑trading system with real‑time data for BTC‑USD and basic risk management.

Let's build. 🦞

---

*Document version: 2026‑03‑11 · Based on existing `projects/coinbase‑analysis/` architecture*  
*Contact: @satyanabot on Telegram · Updates: `ROADMAP/CHANGELOG.md`*