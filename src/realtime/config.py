"""
Configuration for real-time trading system.
"""

import os
from typing import List

from config import DB_PATH  # canonical absolute path

# Symbols to track
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

# Risk parameters
STOP_LOSS_PCT = 0.10
TRAILING_STOP_ACTIVATION_PCT = 0.05
TRAILING_STOP_PCT = 0.05
TAKE_PROFIT_LEVELS = [(0.15, 0.33), (0.20, 0.33), (0.25, 1.00)]
CIRCUIT_BREAKER_PCT = 0.15

# Execution limits
MAX_ORDER_USD = 50.0
MAX_DAILY_USD = 200.0
ORDER_COOLDOWN_SECONDS = 60

# Portfolio
DEFAULT_PORTFOLIO_ID = 1
INITIAL_CAPITAL = 1000.0

# Monitoring
TICK_LOG_INTERVAL = 100  # Log every Nth tick