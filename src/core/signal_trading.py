#!/usr/bin/env python3
"""
Signal-based paper trading.
Extends signal alerts with automated paper trading based on technical signals.
"""

import os
import sys
import sqlite3
import logging
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Import paper trading module
try:
    from core.paper_trading import PaperTradingPortfolio

    PAPER_TRADING_AVAILABLE = True
except ImportError as e:
    logger.warning(f"Paper trading module not available: {e}")
    PAPER_TRADING_AVAILABLE = False

# Import risk manager
try:
    from core.risk_manager import RiskManager

    RISK_MANAGER_AVAILABLE = True
except ImportError:
    RISK_MANAGER_AVAILABLE = False

# Import correlation tracker for regime-adjusted signals
try:
    from data.correlation_tracker import CorrelationTracker

    CORRELATION_AVAILABLE = True
except ImportError:
    CORRELATION_AVAILABLE = False

# Import Polymarket signal generator
try:
    from polymarket_signals import PolymarketSignalGenerator

    POLYMARKET_SIGNALS_AVAILABLE = True
except ImportError:
    POLYMARKET_SIGNALS_AVAILABLE = False

try:
    from config import (
        PORTFOLIO_SYMBOLS as CONFIG_PORTFOLIO_SYMBOLS,
        DB_PATH,
        get_active_portfolios,
        get_asset_class_profile,
    )
except ImportError:
    CONFIG_PORTFOLIO_SYMBOLS = ["BTC-USD", "ETH-USD", "BNB-USD", "GRT-USD"]
    DB_PATH = os.path.normpath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "crypto_data.db")
    )
    get_active_portfolios = None
    get_asset_class_profile = None

# Import backtested strategies — prefer modular backtest package, fall back to monolith
try:
    from backtest.strategies import STRATEGY_REGISTRY as _BACKTEST_REGISTRY

    BACKTESTED_STRATEGIES_AVAILABLE = True
except ImportError:
    try:
        from backtester import (
            BollingerReversionStrategy,
            MultiSignalStrategy,
            ScoreBasedStrategy,
        )

        _BACKTEST_REGISTRY = {
            "BollingerReversion": BollingerReversionStrategy,
            "MultiSignal": MultiSignalStrategy,
            "ScoreBased": ScoreBasedStrategy,
            "ScoreBasedRelaxed": ScoreBasedStrategy,
        }
        BACKTESTED_STRATEGIES_AVAILABLE = True
    except ImportError:
        _BACKTEST_REGISTRY = {}
        BACKTESTED_STRATEGIES_AVAILABLE = False

# Strategy ID cache for trade attribution
_STRATEGY_ID_CACHE: Dict[str, int] = {}


def _resolve_strategy_id(name: str) -> Optional[int]:
    """Look up strategy_registry.id, cached for the process lifetime."""
    if name in _STRATEGY_ID_CACHE:
        return _STRATEGY_ID_CACHE[name]
    try:
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute("SELECT id FROM strategy_registry WHERE name = ?", (name,)).fetchone()
        conn.close()
        if row:
            _STRATEGY_ID_CACHE[name] = row[0]
            return row[0]
    except Exception:
        pass
    return None


import pandas as pd
import numpy as np
from data.indicator_calculator import calculate_all_indicators

# Import strategy tracker for signal logging
try:
    from scoring.strategy_tracker import StrategyTracker

    STRATEGY_TRACKER_AVAILABLE = True
except ImportError:
    STRATEGY_TRACKER_AVAILABLE = False

# ── Strategy routing ─────────────────────────────────────────────────────
# Maps symbol → (strategy_instance, timeframe) based on backtest results.
# Symbols not listed here fall back to the original signal logic.


def build_strategy_routes(
    routes_config: dict = None,
    portfolio_symbols: list = None,
    default_timeframe: str = "1h",
):
    """Build the strategy routing table from portfolio config.

    Every symbol in portfolio_symbols gets a route. Symbols explicitly configured
    in routes_config use their assigned strategy; all others fall back to
    MultiSignal (the catch-all consensus strategy).

    This eliminates the legacy RSI/MACD fallback path — every symbol now runs
    through a validated backtested strategy.
    """
    routes = {}

    if BACKTESTED_STRATEGIES_AVAILABLE and routes_config:
        for symbol, cfg in routes_config.items():
            strategy_name = cfg["strategy"]
            cls = _BACKTEST_REGISTRY.get(strategy_name)
            if cls is None:
                logger.warning(
                    f"Unknown strategy '{strategy_name}' for {symbol} — "
                    f"available: {list(_BACKTEST_REGISTRY.keys())}"
                )
                continue
            params = cfg.get("params", {})
            try:
                instance = cls(**params) if params else cls()
            except TypeError:
                logger.info(f"Ignoring legacy params for {strategy_name}/{symbol}, using defaults")
                instance = cls()
            routes[symbol] = (instance, cfg["timeframe"])

    # Default routing: any symbol not explicitly configured gets MultiSignal.
    # This guarantees no symbol ever falls through to the retired legacy path.
    if BACKTESTED_STRATEGIES_AVAILABLE and portfolio_symbols:
        default_cls = _BACKTEST_REGISTRY.get("MultiSignal")
        if default_cls:
            for symbol in portfolio_symbols:
                if symbol not in routes:
                    routes[symbol] = (default_cls(), default_timeframe)
                    logger.debug(f"Default route: {symbol} → MultiSignal/{default_timeframe}")

    return routes


