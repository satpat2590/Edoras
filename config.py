#!/usr/bin/env python3
"""Central configuration for the trading system."""

import os
import json
from dataclasses import dataclass, field
from typing import Dict, List

# ── Paths ────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "crypto_data.db")
EQUITY_DB_PATH = os.path.join(BASE_DIR, "..", "company-financials", "company_financials.db")
PAPER_STATE_FILE = os.path.join(BASE_DIR, "paper_portfolio_state.json")
RISK_STATE_FILE = os.path.join(BASE_DIR, "risk_state.json")
SIGNAL_STATE_FILE = os.path.join(BASE_DIR, "signal_trading_state.json")
BACKTEST_RESULTS_DIR = os.path.join(BASE_DIR, "backtest_results")

# ── Telegram ─────────────────────────────────────────────────────────────
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_GROUP_ID = os.getenv("TELEGRAM_GROUP_ID", "")

# ── API rate limits ──────────────────────────────────────────────────────
COINBASE_MAX_RPS = 8  # leave headroom from 10/s limit
COINBASE_MAX_CANDLES = 300  # per request

# ── Symbols ──────────────────────────────────────────────────────────────
PORTFOLIO_SYMBOLS = [
    "ETH-USD", "BTC-USD", "XRP-USD", "TROLL-USD",
    "BONK-USD", "FET-USD", "AMP-USD", "GRT-USD",
]

TOP_CRYPTO_SYMBOLS = [
    "BTC-USD", "ETH-USD", "BNB-USD", "SOL-USD", "XRP-USD",
    "ADA-USD", "AVAX-USD", "DOGE-USD", "DOT-USD",
    "LINK-USD", "SHIB-USD", "LTC-USD", "UNI-USD",
]

EQUITY_SYMBOLS = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "META",
    "NVDA", "TSLA", "JPM", "JNJ", "V",
]

# Index / macro symbols fetched via yfinance for correlation tracking
INDEX_SYMBOLS = ["SPY", "QQQ", "^VIX"]

# ── Timeframes ───────────────────────────────────────────────────────────
TIMEFRAMES = {"1h": "ONE_HOUR", "4h": "SIX_HOUR", "1d": "ONE_DAY"}

TIMEFRAME_WEIGHTS_CRYPTO = {"1d": 0.40, "4h": 0.35, "1h": 0.25}
TIMEFRAME_WEIGHTS_EQUITY = {"1d": 0.50, "4h": 0.35, "1h": 0.15}

# ── Scoring component weights ────────────────────────────────────────────
SCORING_WEIGHTS = {
    "momentum": 0.40,
    "trend": 0.25,
    "volatility": 0.15,
    "volume": 0.10,
    "risk_adjusted": 0.10,
}

# ── Signal thresholds (crypto) ───────────────────────────────────────────
CRYPTO_RSI_OVERSOLD = 30
CRYPTO_RSI_OVERBOUGHT = 70
CRYPTO_RSI_WEAK_OVERSOLD = 35
CRYPTO_RSI_WEAK_OVERBOUGHT = 65

# ── Signal thresholds (equity — less volatile) ───────────────────────────
EQUITY_RSI_OVERSOLD = 35
EQUITY_RSI_OVERBOUGHT = 65
EQUITY_RSI_WEAK_OVERSOLD = 40
EQUITY_RSI_WEAK_OVERBOUGHT = 60

# ── Risk management ─────────────────────────────────────────────────────
STOP_LOSS_PCT = 0.10          # 10 % below entry
TRAILING_STOP_ACTIVATION = 0.05  # activate after 5 % gain
TRAILING_STOP_PCT = 0.05      # trail 5 % from peak (fallback if no ATR)

TAKE_PROFIT_LEVELS = [
    (0.15, 0.33),  # at +15 % gain sell 33 %
    (0.20, 0.33),  # at +20 % sell another 33 %
    (0.25, 1.00),  # at +25 % sell remainder
]

MAX_PORTFOLIO_DRAWDOWN = 0.15  # 15 % circuit breaker
MAX_POSITION_PCT = 0.25        # 25 % of portfolio per position
MAX_SECTOR_PCT = 0.40          # 40 % per sector

# ── Portfolios ──────────────────────────────────────────────────────────
# Portfolio IDs (DB primary keys — stable across code)
PORTFOLIO_GALADRIEL = 1   # paper trading (active)
PORTFOLIO_THRANDUIL = 2   # live trading (future)
PORTFOLIO_ELROND = 3      # Coinbase tracking (co-managed)
PORTFOLIO_ARWEN = 4       # Aleph's DEX portfolio (live, on-chain)

