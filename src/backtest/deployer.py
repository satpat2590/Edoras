"""
Strategy Deployer — Bridge between backtest catalogue and live trading.

Responsibilities:
  1. Sync backtest strategy registry → warehouse strategy_registry table
  2. Apply portfolio templates → update strategy_routes_json + portfolio_strategies
  3. Swap individual strategy routes (used by regime monitor)
  4. Resolve strategy_id for trade attribution
"""

import json
import logging
import os
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from .strategies import STRATEGY_REGISTRY
from config import DB_PATH as _CONFIG_DB_PATH

logger = logging.getLogger(__name__)

DEFAULT_DB = _CONFIG_DB_PATH

# Strategy type classification for the warehouse
STRATEGY_TYPE_MAP = {
    "ScoreBased": "momentum",
    "ScoreBasedRelaxed": "momentum",
    "EnhancedScoreBased": "momentum",
    "MACDCross": "momentum",
    "ADXTrend": "trend_following",
    "BollingerReversion": "mean_reversion",
    "MultiSignal": "multi_factor",
    "TSMOM": "momentum",
    "TSMOM_3M": "momentum",
    "PairsTrading": "mean_reversion",
    "PairsTrading_Aggressive": "mean_reversion",
    "RegimeAware": "adaptive",
    "RegimeAware_Heuristic": "adaptive",
}


def sync_registry(db_path: str = DEFAULT_DB) -> int:
    """Ensure all backtest strategies are registered in the warehouse strategy_registry table.

    Returns the number of newly registered strategies.
    """
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    existing = {
        row[0] for row in cur.execute("SELECT name FROM strategy_registry").fetchall()
    }

    added = 0
    for name, cls in STRATEGY_REGISTRY.items():
        if name in existing:
            continue

        desc = cls.describe() if hasattr(cls, "describe") else name
        class_name = cls.__name__
        strategy_type = STRATEGY_TYPE_MAP.get(name, "unknown")
        params = cls().get_parameters() if hasattr(cls, "get_parameters") else {}

        cur.execute("""
            INSERT INTO strategy_registry
            (name, class_name, description, supported_security_types,
             supported_indicator_profiles, default_params_json, is_active,
             strategy_type, parameters)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
        """, (
            name, class_name, desc,
            '["crypto","equity"]', '["standard"]',
            json.dumps(params), strategy_type, json.dumps(params),
        ))
        added += 1
        logger.info(f"Registered strategy: {name} ({strategy_type})")

    # Link registry entries to their best catalogue result
    try:
        cur.execute("""
            UPDATE strategy_registry
            SET qualifying_catalogue_id = (
                SELECT c.id
                FROM strategy_catalogue c
                WHERE c.strategy_name = strategy_registry.name
                  AND c.total_trades >= 3
                ORDER BY c.sharpe_ratio DESC
                LIMIT 1
            ),
            qualified_at = datetime('now')
            WHERE qualifying_catalogue_id IS NULL
              AND is_active = 1
              AND EXISTS (
                  SELECT 1 FROM strategy_catalogue c
                  WHERE c.strategy_name = strategy_registry.name
                    AND c.total_trades >= 3
              )
        """)
    except sqlite3.OperationalError:
        # qualifying_catalogue_id column may not exist yet (pre-migration)
        pass

    conn.commit()
    conn.close()
    return added


def get_strategy_id(name: str, db_path: str = DEFAULT_DB) -> Optional[int]:
    """Look up strategy_registry.id for a strategy name."""
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT id FROM strategy_registry WHERE name = ?", (name,)
    ).fetchone()
    conn.close()
    return row[0] if row else None


def get_strategy_id_map(db_path: str = DEFAULT_DB) -> Dict[str, int]:
    """Return {strategy_name: strategy_id} for all registered strategies."""
    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT name, id FROM strategy_registry").fetchall()
    conn.close()
    return {name: sid for name, sid in rows}