class SignalTradingSystem:
    """Signal detection with automated paper trading, supporting multiple portfolios."""

    def __init__(
        self,
        db_path: str = DB_PATH,
        test_mode: bool = False,
        portfolio_config: dict = None,
    ):
        """Initialize trading system.

        Args:
            db_path: Path to the database.
            test_mode: If True, generate signals but don't execute trades.
            portfolio_config: Dict from get_active_portfolios(). If None, uses
                legacy defaults (CONFIG_PORTFOLIO_SYMBOLS, hardcoded routes).
        """
        self.db_path = db_path
        self.test_mode = test_mode
        self.portfolio = None

        # Portfolio identity
        if portfolio_config:
            self.portfolio_id = portfolio_config["id"]
            self.portfolio_name = portfolio_config["name"]
            self.portfolio_mode = portfolio_config["mode"]
            self.PORTFOLIO_SYMBOLS = portfolio_config["symbols"]
            self.default_timeframe = portfolio_config.get("default_timeframe", "1h")
            routes_cfg = portfolio_config.get("strategy_routes", {})
            state_file = portfolio_config.get("state_file")
        else:
            self.portfolio_id = 1
            self.portfolio_name = "default"
            self.portfolio_mode = "paper"
            self.PORTFOLIO_SYMBOLS = CONFIG_PORTFOLIO_SYMBOLS
            self.default_timeframe = "1h"
            routes_cfg = None
            state_file = None

        self.trade_state_file = f"state/signal_trading_state_{self.portfolio_name}.json"

        # Run regime monitor to adapt routes before building them
        try:
            from core.regime_monitor import check_and_swap

            swaps = check_and_swap(portfolio_id=self.portfolio_id, db_path=self.db_path)
            if swaps:
                logger.info(f"[{self.portfolio_name}] Regime monitor: {len(swaps)} swaps applied")
                # Reload routes from DB after swaps
                conn = sqlite3.connect(self.db_path)
                fresh = conn.execute(
                    "SELECT strategy_routes_json FROM portfolios WHERE id = ?",
                    (self.portfolio_id,),
                ).fetchone()
                conn.close()
                if fresh and fresh[0]:
                    routes_cfg = json.loads(fresh[0])
        except Exception as e:
            logger.debug(f"Regime monitor skipped: {e}")

        # Build backtested strategy routes.
        # Every portfolio symbol gets a route; unrouted symbols default to MultiSignal.
        self.strategy_routes = build_strategy_routes(
            routes_cfg,
            portfolio_symbols=self.PORTFOLIO_SYMBOLS,
            default_timeframe=self.default_timeframe,
        )
        if self.strategy_routes:
            routed = ", ".join(f"{s}({tf})" for s, (_, tf) in self.strategy_routes.items())
            logger.info(f"[{self.portfolio_name}] Strategy routing: {routed}")

        # Strategy tracker for signal logging
        self.strategy_tracker = None
        if STRATEGY_TRACKER_AVAILABLE:
            try:
                self.strategy_tracker = StrategyTracker(
                    db_path=db_path, portfolio_id=self.portfolio_id
                )
                logger.info(f"[{self.portfolio_name}] Strategy tracker loaded")
            except Exception as e:
                logger.warning(f"Strategy tracker unavailable: {e}")

        if PAPER_TRADING_AVAILABLE:
            try:
                pf_kwargs = dict(
                    db_path=db_path,
                    initial_capital=1000.0,
                    portfolio_id=self.portfolio_id,
                )
                if state_file:
                    import os

                    pf_kwargs["state_file"] = os.path.join(
                        os.path.dirname(os.path.abspath(__file__)), state_file
                    )
                self.portfolio = PaperTradingPortfolio(**pf_kwargs)
                self.portfolio._current_trader_id = 3  # Signal Engine
                logger.info(f"[{self.portfolio_name}] Portfolio loaded")
            except Exception as e:
                logger.error(f"Failed to load portfolio: {e}")
                self.portfolio = None
        if test_mode:
            logger.info(f"[{self.portfolio_name}] Running in test mode")
        else:
            logger.warning(f"[{self.portfolio_name}] Paper trading unavailable")

        # Initialize risk manager
        self.risk_manager = None
        if RISK_MANAGER_AVAILABLE:
            try:
                self.risk_manager = RiskManager(db_path=db_path)
                logger.info("Risk manager loaded")
            except Exception as e:
                logger.warning(f"Risk manager unavailable: {e}")

        # Initialize correlation tracker
        self.correlation_tracker = None
        if CORRELATION_AVAILABLE:
            try:
                self.correlation_tracker = CorrelationTracker(db_path=db_path)
                logger.info("Correlation tracker loaded")
            except Exception as e:
                logger.warning(f"Correlation tracker unavailable: {e}")

        # Initialize exit overlay
        self.exit_overlay = None
        try:
            from core.exit_overlay import ExitOverlay

            self.exit_overlay = ExitOverlay(db_path=db_path)
            logger.info(f"[{self.portfolio_name}] Exit overlay loaded")
        except ImportError:
            logger.warning("Exit overlay not available")

        # Initialize Polymarket signal overlay
        self.polymarket_generator = None
        if POLYMARKET_SIGNALS_AVAILABLE:
            try:
                self.polymarket_generator = PolymarketSignalGenerator(
                    db_path=db_path,
                    min_probability_delta=0.05,
                )
                logger.info(f"[{self.portfolio_name}] Polymarket signal overlay loaded")
            except Exception as e:
                logger.warning(f"Polymarket signals unavailable: {e}")

        # Initialize LLM gatekeeper (validates BUY signals before execution)
        # Fail-open by design: if unavailable, signals pass through unchanged.
        self.gatekeeper = None
        try:
            from llm.llm_chain import LLMChain
            from llm.llm_gatekeeper import LLMGatekeeper

            _gk_chain = LLMChain(
                system_prompt=(
                    "You are Edoras Gate, a risk-aware signal validator for a crypto paper "
                    "portfolio. Review trading signals and decide APPROVE, REJECT, or MODIFY. "
                    "Always respond with valid JSON. Be concise."
                ),
                timeout=15,
                cache_ttl=300,
                max_tokens=1000,
                fallback_json={"decisions": []},
            )
            self.gatekeeper = LLMGatekeeper(chain=_gk_chain, timeout_passthrough=True)
            logger.info(
                f"[{self.portfolio_name}] LLM gatekeeper loaded "
                f"(providers: {', '.join(_gk_chain.available_providers())})"
            )
        except Exception as e:
            logger.warning(f"LLM gatekeeper unavailable — signals will pass through: {e}")

        self.load_trade_state()

    def load_trade_state(self):
        """Load previous trade state to avoid duplicates"""
        self.trade_state = {}
        try:
            if os.path.exists(self.trade_state_file):
                with open(self.trade_state_file, "r") as f:
                    self.trade_state = json.load(f)
        except Exception as e:
            logger.warning(f"Could not load trade state: {e}")
            self.trade_state = {}

    def save_trade_state(self):
        """Save current trade state"""
        try:
            os.makedirs("state", exist_ok=True)
            with open(self.trade_state_file, "w") as f:
                json.dump(self.trade_state, f, indent=2)
        except Exception as e:
            logger.warning(f"Could not save trade state: {e}")

    def get_indicator_window(
        self, symbol: str, timeframe: str, lookback: int = 60
    ) -> Optional[pd.DataFrame]:
        """
        Load the last `lookback` candles with indicators for a symbol/timeframe.
        Returns a DataFrame compatible with backtester strategy.generate_signals().
        """
        conn = sqlite3.connect(self.db_path)
        try:
            df = pd.read_sql_query(
                "SELECT c.timestamp, c.open, c.high, c.low, c.close, c.volume, "
                "i.rsi_14, i.macd_line, i.macd_signal, i.macd_histogram, "
                "i.sma_20, i.sma_50, i.sma_200, i.ema_12, i.ema_26, "
                "i.bb_upper, i.bb_middle, i.bb_lower, i.bb_width, "
                "i.atr_14, i.volume_sma_20, i.volume_ratio, i.adx_14 "
                "FROM candlesticks c "
                "JOIN indicators i ON c.symbol=i.symbol AND c.timeframe=i.timeframe AND c.timestamp=i.timestamp "
                "WHERE c.symbol=? AND c.timeframe=? "
                "ORDER BY c.timestamp DESC LIMIT ?",
                conn,
                params=(symbol, timeframe, lookback),
            )
        finally:
            conn.close()

        if df.empty or len(df) < 3:
            return None

        # Reverse to chronological order
        df = df.iloc[::-1].reset_index(drop=True)
        return df

    def run_backtested_strategy(self, symbol: str) -> Optional[Dict]:
        """
        Run the backtested strategy for a symbol (if routed).
        Returns a signal dict compatible with the existing pipeline, or None.
        """
        if symbol not in self.strategy_routes:
            return None

        strategy, timeframe = self.strategy_routes[symbol]
        # Fetch enough bars for the strategy's lookback requirement.
        # TSMOM_3M needs 64, TSMOM needs 253, most others need ~30.
        # 120 covers all 4h strategies with margin; data is cheap.
        df = self.get_indicator_window(symbol, timeframe, lookback=120)
        if df is None:
            logger.warning(f"Insufficient data for backtested strategy on {symbol}/{timeframe}")
            return None

        # Build portfolio context for the strategy
        position_qty = 0
        if self.portfolio and symbol in self.portfolio.positions:
            position_qty = self.portfolio.positions[symbol].get("quantity", 0)
        capital = self.portfolio.capital if self.portfolio else 1000.0

        portfolio_ctx = {
            "capital": capital,
            "position_qty": position_qty,
            "entry_price": 0,
            "symbol": symbol,
        }

        signals = strategy.generate_signals(df, portfolio_ctx)
        if not signals:
            return None

        # Convert backtester signal format to signal_trading format
        sig = signals[0]  # take strongest
        latest = df.iloc[-1]
        return {
            "symbol": symbol,
            "action": sig["action"],
            "strength": sig["weight"] * 100,  # convert 0-1 weight to 0-100 strength
            "reason": f"[{strategy.name}/{timeframe}] {sig['reason']}",
            "rsi": latest.get("rsi_14"),
            "macd_hist": latest.get("macd_histogram"),
            "timestamp": int(latest["timestamp"]),
        }

    def _get_polymarket_signals(self) -> List[Dict]:
        """Fetch Polymarket probability-shift signals mapped to crypto symbols.

        Returns a list of signal dicts compatible with the main signal pipeline.
        Each signal carries source='polymarket' and the originating PM symbol.
        """
        if not self.polymarket_generator:
            return []
        try:
            pm_signals = self.polymarket_generator.generate_signals()
        except Exception as e:
            logger.warning(f"Polymarket signal generation failed: {e}")
            return []
        if not pm_signals:
            return []

        # Only keep signals for symbols in this portfolio
        portfolio_symbols = set(self.PORTFOLIO_SYMBOLS)
        results = []
        for pm in pm_signals:
            if pm["symbol"] not in portfolio_symbols:
                continue
            results.append(
                {
                    "symbol": pm["symbol"],
                    "action": pm["action"],
                    "strength": pm["strength"],
                    "reason": pm["reason"],
                    "rsi": None,
                    "macd_hist": None,
                    "timestamp": int(datetime.now().timestamp()),
                    "_strategy_name": "polymarket_overlay",
                    "_timeframe": "event",
                    "_source": "polymarket",
                    "_pm_symbol": pm.get("polymarket_symbol"),
                    "_probability_delta": pm.get("probability_delta"),
                }
            )

        if results:
            logger.info(
                f"Polymarket overlay: {len(results)} signals "
                f"({', '.join(r['symbol'] + ' ' + r['action'] for r in results)})"
            )
        return results

    # ── Data freshness gate ───────────────────────────────────────────────

    # Maximum acceptable staleness per timeframe (seconds).
    # If the latest candle is older than this, the symbol is skipped.
    _FRESHNESS_THRESHOLDS = {
        "5m": 60 * 30,  # 30 minutes
        "1h": 3600 * 3,  # 3 hours
        "4h": 3600 * 10,  # 10 hours (slightly more than 2 4h periods)
        "1d": 3600 * 28,  # 28 hours (slightly more than 1 trading day)
    }

    def _is_data_fresh(self, symbol: str, timeframe: str) -> bool:
        """Return True if the latest candle for symbol/timeframe is within the
        acceptable staleness threshold.  Missing data is treated as stale."""
        threshold = self._FRESHNESS_THRESHOLDS.get(timeframe, 3600 * 6)
        try:
            conn = sqlite3.connect(self.db_path)
            row = conn.execute(
                "SELECT MAX(timestamp) FROM candlesticks WHERE symbol=? AND timeframe=?",
                (symbol, timeframe),
            ).fetchone()
            conn.close()
            if not row or row[0] is None:
                logger.warning(f"[freshness] No {timeframe} data for {symbol} — skipping")
                return False
            age = int(datetime.now().timestamp()) - row[0]
            if age > threshold:
                hours = age / 3600
                logger.warning(
                    f"[freshness] Stale {timeframe} data for {symbol}: "
                    f"{hours:.1f}h old (threshold {threshold / 3600:.0f}h) — skipping"
                )
                return False
            return True
        except Exception as e:
            logger.debug(f"[freshness] Check failed for {symbol}/{timeframe}: {e}")
            return True  # fail-open: don't block signals on DB errors

    def get_latest_indicators(self, symbol: str, timeframe: str = "1h"):
        """Get latest indicators for a symbol/timeframe"""
        conn = sqlite3.connect(self.db_path)
        query = """
        SELECT i.timestamp, i.rsi_14, i.macd_histogram, c.close, i.adx_14, i.volume_ratio, i.sma_20, i.sma_50
        FROM indicators i 
        JOIN candlesticks c ON i.symbol = c.symbol 
            AND i.timeframe = c.timeframe 
            AND i.timestamp = c.timestamp
        WHERE i.symbol = ? AND i.timeframe = ?
        ORDER BY i.timestamp DESC LIMIT 1
        """

        cursor = conn.cursor()
        cursor.execute(query, (symbol, timeframe))
        row = cursor.fetchone()
        conn.close()

        if row:
            return {
                "timestamp": row[0],
                "rsi": row[1],
                "macd_hist": row[2],
                "price": row[3],
                "adx": row[4],
                "volume_ratio": row[5],
                "sma_20": row[6],
                "sma_50": row[7],
            }
        return None

    def check_all_symbols(self, timeframe=None):
        """Check trading signals for all portfolio symbols, with risk checks first."""
        timeframe = timeframe or self.default_timeframe
        signals = []

        # ── Risk checks BEFORE generating buy signals ────────────────
        risk_exit_signals = []
        risk_report = None
        if self.risk_manager and self.portfolio:
            # Build position values map
            positions = {}
            for sym, pos in self.portfolio.positions.items():
                price = self.portfolio.get_current_price(sym)
                positions[sym] = price * pos["quantity"]
            portfolio_value = self.portfolio.get_portfolio_value()

            exit_sigs, cb, violations = self.risk_manager.run_all_checks(positions, portfolio_value)
            risk_exit_signals = exit_sigs

            if exit_sigs or cb or violations:
                risk_report = self.risk_manager.format_risk_report(exit_sigs, cb, violations)
                logger.info(f"Risk check: {len(exit_sigs)} exits, {len(violations)} violations")

            # If circuit breaker is active, skip all buy signals
            if self.risk_manager.circuit_breaker_active:
                logger.warning("SKIP ALL: circuit_breaker active — suppressing all buy signals")
                return [], risk_exit_signals, risk_report

        # ── Generate trading signals ─────────────────────────────────
        for symbol in self.PORTFOLIO_SYMBOLS:
            # ── Data freshness gate: skip symbol if data is stale ─────
            signal_tf = (
                self.strategy_routes[symbol][1] if symbol in self.strategy_routes else timeframe
            )
            if not self._is_data_fresh(symbol, signal_tf):
                continue

            # Try backtested strategy first (if symbol is routed)
            if symbol in self.strategy_routes:
                bt_signal = self.run_backtested_strategy(symbol)
                if bt_signal:
                    strat, tf = self.strategy_routes[symbol]
                    # Log signal to strategy tracker
                    if self.strategy_tracker:
                        signal_id = self.strategy_tracker.log_signal(
                            strategy_name=strat.name,
                            symbol=symbol,
                            timeframe=tf,
                            action=bt_signal["action"],
                            strength=bt_signal["strength"],
                            reason=bt_signal["reason"],
                            was_executed=False,  # updated after execution
                            adx=bt_signal.get("adx"),
                            rsi=bt_signal.get("rsi"),
                        )
                        bt_signal["_signal_id"] = signal_id
                        bt_signal["_strategy_name"] = strat.name
                        bt_signal["_timeframe"] = tf

                    if bt_signal["strength"] >= 35:
                        signals.append(bt_signal)
                        logger.info(
                            f"Backtested signal: {symbol} {bt_signal['action']} "
                            f"strength={bt_signal['strength']:.1f}"
                        )
                    else:
                        logger.info(
                            f"Routed strategy weak signal for {symbol}: "
                            f"{bt_signal['action']} strength={bt_signal['strength']:.1f} < 35 — skipping"
                        )
                else:
                    logger.info(f"Routed strategy silent for {symbol} — holding")

        # ── Exit overlay: check all held positions for exit conditions ──
        if self.portfolio and self.exit_overlay:
            try:
                exit_signals = self.exit_overlay.check_all_exits(
                    portfolio_positions=self.portfolio.positions,
                    db_path=self.db_path,
                )
                for exit_sig in exit_signals:
                    existing = next((s for s in signals if s["symbol"] == exit_sig["symbol"]), None)
                    if existing:
                        if existing["action"] == "SELL":
                            boost = min(exit_sig["strength"] * 0.3, 15)
                            existing["strength"] = min(existing["strength"] + boost, 100)
                            existing["reason"] += (
                                f" | exit overlay confirms ({exit_sig['exit_type']})"
                            )
                            logger.info(
                                f"Exit overlay confirms SELL {exit_sig['symbol']} "
                                f"(+{boost:.0f} boost from {exit_sig['exit_type']})"
                            )
                        elif existing["action"] == "BUY":
                            logger.warning(
                                f"Exit overlay conflicts with BUY for {exit_sig['symbol']} "
                                f"— exit wins ({exit_sig['exit_type']})"
                            )
                            signals.remove(existing)
                            signals.append(exit_sig)
                    else:
                        signals.append(exit_sig)
                        logger.info(
                            f"Exit overlay: SELL {exit_sig['symbol']} "
                            f"strength={exit_sig['strength']:.0f} ({exit_sig['exit_type']})"
                        )

                    # Log exit overlay signal to strategy tracker
                    if self.strategy_tracker and exit_sig in signals:
                        signal_id = self.strategy_tracker.log_signal(
                            strategy_name="exit_overlay",
                            symbol=exit_sig["symbol"],
                            timeframe=exit_sig.get("_timeframe", "4h"),
                            action="SELL",
                            strength=exit_sig["strength"],
                            reason=exit_sig["reason"],
                            was_executed=False,
                        )
                        exit_sig["_signal_id"] = signal_id

                if exit_signals:
                    logger.info(f"Exit overlay: {len(exit_signals)} exit signals generated")
            except Exception as e:
                logger.error(f"Exit overlay failed: {e}")

        # ── Auto-enroll held but unrouted symbols ──
        if self.portfolio and self.strategy_routes:
            held = set(
                sym for sym, pos in self.portfolio.positions.items() if pos.get("quantity", 0) > 0
            )
            routed = set(self.strategy_routes.keys())
            unrouted_held = held - routed
            if unrouted_held:
                try:
                    from backtest.deployer import enroll_symbol

                    for sym in sorted(unrouted_held):
                        result = enroll_symbol(
                            sym,
                            portfolio_id=self.portfolio_id,
                            db_path=self.db_path,
                            reason="auto-enrolled by signal engine (held but unrouted)",
                        )
                        if result.get("enrolled"):
                            logger.info(
                                f"Auto-enrolled unrouted {sym} → "
                                f"{result['strategy']}/{result['timeframe']}"
                            )
                            # Hot-patch so this run picks it up immediately
                            new_route_cfg = {
                                sym: {
                                    "strategy": result["strategy"],
                                    "timeframe": result["timeframe"],
                                    "params": result.get("params", {}),
                                }
                            }
                            new_routes = build_strategy_routes(
                                new_route_cfg,
                                [sym],
                                self.default_timeframe,
                            )
                            self.strategy_routes.update(new_routes)
                            if sym not in self.PORTFOLIO_SYMBOLS:
                                self.PORTFOLIO_SYMBOLS.append(sym)
                except Exception as e:
                    logger.warning(
                        f"Auto-enroll of unrouted symbols failed: {e} — "
                        f"held but unrouted: {', '.join(sorted(unrouted_held))}"
                    )

        # ── Polymarket overlay: boost or create signals from prediction markets ──
        pm_signals = self._get_polymarket_signals()
        if pm_signals:
            # Build lookup of existing signals by (symbol, action)
            existing = {}
            for i, sig in enumerate(signals):
                existing[(sig["symbol"], sig["action"])] = i

            for pm in pm_signals:
                key = (pm["symbol"], pm["action"])
                if key in existing:
                    # Agreement: boost the existing signal's strength
                    idx = existing[key]
                    boost = min(pm["strength"] * 0.25, 15)  # cap boost at +15
                    old_str = signals[idx]["strength"]
                    signals[idx]["strength"] = min(old_str + boost, 100)
                    signals[idx]["reason"] += f" | PM boost +{boost:.0f} ({pm['reason']})"
                    logger.info(
                        f"Polymarket boost: {pm['symbol']} {pm['action']} "
                        f"+{boost:.0f} ({old_str:.0f}→{signals[idx]['strength']:.0f})"
                    )
                else:
                    # New signal from Polymarket alone — cap at 65 (moderate conviction)
                    pm["strength"] = min(pm["strength"], 65)
                    # Log to strategy tracker
                    if self.strategy_tracker:
                        signal_id = self.strategy_tracker.log_signal(
                            strategy_name="polymarket_overlay",
                            symbol=pm["symbol"],
                            timeframe="event",
                            action=pm["action"],
                            strength=pm["strength"],
                            reason=pm["reason"],
                            was_executed=False,
                        )
                        pm["_signal_id"] = signal_id
                    if pm["strength"] >= 35:
                        signals.append(pm)
                        logger.info(
                            f"Polymarket new signal: {pm['symbol']} {pm['action']} "
                            f"strength={pm['strength']:.0f}"
                        )

        return signals, risk_exit_signals, risk_report

    def _record_skip(self, sig: Dict, reason: str):
        """Record a skip reason for a signal in the strategy_signals_log."""
        if self.strategy_tracker and "_signal_id" in sig:
            try:
                conn = sqlite3.connect(self.db_path)
                conn.execute(
                    "UPDATE strategy_signals_log SET skip_reason=? WHERE id=?",
                    (reason, sig["_signal_id"]),
                )
                conn.commit()
                conn.close()
            except Exception as e:
                logger.debug(f"Could not record skip reason: {e}")

    def execute_paper_trades(self, signals: List[Dict], risk_exits: List = None):
        """Execute paper trades based on signals and risk exits."""
        if not self.portfolio:
            logger.warning("No portfolio loaded — cannot execute trades")
            return

        executed = []

        # 1. Handle risk-triggered exits first (stop-loss, circuit breaker, etc.)
        if risk_exits:
            for exit_sig in risk_exits:
                symbol = exit_sig.symbol if hasattr(exit_sig, "symbol") else str(exit_sig)
                if symbol in self.portfolio.positions:
                    reason = exit_sig.reason if hasattr(exit_sig, "reason") else "risk trigger"
                    exit_type = (
                        exit_sig.exit_type if hasattr(exit_sig, "exit_type") else "risk_exit"
                    )
                    self.portfolio.set_trade_context(symbol, exit_reason=exit_type)
                    logger.info(f"Risk exit: selling {symbol} — {reason}")
                    self.portfolio.execute_sell_all(symbol)
                    executed.append(f"RISK_SELL {symbol}")

        # 2. Execute signal-driven trades
        portfolio_value = self.portfolio.get_portfolio_value()

        # Fetch market regime once for all signals
        _regime_label, _regime_vix = "unknown", None
        if self.correlation_tracker:
            try:
                _regime_label, _regime_vix = self.correlation_tracker.detect_regime()
            except Exception:
                pass

        for sig in signals:
            symbol = sig["symbol"]
            action = sig["action"]
            strength = sig["strength"]

            # Determine signal type from reason text
            reason = sig.get("reason", "")
            if "oversold" in reason or "overbought" in reason:
                sig_type = "mean_reversion"
            elif "Trend" in reason:
                sig_type = "trend_following"
            elif "Momentum" in reason or "momentum" in reason:
                sig_type = "momentum"
            else:
                sig_type = "unknown"

            self.portfolio.set_trade_context(
                symbol,
                signal_type=sig_type,
                signal_strength=strength,
                exit_reason="signal",
            )
            # Rich decision_context for signal engine trades
            import json as _json

            strategy_name = sig.get("_strategy_name")
            self.portfolio._pending_decision_context = _json.dumps(
                {
                    "signal_type": sig_type,
                    "signal_strength": round(strength, 2),
                    "action": action,
                    "reason": reason,
                    "strategy": strategy_name,
                    "timeframe": sig.get("_timeframe"),
                }
            )
            # Write strategy_id for warehouse attribution
            if strategy_name:
                sid = _resolve_strategy_id(strategy_name)
                if sid:
                    self.portfolio._pending_strategy_id = sid

            if action == "BUY":
                # Regime gate: require higher conviction in adverse regimes
                if _regime_label == "risk-off" and strength < 75:
                    logger.info(
                        f"SKIP BUY {symbol}: regime_gate risk-off "
                        f"(strength {strength:.1f} < 75, VIX={_regime_vix})"
                    )
                    self._record_skip(sig, f"regime_gate_risk_off (str={strength:.1f}<75)")
                    continue
                if _regime_label == "neutral" and strength < 60:
                    logger.info(
                        f"SKIP BUY {symbol}: regime_gate neutral "
                        f"(strength {strength:.1f} < 60, VIX={_regime_vix})"
                    )
                    self._record_skip(sig, f"regime_gate_neutral (str={strength:.1f}<60)")
                    continue

                # Check if we already hold this symbol
                if symbol in self.portfolio.positions:
                    # Allow adding to positions for high-conviction signals (strength >= 80)
                    _prof_check = get_asset_class_profile(symbol) if get_asset_class_profile else {}
                    max_pct = _prof_check.get("max_position_pct", 0.25)
                    current_pos_value = self.portfolio.get_position_value(symbol)
                    current_pct = (
                        current_pos_value / portfolio_value if portfolio_value > 0 else 1.0
                    )
                    if strength < 80 or current_pct >= max_pct * 0.9:
                        pos_qty = self.portfolio.positions[symbol].get("quantity", 0)
                        skip_reason = (
                            f"position_held (qty={pos_qty:.6g}, "
                            f"alloc={current_pct:.1%}/{max_pct:.0%})"
                        )
                        if strength >= 80:
                            skip_reason += " — near max allocation"
                        logger.info(f"SKIP BUY {symbol}: {skip_reason}")
                        self._record_skip(sig, skip_reason)
                        continue
                    else:
                        logger.info(
                            f"Adding to {symbol}: high conviction (str={strength:.0f}), "
                            f"current alloc={current_pct:.1%} < {max_pct:.0%}"
                        )

                # Dedup: skip if same symbol was bought in the last 60 seconds
                last_buy_time = self.trade_state.get(f"last_buy_{symbol}")
                if last_buy_time:
                    try:
                        elapsed = (
                            datetime.now() - datetime.fromisoformat(last_buy_time)
                        ).total_seconds()
                        if elapsed < 60:
                            logger.info(
                                f"SKIP BUY {symbol}: dedup_window ({elapsed:.0f}s since last buy)"
                            )
                            self._record_skip(sig, f"dedup_window ({elapsed:.0f}s)")
                            continue
                    except Exception:
                        pass

                # Position size: scale with signal strength
                # strength < 50  → skip (too weak after fixes)
                # strength 50-65 → 3-5% of portfolio (low conviction)
                # strength 65-80 → 5-10% of portfolio (moderate)
                # strength 80+   → 10-15% of portfolio (high conviction)
                if strength < 50:
                    logger.info(f"SKIP BUY {symbol}: strength_too_low ({strength:.1f} < 50)")
                    self._record_skip(sig, f"strength_too_low ({strength:.1f})")
                    continue
                if strength >= 80:
                    alloc_pct = 0.10 + min((strength - 80) / 200, 0.05)
                elif strength >= 65:
                    alloc_pct = 0.05 + (strength - 65) / 300
                else:
                    alloc_pct = 0.03 + (strength - 50) / 750

                # Regime-adjusted sizing: smaller positions in adverse regimes
                if _regime_label == "risk-off":
                    alloc_pct *= 0.5
                    logger.info(
                        f"  {symbol}: risk-off regime → halved allocation to {alloc_pct:.1%}"
                    )
                elif _regime_label == "neutral":
                    alloc_pct *= 0.7
                    logger.info(
                        f"  {symbol}: neutral regime → reduced allocation to {alloc_pct:.1%}"
                    )

                # Cap at per-asset-class position limit
                _prof = get_asset_class_profile(symbol) if get_asset_class_profile else {}
                max_pct = _prof.get("max_position_pct", 0.25)
                alloc_pct = min(alloc_pct, max_pct)

                # If adding to existing position, reduce alloc to fill up to max
                if symbol in self.portfolio.positions:
                    current_pos_value = self.portfolio.get_position_value(symbol)
                    current_pct = current_pos_value / portfolio_value if portfolio_value > 0 else 0
                    remaining_pct = max_pct - current_pct
                    alloc_pct = min(alloc_pct, remaining_pct)

                buy_amount = portfolio_value * alloc_pct
                buy_amount = min(buy_amount, self.portfolio.capital * 0.95)  # keep 5% cash reserve

                min_trade = _prof.get("min_trade_usd", 10.0)
                if buy_amount < min_trade:
                    logger.info(
                        f"SKIP BUY {symbol}: insufficient_cash "
                        f"(${buy_amount:.2f} < min ${min_trade:.0f})"
                    )
                    self._record_skip(sig, f"insufficient_cash (${buy_amount:.2f})")
                    continue

                logger.info(
                    f"Signal BUY {symbol}: strength={strength:.1f}, amount=${buy_amount:.2f}"
                )
                if self.portfolio.execute_buy(symbol, buy_amount):
                    executed.append(f"BUY {symbol} ${buy_amount:.2f}")
                    self.trade_state[f"last_buy_{symbol}"] = datetime.now().isoformat()
                    # Mark signal as executed in tracker
                    if self.strategy_tracker and "_signal_id" in sig:
                        price = self.portfolio.get_current_price(symbol)
                        self.strategy_tracker.mark_signal_executed(sig["_signal_id"], price)

            elif action == "SELL":
                if symbol not in self.portfolio.positions:
                    logger.info(f"SKIP SELL {symbol}: no_position_to_sell")
                    self._record_skip(sig, "no_position_to_sell")
                    continue

                # Minimum holding period (asset-class-aware, prevents fee-destroying churn)
                # Risk-driven exits (stop-loss, circuit breaker) bypass this
                _sell_prof = get_asset_class_profile(symbol) if get_asset_class_profile else {}
                min_hold = _sell_prof.get("min_hold_hours", 12)
                entry_date_str = self.portfolio.entry_prices.get(f"{symbol}_date")
                if entry_date_str and sig.get("_strategy_name", "") != "risk_exit":
                    try:
                        entry_dt = datetime.fromisoformat(entry_date_str)
                        held_hours = (datetime.now() - entry_dt).total_seconds() / 3600
                        if held_hours < min_hold:
                            logger.info(
                                f"SKIP SELL {symbol}: min_hold_period "
                                f"(held {held_hours:.1f}h < {min_hold}h)"
                            )
                            self._record_skip(sig, f"min_hold_period ({held_hours:.1f}h)")
                            continue
                    except Exception:
                        pass

                # Sell sizing: strong signals sell more
                if strength >= 70:
                    sell_pct = 1.0  # sell all
                elif strength >= 50:
                    sell_pct = 0.5  # sell half
                else:
                    sell_pct = 0.33  # sell a third

                logger.info(
                    f"Signal SELL {symbol}: strength={strength:.1f}, pct={sell_pct * 100:.0f}%"
                )
                entry_price = self.portfolio.positions.get(symbol, {}).get("avg_price", 0)
                exit_price = self.portfolio.get_current_price(symbol)
                if sell_pct >= 1.0:
                    self.portfolio.execute_sell_all(symbol)
                else:
                    self.portfolio.execute_partial_sell(symbol, sell_pct)
                executed.append(f"SELL {symbol} {sell_pct * 100:.0f}%")
                # Update signal outcome in tracker
                if self.strategy_tracker and "_signal_id" in sig and entry_price > 0:
                    outcome_pct = (exit_price - entry_price) / entry_price
                    self.strategy_tracker.update_signal_outcome(
                        sig["_signal_id"], outcome_pct, exit_price, "signal_sell"
                    )

        if executed:
            logger.info(f"Executed {len(executed)} paper trades: {', '.join(executed)}")
            # Save updated trade state
            self.trade_state["last_trade_time"] = datetime.now().isoformat()
            self.trade_state["last_trades"] = executed
            self.save_trade_state()
        else:
            logger.info("No trades executed this cycle")

        return executed