# Trader IDs
TRADER_ALEPH = 1
TRADER_REGI = 2

# Backwards compat — prefer get_active_portfolios() for multi-portfolio support
ACTIVE_PORTFOLIO_ID = PORTFOLIO_GALADRIEL

# ── DEX Configuration ──────────────────────────────────────────────────
DEX_CONFIG = {
    "enabled": True,
    "default_chain": "base",
    "bankr_api_url": "https://api.bankr.bot",
    "bankr_config_path": os.path.expanduser("~/.bankr/config.json"),
    # Risk thresholds
    "min_liquidity_usd": 100_000,
    "min_volume_24h_usd": 50_000,
    "max_slippage_percent": 5.0,
    "max_position_size_percent": 10.0,
    "min_token_age_days": 7,
    "min_holder_count": 100,
    # Execution
    "supported_chains": ["base", "ethereum"],
    "job_poll_interval_sec": 3,
    "job_poll_max_wait_sec": 60,
    "max_single_order_usd": 100.0,
    "max_daily_volume_usd": 500.0,
}

DEX_SYMBOLS = ["VVV-BASE", "BNKR-BASE", "WETH-BASE", "USDC-BASE"]


def get_active_portfolios(db_path: str = None) -> list:
    """Load all active portfolios from the database.

    Returns list of dicts with portfolio config:
        id, name, mode, asset_class, initial_capital, symbols, strategy_routes,
        default_timeframe, state_file
    """
    import sqlite3
    _db = db_path or DB_PATH
    conn = sqlite3.connect(_db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, name, mode, asset_class, initial_capital, currency, "
        "symbols_json, strategy_routes_json, default_timeframe, state_file "
        "FROM portfolios WHERE is_active = 1 ORDER BY id"
    ).fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        d["symbols"] = json.loads(d.pop("symbols_json")) if d.get("symbols_json") else PORTFOLIO_SYMBOLS
        d["strategy_routes"] = json.loads(d.pop("strategy_routes_json")) if d.get("strategy_routes_json") else {}
        result.append(d)
    return result

# ── Paper trading ────────────────────────────────────────────────────────
PAPER_INITIAL_CAPITAL = 1000.0
PAPER_TRANSACTION_COST = 0.001  # 0.1 %
PAPER_MIN_TRADE_USD = 10.0

# ── Data quality thresholds ──────────────────────────────────────────────
MIN_DAYS_FOR_INDICATORS = 50
MIN_DAYS_FOR_SMA200 = 200
MIN_DAYS_FOR_SHARPE = 30
MIN_DAYS_FOR_VAR = 60
MIN_DAYS_FOR_DRAWDOWN = 30
MIN_DAYS_FOR_CORRELATION = 30

# ── Backfill ─────────────────────────────────────────────────────────────
DEFAULT_BACKFILL_DAYS = 1100  # ~3 years for statistical significance

# ── Live execution safety limits ─────────────────────────────────────────
LIVE_MAX_SINGLE_ORDER_USD = 50.0
LIVE_MAX_DAILY_VOLUME_USD = 200.0
LIVE_MAX_OPEN_ORDERS = 5
LIVE_MIN_ORDER_INTERVAL_SEC = 60

# ── Category mappings ────────────────────────────────────────────────────
CRYPTO_CATEGORIES = {
    "large_cap": {"BTC-USD", "ETH-USD", "BNB-USD", "SOL-USD", "XRP-USD", "ADA-USD", "AVAX-USD", "DOGE-USD", "DOT-USD", "LINK-USD", "SHIB-USD", "LTC-USD", "UNI-USD"},
    "defi": {"UNI-USD", "AAVE-USD", "COMP-USD", "MKR-USD", "SNX-USD", "CRV-USD"},
    "meme": {"DOGE-USD", "SHIB-USD", "BONK-USD", "TROLL-USD", "PEPE-USD"},
    "layer1": {"BTC-USD", "ETH-USD", "SOL-USD", "ADA-USD", "AVAX-USD", "DOT-USD"},
    "ai": {"FET-USD", "OCEAN-USD", "NMR-USD"},
    "gaming": {"SAND-USD", "MANA-USD", "GALA-USD", "IMX-USD"},
}

EQUITY_SECTORS = {
    "tech": {"AAPL", "MSFT", "GOOGL", "META", "NVDA"},
    "consumer": {"AMZN", "TSLA"},
    "finance": {"JPM", "V"},
    "healthcare": {"JNJ"},
}