def apply_template(
    template_name: str,
    portfolio_id: int = 1,
    db_path: str = DEFAULT_DB,
    dry_run: bool = False,
) -> dict:
    """Apply a portfolio template to a portfolio.

    Updates:
      - portfolios.strategy_routes_json
      - portfolios.symbols_json
      - portfolio_strategies rows (with allocation_pct)

    Returns a summary dict of changes made.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Load template
    tmpl = conn.execute(
        "SELECT * FROM portfolio_templates WHERE name = ?", (template_name,)
    ).fetchone()
    if not tmpl:
        conn.close()
        raise ValueError(f"Template '{template_name}' not found")

    allocations = json.loads(tmpl["strategy_allocations_json"])

    # Load current portfolio
    portfolio = conn.execute(
        "SELECT name, strategy_routes_json, symbols_json FROM portfolios WHERE id = ?",
        (portfolio_id,)
    ).fetchone()
    if not portfolio:
        conn.close()
        raise ValueError(f"Portfolio id={portfolio_id} not found")

    old_routes = json.loads(portfolio["strategy_routes_json"] or "{}")
    old_symbols = json.loads(portfolio["symbols_json"] or "[]")

    # Build new routes and symbols from template
    new_routes = {}
    new_symbols = set()
    for alloc in allocations:
        symbol = alloc["symbol"]
        strategy = alloc["strategy"]
        timeframe = alloc.get("timeframe", "1d")
        new_symbols.add(symbol)

        # Get strategy params from registry
        cls = STRATEGY_REGISTRY.get(strategy)
        params = cls().get_parameters() if cls else {}

        new_routes[symbol] = {
            "strategy": strategy,
            "timeframe": timeframe,
            "weight": alloc["weight"],
            "params": params if params else {},
        }

    changes = {
        "template": template_name,
        "portfolio": portfolio["name"],
        "symbols_added": list(new_symbols - set(old_symbols)),
        "symbols_removed": list(set(old_symbols) - new_symbols),
        "routes_changed": {},
    }

    for sym in new_symbols:
        old = old_routes.get(sym, {}).get("strategy")
        new = new_routes[sym]["strategy"]
        if old != new:
            changes["routes_changed"][sym] = {"from": old, "to": new}

    if dry_run:
        conn.close()
        return changes

    # Apply updates
    conn.execute(
        "UPDATE portfolios SET strategy_routes_json = ?, symbols_json = ?, updated_at = datetime('now') WHERE id = ?",
        (json.dumps(new_routes), json.dumps(sorted(new_symbols)), portfolio_id),
    )

    # Update portfolio_strategies
    strategy_id_map = get_strategy_id_map(db_path)

    # Retire old assignments
    conn.execute(
        "UPDATE portfolio_strategies SET is_active = 0, retired_at = datetime('now') WHERE portfolio_id = ? AND is_active = 1",
        (portfolio_id,),
    )

    # Upsert new assignments
    for alloc in allocations:
        sid = strategy_id_map.get(alloc["strategy"])
        if sid:
            existing = conn.execute(
                "SELECT id FROM portfolio_strategies WHERE portfolio_id = ? AND strategy_id = ?",
                (portfolio_id, sid)
            ).fetchone()
            if existing:
                conn.execute("""
                    UPDATE portfolio_strategies
                    SET allocation_pct = ?, is_active = 1, assigned_at = datetime('now'), retired_at = NULL
                    WHERE portfolio_id = ? AND strategy_id = ?
                """, (round(alloc["weight"] * 100, 2), portfolio_id, sid))
            else:
                conn.execute("""
                    INSERT INTO portfolio_strategies
                    (portfolio_id, strategy_id, allocation_pct, is_active, assigned_at)
                    VALUES (?, ?, ?, 1, datetime('now'))
                """, (portfolio_id, sid, round(alloc["weight"] * 100, 2)))

    conn.commit()
    conn.close()

    logger.info(f"Applied template '{template_name}' to portfolio {portfolio_id}: "
                f"{len(new_routes)} routes, {len(new_symbols)} symbols")
    return changes


def swap_strategy(
    symbol: str,
    new_strategy: str,
    portfolio_id: int = 1,
    timeframe: str = "1d",
    reason: str = "",
    db_path: str = DEFAULT_DB,
) -> dict:
    """Swap the strategy for a single symbol in a portfolio's routing table.

    Used by the regime monitor for dynamic strategy adaptation.
    Logs the swap to strategy_swaps table for auditability.
    """
    conn = sqlite3.connect(db_path)

    # Ensure swap log table exists
    conn.execute("""
        CREATE TABLE IF NOT EXISTS strategy_swaps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            portfolio_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            old_strategy TEXT,
            new_strategy TEXT NOT NULL,
            old_timeframe TEXT,
            new_timeframe TEXT NOT NULL,
            reason TEXT,
            regime TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    # Load current routes
    row = conn.execute(
        "SELECT strategy_routes_json FROM portfolios WHERE id = ?", (portfolio_id,)
    ).fetchone()
    if not row:
        conn.close()
        raise ValueError(f"Portfolio {portfolio_id} not found")

    routes = json.loads(row[0] or "{}")
    old = routes.get(symbol, {})
    old_strategy = old.get("strategy")
    old_timeframe = old.get("timeframe", "1d")

    if old_strategy == new_strategy:
        conn.close()
        return {"swapped": False, "reason": "same strategy"}

    # Get params for new strategy
    cls = STRATEGY_REGISTRY.get(new_strategy)
    params = cls().get_parameters() if cls else {}

    routes[symbol] = {
        "strategy": new_strategy,
        "timeframe": timeframe,
        "params": params if params else {},
    }

    conn.execute(
        "UPDATE portfolios SET strategy_routes_json = ?, updated_at = datetime('now') WHERE id = ?",
        (json.dumps(routes), portfolio_id),
    )

    # Log the swap
    conn.execute("""
        INSERT INTO strategy_swaps
        (portfolio_id, symbol, old_strategy, new_strategy, old_timeframe, new_timeframe, reason)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (portfolio_id, symbol, old_strategy, new_strategy, old_timeframe, timeframe, reason))

    conn.commit()
    conn.close()

    logger.info(f"Swapped {symbol}: {old_strategy} → {new_strategy} ({reason})")
    return {
        "swapped": True,
        "symbol": symbol,
        "from": old_strategy,
        "to": new_strategy,
        "reason": reason,
    }


def enroll_symbol(
    symbol: str,
    portfolio_id: int = 1,
    strategy: str = None,
    timeframe: str = "4h",
    reason: str = "auto-enrolled on first buy",
    db_path: str = DEFAULT_DB,
) -> dict:
    """Add a symbol to a portfolio's tracked universe and assign a strategy route.

    Called automatically when the LLM agent or any other component buys a symbol
    that isn't yet in the signal engine's monitored list.  Idempotent: safe to
    call multiple times for the same symbol.

    Strategy selection order:
      1. Explicit ``strategy`` argument (if provided).
      2. Catalogue lookup via regime detection (best Sharpe for current regime).
      3. Fallback to RegimeAware (adapts to any regime).

    Returns dict with ``enrolled`` bool and details.
    """
    conn = sqlite3.connect(db_path)

    row = conn.execute(
        "SELECT symbols_json, strategy_routes_json FROM portfolios WHERE id = ?",
        (portfolio_id,),
    ).fetchone()
    if not row:
        conn.close()
        return {"enrolled": False, "reason": f"portfolio {portfolio_id} not found"}

    symbols = json.loads(row[0] or "[]")
    routes = json.loads(row[1] or "{}")

    already_in_symbols = symbol in symbols
    already_in_routes = symbol in routes

    if already_in_symbols and already_in_routes:
        conn.close()
        return {"enrolled": False, "reason": "already enrolled"}

    # ── Strategy selection ────────────────────────────────────────────
    chosen = strategy
    regime = "unknown"

    if not chosen:
        try:
            from core.regime_monitor import (
                best_strategy_for_regime,
                detect_regime,
            )

            result = detect_regime(symbol, db_path)
            regime = result["regime"]
            best = best_strategy_for_regime(symbol, regime, db_path)
            if best and best["strategy_name"] in STRATEGY_REGISTRY:
                chosen = best["strategy_name"]
                logger.info(
                    f"[enroll] {symbol}: catalogue hit → {chosen} "
                    f"(regime={regime}, sharpe={best['sharpe_ratio']:.2f})"
                )
            elif best:
                logger.debug(
                    f"[enroll] {symbol}: catalogue suggested {best['strategy_name']} "
                    f"but it's not in live registry — skipping"
                )
        except Exception as e:
            logger.debug(f"[enroll] Regime/catalogue lookup failed for {symbol}: {e}")

    if not chosen:
        chosen = "RegimeAware"
        logger.info(f"[enroll] {symbol}: no catalogue data → defaulting to RegimeAware")

    # Resolve strategy params
    cls = STRATEGY_REGISTRY.get(chosen)
    params = cls().get_parameters() if cls else {}

    # ── Update DB ─────────────────────────────────────────────────────
    if not already_in_symbols:
        symbols.append(symbol)
        symbols.sort()

    if not already_in_routes:
        routes[symbol] = {
            "strategy": chosen,
            "timeframe": timeframe,
            "params": params if params else {},
        }

    try:
        conn.execute(
            "UPDATE portfolios SET symbols_json = ?, strategy_routes_json = ?, "
            "updated_at = datetime('now') WHERE id = ?",
            (json.dumps(symbols), json.dumps(routes), portfolio_id),
        )

        # Audit trail in strategy_swaps
        conn.execute(
            """CREATE TABLE IF NOT EXISTS strategy_swaps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                portfolio_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                old_strategy TEXT,
                new_strategy TEXT NOT NULL,
                old_timeframe TEXT,
                new_timeframe TEXT NOT NULL,
                reason TEXT,
                regime TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )"""
        )
        conn.execute(
            "INSERT INTO strategy_swaps "
            "(portfolio_id, symbol, old_strategy, new_strategy, old_timeframe, new_timeframe, reason, regime) "
            "VALUES (?, ?, NULL, ?, NULL, ?, ?, ?)",
            (portfolio_id, symbol, chosen, timeframe, reason, regime),
        )
        conn.commit()
    except Exception as e:
        conn.close()
        logger.error(f"[enroll] DB update failed for {symbol}: {e}")
        return {"enrolled": False, "reason": str(e)}

    conn.close()
    logger.info(f"Enrolled {symbol} → {chosen}/{timeframe} (regime={regime})")
    return {
        "enrolled": True,
        "symbol": symbol,
        "strategy": chosen,
        "timeframe": timeframe,
        "regime": regime,
        "params": params,
    }


def get_swap_history(
    portfolio_id: int = 1,
    limit: int = 20,
    db_path: str = DEFAULT_DB,
) -> List[dict]:
    """Get recent strategy swap history."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT * FROM strategy_swaps
        WHERE portfolio_id = ?
        ORDER BY created_at DESC
        LIMIT ?
    """, (portfolio_id, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]