def run_portfolio(portfolio_config: dict = None, test_mode: bool = False):
    """Run signal check + trade execution for a single portfolio."""
    name = portfolio_config["name"] if portfolio_config else "default"
    trading_system = SignalTradingSystem(test_mode=test_mode, portfolio_config=portfolio_config)

    result = trading_system.check_all_symbols()
    if isinstance(result, tuple):
        signals, risk_exits, risk_report = result
    else:
        signals, risk_exits, risk_report = result, [], None

    print(f"\n{'=' * 60}")
    print(f"  {name} ({trading_system.portfolio_mode})")
    print(f"{'=' * 60}")

    if risk_report:
        print(risk_report)

    if risk_exits:
        print(f"\n⚠️  {len(risk_exits)} risk-triggered exits:")
        for ex in risk_exits:
            label = ex.label if hasattr(ex, "label") else str(ex)
            reason = ex.reason if hasattr(ex, "reason") else ""
            print(f"  {label}: {reason}")

    if not signals and not risk_exits:
        print("No actionable signals.")
        return

    if signals:
        print(f"\n{len(signals)} actionable signals:")
        for sig in signals:
            print(f"  {sig['symbol']}: {sig['action']} (strength {sig['strength']:.1f})")
            print(f"    {sig['reason']}")

    if not test_mode:
        # ── LLM Gatekeeper: validate BUY signals before execution ──────────
        # SELL signals (exits, overlays) bypass the gate — they must execute.
        if trading_system.gatekeeper and signals:
            buy_signals = [s for s in signals if s.get("action", "").upper() == "BUY"]
            sell_signals = [s for s in signals if s.get("action", "").upper() != "BUY"]
            if buy_signals:
                # Build portfolio state snapshot for the gatekeeper
                _pf_state: dict = {}
                if trading_system.portfolio:
                    _pf_val = trading_system.portfolio.get_portfolio_value()
                    _pf_cash = trading_system.portfolio.capital
                    _pf_positions = {
                        sym: {
                            "value": round(
                                trading_system.portfolio.get_current_price(sym) * pos["quantity"],
                                2,
                            ),
                            "pnl_pct": round(
                                (
                                    trading_system.portfolio.get_current_price(sym)
                                    / pos.get("avg_price", 1)
                                    - 1
                                )
                                * 100,
                                2,
                            )
                            if pos.get("avg_price")
                            else 0,
                        }
                        for sym, pos in trading_system.portfolio.positions.items()
                    }
                    _pf_state = {
                        "value": _pf_val,
                        "cash": _pf_cash,
                        "positions": _pf_positions,
                    }

                # Detect regime
                _regime = "unknown"
                if trading_system.correlation_tracker:
                    try:
                        _regime, _ = trading_system.correlation_tracker.detect_regime()
                    except Exception:
                        pass

                validated_buys = trading_system.gatekeeper.validate_signals(
                    buy_signals, _pf_state, _regime
                )
                signals = sell_signals + validated_buys

                gk_rejected = len(buy_signals) - len(validated_buys)
                if gk_rejected:
                    print(f"\n  Gatekeeper rejected {gk_rejected} BUY signal(s)")

        executed = trading_system.execute_paper_trades(signals, risk_exits)
        if executed:
            print(f"\n✅ Executed {len(executed)} trades:")
            for t in executed:
                print(f"  {t}")
        elif signals:
            print("\nNo trades executed (positions already held or insufficient capital)")
        if trading_system.portfolio:
            trading_system.portfolio.save_daily_snapshot()
    else:
        print("(test mode — no trades)")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Signal Trading System")
    parser.add_argument("--check", action="store_true", help="Check signals and execute trades")
    parser.add_argument(
        "--test", action="store_true", help="Dry run: check signals but do not trade"
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="(Alias for --check, kept for backwards compat)",
    )
    parser.add_argument(
        "--portfolio",
        type=str,
        default=None,
        help="Run only this portfolio (by name). Default: all active.",
    )
    args = parser.parse_args()

    test_mode = args.test and not (args.check or args.execute)

    # Load active portfolios from DB
    portfolios = get_active_portfolios() if get_active_portfolios else []

    if args.portfolio:
        portfolios = [p for p in portfolios if p["name"].lower() == args.portfolio.lower()]
        if not portfolios:
            print(f"Portfolio '{args.portfolio}' not found or not active.")
            sys.exit(1)

    if not portfolios:
        # Fallback: legacy single-portfolio mode
        run_portfolio(portfolio_config=None, test_mode=test_mode)
    else:
        for pf in portfolios:
            try:
                run_portfolio(portfolio_config=pf, test_mode=test_mode)
            except Exception as e:
                logger.error(f"[{pf['name']}] Failed: {e}")
                import traceback

                traceback.print_exc()