def get_asset_type(symbol: str) -> str:
    """Return asset class from securities table, falling back to heuristics."""
    try:
        import sqlite3
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute(
            "SELECT asset_class FROM securities WHERE symbol=? AND is_active=1 LIMIT 1",
            (symbol,),
        ).fetchone()
        conn.close()
        if row:
            return row[0]
    except Exception:
        pass
    # Heuristic fallback
    if symbol in INDEX_SYMBOLS or symbol.startswith("^"):
        return "index"
    if symbol.endswith("-USD"):
        return "crypto"
    return "equity"


def get_sector(symbol: str) -> str:
    """Return sector from securities table, falling back to hardcoded maps."""
    try:
        import sqlite3
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute(
            "SELECT sector FROM securities WHERE symbol=? AND is_active=1 LIMIT 1",
            (symbol,),
        ).fetchone()
        conn.close()
        if row and row[0]:
            return row[0]
    except Exception:
        pass
    # Hardcoded fallback
    for sector, syms in CRYPTO_CATEGORIES.items():
        if symbol in syms:
            return sector
    for sector, syms in EQUITY_SECTORS.items():
        if symbol in syms:
            return sector
    return "other"


def get_security_info(symbol: str) -> dict:
    """Get full security metadata from the securities table."""
    try:
        import sqlite3
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT s.*, e.code as exchange_code, e.exchange_type, e.quote_currency as exchange_quote "
            "FROM securities s JOIN exchanges e ON s.exchange_id=e.id "
            "WHERE s.symbol=? AND s.is_active=1 LIMIT 1",
            (symbol,),
        ).fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception:
        return None


def all_symbols() -> List[str]:
    """Deduplicated union of all tracked symbols."""
    seen = set()
    result = []
    for sym in PORTFOLIO_SYMBOLS + TOP_CRYPTO_SYMBOLS + EQUITY_SYMBOLS + INDEX_SYMBOLS:
        if sym not in seen:
            seen.add(sym)
            result.append(sym)
    return result


# ── Account resolver ──────────────────────────────────────────────────────
# In-memory cache: (portfolio_id, venue_id) → account_id
_account_cache: Dict[tuple, int] = {}


def resolve_account_id(portfolio_id: int, venue_id: int = None,
                       venue_code: str = None,
                       db_path: str = None) -> int:
    """Resolve account_id from the accounts bridge table.

    Provide either venue_id or venue_code. Returns account_id or raises ValueError.
    Results are cached for the process lifetime.
    """
    _db = db_path or DB_PATH
    if venue_code and not venue_id:
        import sqlite3 as _sq
        c = _sq.connect(_db)
        r = c.execute("SELECT id FROM exchanges WHERE code = ?", (venue_code,)).fetchone()
        c.close()
        if not r:
            raise ValueError(f"Unknown venue code: {venue_code}")
        venue_id = r[0]

    key = (portfolio_id, venue_id)
    if key in _account_cache:
        return _account_cache[key]

    import sqlite3 as _sq
    conn = _sq.connect(_db)
    row = conn.execute(
        "SELECT id FROM accounts WHERE portfolio_id = ? AND venue_id = ? AND status = 'active' LIMIT 1",
        (portfolio_id, venue_id),
    ).fetchone()
    conn.close()
    if not row:
        raise ValueError(
            f"No active account for portfolio_id={portfolio_id}, venue_id={venue_id}"
        )
    _account_cache[key] = row[0]
    return row[0]


# Cache: portfolio_id → list of active account IDs
_portfolio_accounts_cache: Dict[int, List[int]] = {}


def get_account_ids(portfolio_id: int, db_path: str = None) -> List[int]:
    """Return all active account IDs for a portfolio.

    Used by Phase 3 query migration: queries filter by account_id
    instead of portfolio_id on trades/positions tables.
    Results are cached for the process lifetime.
    """
    if portfolio_id in _portfolio_accounts_cache:
        return _portfolio_accounts_cache[portfolio_id]

    _db = db_path or DB_PATH
    import sqlite3 as _sq
    conn = _sq.connect(_db)
    rows = conn.execute(
        "SELECT id FROM accounts WHERE portfolio_id = ? AND status = 'active'",
        (portfolio_id,),
    ).fetchall()
    conn.close()
    ids = [r[0] for r in rows]
    _portfolio_accounts_cache[portfolio_id] = ids
    return ids
