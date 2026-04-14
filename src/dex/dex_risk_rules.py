#!/usr/bin/env python3
"""
DEX-specific risk checks — layered on top of risk_manager.py.

Does NOT modify risk_manager.py. Provides additional DEX-specific safety
checks that run before or alongside standard risk management.
"""

import logging
import os
import sqlite3
import sys
from datetime import datetime
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import DB_PATH, DEX_CONFIG, get_account_ids

logger = logging.getLogger(__name__)


class DexRiskRules:
    """DEX-specific risk checks for the Arwen portfolio."""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self.cfg = DEX_CONFIG

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def check_liquidity(self, symbol: str) -> Optional[str]:
        """Check if token has minimum liquidity. Returns warning or None."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT dt.liquidity, dt.volume_24h, dt.holder_count, dt.is_verified "
            "FROM securities s JOIN dex_tokens dt ON dt.security_id = s.id "
            "WHERE s.symbol = ? AND s.is_dex = 1",
            (symbol,),
        ).fetchone()
        conn.close()

        if not row:
            return f"{symbol}: no DEX metadata found"
        if not row["liquidity"]:
            return f"{symbol}: liquidity data unavailable"
        if row["liquidity"] < self.cfg["min_liquidity_usd"]:
            return (f"{symbol}: liquidity ${row['liquidity']:.0f} "
                    f"below min ${self.cfg['min_liquidity_usd']:.0f}")
        return None

    def check_holder_count(self, symbol: str) -> Optional[str]:
        """Check minimum holder count."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT dt.holder_count FROM securities s "
            "JOIN dex_tokens dt ON dt.security_id = s.id "
            "WHERE s.symbol = ? AND s.is_dex = 1",
            (symbol,),
        ).fetchone()
        conn.close()

        if row and row["holder_count"] and row["holder_count"] < self.cfg["min_holder_count"]:
            return (f"{symbol}: {row['holder_count']} holders "
                    f"below min {self.cfg['min_holder_count']}")
        return None

    def estimate_slippage(self, symbol: str, trade_usd: float) -> float:
        """Estimate slippage for a given trade size.

        Uses simplified AMM model: slippage ≈ trade_size / (2 × pool_liquidity)
        Returns estimated slippage as a percentage.
        """
        conn = self._get_conn()
        row = conn.execute(
            "SELECT dt.liquidity FROM securities s "
            "JOIN dex_tokens dt ON dt.security_id = s.id "
            "WHERE s.symbol = ? AND s.is_dex = 1",
            (symbol,),
        ).fetchone()
        conn.close()

        if not row or not row["liquidity"] or row["liquidity"] <= 0:
            return 99.0  # unknown = assume worst case

        return (trade_usd / (2 * row["liquidity"])) * 100

    def check_slippage(self, symbol: str, trade_usd: float) -> Optional[str]:
        """Check if estimated slippage exceeds threshold."""
        slippage = self.estimate_slippage(symbol, trade_usd)
        if slippage > self.cfg["max_slippage_percent"]:
            return (f"{symbol}: estimated slippage {slippage:.1f}% "
                    f"exceeds max {self.cfg['max_slippage_percent']}%")
        return None

    def check_position_vs_liquidity(self, symbol: str,
                                     portfolio_id: int = 4) -> Optional[str]:
        """Warn if position value > 2% of pool liquidity."""
        conn = self._get_conn()
        # Phase 3: query via account_ids
        account_ids = get_account_ids(portfolio_id, db_path=self.db_path)
        if account_ids:
            placeholders = ','.join('?' * len(account_ids))
            pos = conn.execute(
                f"SELECT quantity * COALESCE(current_price, entry_price) as value "
                f"FROM positions WHERE account_id IN ({placeholders}) AND symbol = ? AND status = 'open'",
                account_ids + [symbol],
            ).fetchone()
        else:
            pos = conn.execute(
                "SELECT quantity * COALESCE(current_price, entry_price) as value "
                "FROM positions WHERE portfolio_id = ? AND symbol = ? AND status = 'open'",
                (portfolio_id, symbol),
            ).fetchone()
        liq = conn.execute(
            "SELECT dt.liquidity FROM securities s "
            "JOIN dex_tokens dt ON dt.security_id = s.id "
            "WHERE s.symbol = ? AND s.is_dex = 1",
            (symbol,),
        ).fetchone()
        conn.close()

        if pos and liq and liq["liquidity"] and liq["liquidity"] > 0:
            pct = (pos["value"] / liq["liquidity"]) * 100
            if pct > 2.0:
                return (f"{symbol}: position is {pct:.1f}% of pool liquidity "
                        f"(${pos['value']:.0f} / ${liq['liquidity']:.0f})")
        return None

    def run_all_checks(self, symbol: str, trade_usd: float = 0,
                       portfolio_id: int = 4) -> Tuple[bool, List[str]]:
        """Run all DEX risk checks. Returns (is_safe, [warnings])."""
        warnings = []

        w = self.check_liquidity(symbol)
        if w:
            warnings.append(w)

        w = self.check_holder_count(symbol)
        if w:
            warnings.append(w)

        if trade_usd > 0:
            w = self.check_slippage(symbol, trade_usd)
            if w:
                warnings.append(w)

        w = self.check_position_vs_liquidity(symbol, portfolio_id)
        if w:
            warnings.append(w)

        # Any liquidity or slippage warning is a hard block
        is_safe = not any("liquidity" in w.lower() or "slippage" in w.lower()
                          for w in warnings)
        return is_safe, warnings
