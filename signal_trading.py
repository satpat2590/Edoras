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
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# Import paper trading module
try:
    from paper_trading import PaperTradingPortfolio
    PAPER_TRADING_AVAILABLE = True
except ImportError as e:
    logger.warning(f"Paper trading module not available: {e}")
    PAPER_TRADING_AVAILABLE = False

# Import risk manager
try:
    from risk_manager import RiskManager
    RISK_MANAGER_AVAILABLE = True
except ImportError:
    RISK_MANAGER_AVAILABLE = False

# Import correlation tracker for regime-adjusted signals
try:
    from correlation_tracker import CorrelationTracker
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
    from config import PORTFOLIO_SYMBOLS as CONFIG_PORTFOLIO_SYMBOLS, DB_PATH, get_active_portfolios, get_asset_class_profile
except ImportError:
    CONFIG_PORTFOLIO_SYMBOLS = ["BTC-USD", "ETH-USD", "BNB-USD", "GRT-USD"]
    DB_PATH = "crypto_data.db"
    get_active_portfolios = None
    get_asset_class_profile = None

# Import backtested strategies — prefer modular backtest package, fall back to monolith
try:
    from backtest.strategies import STRATEGY_REGISTRY as _BACKTEST_REGISTRY
    BACKTESTED_STRATEGIES_AVAILABLE = True
except ImportError:
    try:
        from backtester import BollingerReversionStrategy, MultiSignalStrategy, ScoreBasedStrategy
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
from indicator_calculator import calculate_all_indicators

# Import strategy tracker for signal logging
try:
    from strategy_tracker import StrategyTracker
    STRATEGY_TRACKER_AVAILABLE = True
except ImportError:
    STRATEGY_TRACKER_AVAILABLE = False

# ── Strategy routing ─────────────────────────────────────────────────────
# Maps symbol → (strategy_instance, timeframe) based on backtest results.
# Symbols not listed here fall back to the original signal logic.

def build_strategy_routes(routes_config: dict = None):
    """Build the strategy routing table from portfolio config.

    Looks up strategy classes from the backtest STRATEGY_REGISTRY,
    which covers all 13 strategies (original + TSMOM, PairsTrading, RegimeAware).
    """
    if not BACKTESTED_STRATEGIES_AVAILABLE or not routes_config:
        return {}
    routes = {}
    for symbol, cfg in routes_config.items():
        strategy_name = cfg["strategy"]
        cls = _BACKTEST_REGISTRY.get(strategy_name)
        if cls is None:
            logger.warning(f"Unknown strategy '{strategy_name}' for {symbol} — "
                           f"available: {list(_BACKTEST_REGISTRY.keys())}")
            continue
        params = cfg.get("params", {})
        try:
            instance = cls(**params) if params else cls()
        except TypeError:
            # Params don't match the new class signature — use defaults
            logger.info(f"Ignoring legacy params for {strategy_name}/{symbol}, using defaults")
            instance = cls()
        routes[symbol] = (instance, cfg["timeframe"])
    return routes


class SignalTradingSystem:
    """Signal detection with automated paper trading, supporting multiple portfolios."""

    def __init__(self, db_path: str = "crypto_data.db", test_mode: bool = False,
                 portfolio_config: dict = None):
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

        self.trade_state_file = f"signal_trading_state_{self.portfolio_name}.json"

        # Run regime monitor to adapt routes before building them
        try:
            from regime_monitor import check_and_swap
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

        # Build backtested strategy routes
        self.strategy_routes = build_strategy_routes(routes_cfg)
        if self.strategy_routes:
            routed = ", ".join(f"{s}({tf})" for s, (_, tf) in self.strategy_routes.items())
            logger.info(f"[{self.portfolio_name}] Strategy routing: {routed}")

        # Strategy tracker for signal logging
        self.strategy_tracker = None
        if STRATEGY_TRACKER_AVAILABLE:
            try:
                self.strategy_tracker = StrategyTracker(db_path=db_path, portfolio_id=self.portfolio_id)
                logger.info(f"[{self.portfolio_name}] Strategy tracker loaded")
            except Exception as e:
                logger.warning(f"Strategy tracker unavailable: {e}")

        if PAPER_TRADING_AVAILABLE and not test_mode:
            try:
                pf_kwargs = dict(db_path=db_path, initial_capital=1000.0, portfolio_id=self.portfolio_id)
                if state_file:
                    import os
                    pf_kwargs["state_file"] = os.path.join(os.path.dirname(os.path.abspath(__file__)), state_file)
                self.portfolio = PaperTradingPortfolio(**pf_kwargs)
                self.portfolio._current_trader_id = 3  # Signal Engine
                logger.info(f"[{self.portfolio_name}] Portfolio loaded")
            except Exception as e:
                logger.error(f"Failed to load portfolio: {e}")
                self.portfolio = None
        elif test_mode:
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

        self.load_trade_state()
    
    def load_trade_state(self):
        """Load previous trade state to avoid duplicates"""
        self.trade_state = {}
        try:
            if os.path.exists(self.trade_state_file):
                with open(self.trade_state_file, 'r') as f:
                    self.trade_state = json.load(f)
        except Exception as e:
            logger.warning(f"Could not load trade state: {e}")
            self.trade_state = {}
    
    def save_trade_state(self):
        """Save current trade state"""
        try:
            with open(self.trade_state_file, 'w') as f:
                json.dump(self.trade_state, f, indent=2)
        except Exception as e:
            logger.warning(f"Could not save trade state: {e}")
    
    def get_indicator_window(self, symbol: str, timeframe: str, lookback: int = 60) -> Optional[pd.DataFrame]:
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
            position_qty = self.portfolio.positions[symbol].get('quantity', 0)
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
            results.append({
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
            })

        if results:
            logger.info(f"Polymarket overlay: {len(results)} signals "
                        f"({', '.join(r['symbol'] + ' ' + r['action'] for r in results)})")
        return results

    def get_latest_indicators(self, symbol: str, timeframe: str = '1h'):
        """Get latest indicators for a symbol/timeframe"""
        conn = sqlite3.connect(self.db_path)
        query = '''
        SELECT i.timestamp, i.rsi_14, i.macd_histogram, c.close, i.adx_14, i.volume_ratio, i.sma_20, i.sma_50
        FROM indicators i 
        JOIN candlesticks c ON i.symbol = c.symbol 
            AND i.timeframe = c.timeframe 
            AND i.timestamp = c.timestamp
        WHERE i.symbol = ? AND i.timeframe = ?
        ORDER BY i.timestamp DESC LIMIT 1
        '''
        
        cursor = conn.cursor()
        cursor.execute(query, (symbol, timeframe))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return {
                'timestamp': row[0],
                'rsi': row[1],
                'macd_hist': row[2],
                'price': row[3],
                'adx': row[4],
                'volume_ratio': row[5],
                'sma_20': row[6],
                'sma_50': row[7]
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
                positions[sym] = price * pos['quantity']
            portfolio_value = self.portfolio.get_portfolio_value()

            exit_sigs, cb, violations = self.risk_manager.run_all_checks(positions, portfolio_value)
            risk_exit_signals = exit_sigs

            if exit_sigs or cb or violations:
                risk_report = self.risk_manager.format_risk_report(exit_sigs, cb, violations)
                logger.info(f"Risk check: {len(exit_sigs)} exits, {len(violations)} violations")

            # If circuit breaker is active, skip all buy signals
            if self.risk_manager.circuit_breaker_active:
                logger.warning("Circuit breaker active — suppressing all buy signals")
                return [], risk_exit_signals, risk_report

        # ── Generate trading signals ─────────────────────────────────
        for symbol in self.PORTFOLIO_SYMBOLS:
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
                            action=bt_signal['action'],
                            strength=bt_signal['strength'],
                            reason=bt_signal['reason'],
                            was_executed=False,  # updated after execution
                            adx=bt_signal.get('adx'),
                            rsi=bt_signal.get('rsi'),
                        )
                        bt_signal['_signal_id'] = signal_id
                        bt_signal['_strategy_name'] = strat.name
                        bt_signal['_timeframe'] = tf

                    if bt_signal['strength'] >= 35:
                        signals.append(bt_signal)
                        logger.info(f"Backtested signal: {symbol} {bt_signal['action']} "
                                    f"strength={bt_signal['strength']:.1f}")
                    else:
                        logger.info(f"Routed strategy weak signal for {symbol}: "
                                    f"{bt_signal['action']} strength={bt_signal['strength']:.1f} < 35 — skipping")
                else:
                    logger.info(f"Routed strategy silent for {symbol} — holding (no legacy fallback)")
                continue  # routed symbol: strategy decides, never fall back to legacy

            # Legacy signal logic — only for symbols NOT routed to a backtested strategy
            indicators = self.get_latest_indicators(symbol, timeframe)
            if not indicators:
                logger.warning(f"No indicators for {symbol} {timeframe}")
                continue
            signal = self.check_trading_signals(symbol, indicators)
            if signal:
                enhanced = self.enhance_signal(signal, indicators)
                # Log legacy signal to strategy tracker
                if self.strategy_tracker:
                    sig_type = 'legacy'
                    reason = enhanced.get('reason', '')
                    if 'oversold' in reason or 'overbought' in reason:
                        sig_type = 'legacy_mean_reversion'
                    elif 'Trend' in reason:
                        sig_type = 'legacy_trend'
                    elif 'Momentum' in reason:
                        sig_type = 'legacy_momentum'
                    signal_id = self.strategy_tracker.log_signal(
                        strategy_name=sig_type,
                        symbol=symbol,
                        timeframe=timeframe,
                        action=enhanced['action'],
                        strength=enhanced['strength'],
                        reason=enhanced['reason'],
                        was_executed=False,
                        adx=indicators.get('adx'),
                        rsi=indicators.get('rsi'),
                    )
                    enhanced['_signal_id'] = signal_id
                    enhanced['_strategy_name'] = sig_type
                    enhanced['_timeframe'] = timeframe

                # Filter by strength threshold
                if enhanced['strength'] >= 35:
                    signals.append(enhanced)

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
                    logger.info(f"Polymarket boost: {pm['symbol']} {pm['action']} "
                                f"+{boost:.0f} ({old_str:.0f}→{signals[idx]['strength']:.0f})")
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
                        logger.info(f"Polymarket new signal: {pm['symbol']} {pm['action']} "
                                    f"strength={pm['strength']:.0f}")

        return signals, risk_exit_signals, risk_report

    def check_trading_signals(self, symbol: str, indicators: dict) -> Dict[str, any]:
        """
        Check for trading signals based on indicators.
        Returns dict with signal type, strength, and action recommendation.

        Signal types:
          1. Mean-reversion (original): RSI extremes + MACD confirmation
          2. Trend-following: Pullback in established uptrend / breakdown in downtrend
          3. Momentum breakout: Strong move with volume confirmation
        """
        rsi = indicators.get('rsi')
        macd_hist = indicators.get('macd_hist')
        price = indicators.get('price')
        sma_20 = indicators.get('sma_20')
        sma_50 = indicators.get('sma_50')
        adx = indicators.get('adx')
        volume_ratio = indicators.get('volume_ratio')

        if rsi is None or macd_hist is None:
            return None

        # Asset-class-aware RSI thresholds
        _p = get_asset_class_profile(symbol) if get_asset_class_profile else {}
        rsi_os = _p.get("rsi_oversold", 30)       # strong oversold
        rsi_ob = _p.get("rsi_overbought", 70)     # strong overbought
        rsi_wos = _p.get("rsi_weak_oversold", 35)  # weak oversold
        rsi_wob = _p.get("rsi_weak_overbought", 65) # weak overbought

        # Skip legacy RSI signals for binary-profile assets (prediction markets)
        if _p.get("indicator_profile") == "binary":
            return None

        # Convert SMA values safely
        try:
            sma_20_f = float(sma_20) if sma_20 is not None else None
            sma_50_f = float(sma_50) if sma_50 is not None else None
            price_f = float(price) if price is not None else None
            adx_f = float(adx) if adx is not None else None
            vol_ratio = float(volume_ratio) if volume_ratio is not None else 1.0
        except (ValueError, TypeError):
            sma_20_f = sma_50_f = price_f = adx_f = None
            vol_ratio = 1.0

        signal = {
            'symbol': symbol,
            'rsi': rsi,
            'macd_hist': macd_hist,
            'timestamp': indicators['timestamp'],
            'action': 'HOLD',
            'strength': 0,
            'reason': ''
        }

        # ── 1. MEAN-REVERSION SIGNALS (original logic) ──────────────────

        # Strong oversold BUY: RSI < oversold + MACD turning bullish
        if rsi < rsi_os and macd_hist > 0:
            signal['action'] = 'BUY'
            signal['strength'] = (rsi_os - rsi) * 3.33 + min(macd_hist * 100, 50)
            signal['reason'] = f'Strong oversold reversal: RSI={rsi:.1f} MACD={macd_hist:.4f}'

        # Strong overbought SELL: RSI > overbought + MACD turning bearish
        elif rsi > rsi_ob and macd_hist < 0:
            signal['action'] = 'SELL'
            signal['strength'] = (rsi - rsi_ob) * 3.33 + min(abs(macd_hist) * 100, 50)
            signal['reason'] = f'Strong overbought reversal: RSI={rsi:.1f} MACD={macd_hist:.4f}'

        # Weak oversold BUY
        elif rsi < rsi_wos and macd_hist > 0:
            signal['action'] = 'BUY'
            signal['strength'] = (rsi_wos - rsi) * 2.0 + min(macd_hist * 100, 30)
            signal['reason'] = f'Weak oversold: RSI={rsi:.1f} MACD={macd_hist:.4f}'

        # Weak overbought SELL
        elif rsi > rsi_wob and macd_hist < 0:
            signal['action'] = 'SELL'
            signal['strength'] = (rsi - rsi_wob) * 2.0 + min(abs(macd_hist) * 100, 30)
            signal['reason'] = f'Weak overbought: RSI={rsi:.1f} MACD={macd_hist:.4f}'

        # ── 2. TREND-FOLLOWING SIGNALS ───────────────────────────────────
        # Only fire if mean-reversion didn't match and we have SMA data.

        elif price_f and sma_20_f and sma_50_f:

            in_uptrend = (price_f > sma_50_f and sma_20_f > sma_50_f
                          and macd_hist > 0)
            in_downtrend = (price_f < sma_50_f and sma_20_f < sma_50_f
                            and macd_hist < 0)

            # Trend BUY: Pullback in uptrend — RSI cooled to 38-62
            # Crypto stays overbought longer, so 62 is still a valid entry
            if in_uptrend and 38 <= rsi <= 62:
                strength = 35 + max(0, (55 - rsi)) * 0.5  # stronger when deeper pullback
                if adx_f and adx_f > 20:
                    strength += 8
                signal['action'] = 'BUY'
                signal['strength'] = strength
                signal['reason'] = (f'Trend pullback BUY: RSI={rsi:.1f} '
                                    f'price>{("SMA50" if price_f > sma_50_f else "?")} '
                                    f'MACD={macd_hist:.4f}'
                                    + (f' ADX={adx_f:.0f}' if adx_f else ''))

            # Trend SELL: Rally in downtrend — RSI bounced to 40-62
            elif in_downtrend and 40 <= rsi <= 62:
                strength = 35 + (rsi - 40) * 0.5
                if adx_f and adx_f > 20:
                    strength += 8
                signal['action'] = 'SELL'
                signal['strength'] = strength
                signal['reason'] = (f'Trend breakdown SELL: RSI={rsi:.1f} '
                                    f'price<SMA50 MACD={macd_hist:.4f}'
                                    + (f' ADX={adx_f:.0f}' if adx_f else ''))

            # ── 3. MOMENTUM BREAKOUT SIGNALS ─────────────────────────────
            # Price breaking above SMA20 with volume + bullish MACD

            # Momentum BUY: Price above SMA20, MACD bullish, volume above average
            elif (price_f > sma_20_f and macd_hist > 0
                  and vol_ratio > 0.8 and 45 <= rsi <= 70):
                strength = 30 + min(vol_ratio * 10, 20) + min(macd_hist * 50, 15)
                signal['action'] = 'BUY'
                signal['strength'] = strength
                signal['reason'] = (f'Momentum breakout BUY: vol={vol_ratio:.1f}x '
                                    f'RSI={rsi:.1f} MACD={macd_hist:.4f}')

            # Momentum SELL: Price below SMA20, MACD bearish, volume above average
            elif (price_f < sma_20_f and macd_hist < 0
                  and vol_ratio > 0.8 and 30 <= rsi <= 55):
                strength = 30 + min(vol_ratio * 10, 20) + min(abs(macd_hist) * 50, 15)
                signal['action'] = 'SELL'
                signal['strength'] = strength
                signal['reason'] = (f'Momentum breakdown SELL: vol={vol_ratio:.1f}x '
                                    f'RSI={rsi:.1f} MACD={macd_hist:.4f}')

        return signal if signal['action'] != 'HOLD' else None
    
    def get_latest_sentiment(self, symbol: str, max_age_hours: int = 24):
        """Get latest sentiment score for symbol within max_age_hours"""
        conn = sqlite3.connect(self.db_path)
        cutoff = int(datetime.now().timestamp()) - max_age_hours * 3600
        query = 'SELECT score, confidence, summary, timestamp FROM sentiment_scores WHERE symbol = ? AND timestamp >= ? ORDER BY timestamp DESC LIMIT 1'
        cursor = conn.cursor()
        cursor.execute(query, (symbol, cutoff))
        row = cursor.fetchone()
        conn.close()
        if row:
            return {
                'score': row[0],
                'confidence': row[1],
                'summary': row[2],
                'timestamp': row[3]
            }
        return None

    def multi_timeframe_alignment(self, symbol: str):
        """
        Compute alignment score between 1h and 4h timeframes.
        Returns score 0‑1 where 1 indicates perfect alignment.
        """
        # Get indicators for both timeframes
        indicators_1h = self.get_latest_indicators(symbol, '1h')
        indicators_4h = self.get_latest_indicators(symbol, '4h')
        
        if not indicators_1h or not indicators_4h:
            return 0.5  # neutral if missing data
        
        score = 0.0
        max_score = 0.0
        details = []
        
        # 1. Price vs SMA20 alignment (bullish if price > SMA20)
        price_1h = indicators_1h.get('price')
        sma20_1h = indicators_1h.get('sma_20')
        price_4h = indicators_4h.get('price')
        sma20_4h = indicators_4h.get('sma_20')
        
        if price_1h is not None and sma20_1h is not None and price_4h is not None and sma20_4h is not None:
            bullish_1h = price_1h > sma20_1h
            bullish_4h = price_4h > sma20_4h
            if bullish_1h == bullish_4h:
                score += 0.25
                details.append('price>SMA20 aligned')
            max_score += 0.25
        else:
            max_score += 0.25  # missing data, treat as neutral
        
        # 2. SMA20 vs SMA50 alignment (trend)
        sma50_1h = indicators_1h.get('sma_50')
        sma50_4h = indicators_4h.get('sma_50')
        if sma20_1h is not None and sma50_1h is not None and sma20_4h is not None and sma50_4h is not None:
            trend_up_1h = sma20_1h > sma50_1h
            trend_up_4h = sma20_4h > sma50_4h
            if trend_up_1h == trend_up_4h:
                score += 0.25
                details.append('SMA20>SMA50 aligned')
            max_score += 0.25
        else:
            max_score += 0.25
        
        # 3. MACD histogram sign alignment
        macd_1h = indicators_1h.get('macd_hist')
        macd_4h = indicators_4h.get('macd_hist')
        if macd_1h is not None and macd_4h is not None:
            bullish_macd_1h = macd_1h > 0
            bullish_macd_4h = macd_4h > 0
            if bullish_macd_1h == bullish_macd_4h:
                score += 0.25
                details.append('MACD sign aligned')
            max_score += 0.25
        else:
            max_score += 0.25
        
        # 4. RSI zone alignment (oversold/neutral/overbought)
        rsi_1h = indicators_1h.get('rsi')
        rsi_4h = indicators_4h.get('rsi')
        if rsi_1h is not None and rsi_4h is not None:
            def rsi_zone(rsi):
                if rsi < 30:
                    return 'oversold'
                elif rsi > 70:
                    return 'overbought'
                else:
                    return 'neutral'
            zone_1h = rsi_zone(rsi_1h)
            zone_4h = rsi_zone(rsi_4h)
            if zone_1h == zone_4h:
                score += 0.25
                details.append('RSI zone aligned')
            max_score += 0.25
        else:
            max_score += 0.25
        
        # Normalize score (if max_score == 0, return 0.5)
        if max_score == 0:
            return 0.5
        
        alignment = score / max_score
        # Log details for debugging
        logger.debug(f'Multi‑timeframe alignment for {symbol}: {alignment:.2f} ({", ".join(details)})')
        return alignment

    def enhance_signal(self, signal, indicators):
        """Apply sentiment, ADX, volume, and multi‑timeframe alignment filters to signal strength"""
        if not signal:
            return signal
        # Get sentiment
        sentiment = self.get_latest_sentiment(signal['symbol'])
        sentiment_score = sentiment['score'] if sentiment else 0.5
        # ADX and volume
        adx = indicators.get('adx')
        volume_ratio = indicators.get('volume_ratio')
        
        strength = signal['strength']
        reasons = [signal['reason']]
        
        # Sentiment multiplier
        if sentiment:
            if signal['action'] == 'BUY':
                if sentiment_score > 0.6:
                    strength *= 1.2
                    reasons.append(f'positive sentiment ({sentiment_score:.2f})')
                elif sentiment_score < 0.4:
                    strength *= 0.5
                    reasons.append(f'negative sentiment ({sentiment_score:.2f})')
            elif signal['action'] == 'SELL':
                if sentiment_score < 0.4:
                    strength *= 1.2
                    reasons.append(f'negative sentiment ({sentiment_score:.2f})')
                elif sentiment_score > 0.6:
                    strength *= 0.5
                    reasons.append(f'positive sentiment ({sentiment_score:.2f})')
        
        # ADX regime filter (30+ = confirmed trend, was 25)
        if adx is not None:
            if adx > 30:  # trending
                # Favor signals aligned with MACD direction
                macd_hist = indicators.get('macd_hist')
                if (signal['action'] == 'BUY' and macd_hist > 0) or (signal['action'] == 'SELL' and macd_hist < 0):
                    strength *= 1.3
                    reasons.append('trending market')
                else:
                    strength *= 0.7
                    reasons.append('counter‑trend')
            else:  # ranging
                # Favor mean‑reversion
                if (signal['action'] == 'BUY' and signal['rsi'] < 35) or (signal['action'] == 'SELL' and signal['rsi'] > 65):
                    strength *= 1.2
                    reasons.append('ranging market')
        
        # Volume confirmation
        if volume_ratio is not None and volume_ratio > 1.2:
            strength *= 1.2
            reasons.append('high volume')
        
        # Multi‑timeframe alignment
        alignment = self.multi_timeframe_alignment(signal['symbol'])
        alignment_multiplier = 1.0
        if alignment >= 0.75:
            alignment_multiplier = 1.3
            reasons.append(f'strong timeframe alignment ({alignment:.2f})')
        elif alignment >= 0.5:
            alignment_multiplier = 1.1
            reasons.append(f'good timeframe alignment ({alignment:.2f})')
        elif alignment < 0.25:
            alignment_multiplier = 0.5
            reasons.append(f'poor timeframe alignment ({alignment:.2f})')
        elif alignment < 0.5:
            alignment_multiplier = 0.8
            reasons.append(f'weak timeframe alignment ({alignment:.2f})')
        strength *= alignment_multiplier
        
        # Market regime adjustment (VIX-based)
        if self.correlation_tracker:
            try:
                strength = self.correlation_tracker.regime_signal_adjustment(strength, signal['action'])
                regime, vix = self.correlation_tracker.detect_regime()
                if regime != 'unknown':
                    reasons.append(f'regime={regime}' + (f' VIX={vix:.0f}' if vix else ''))
            except Exception as e:
                logger.debug(f"Regime adjustment failed: {e}")

        # Cap strength at 100
        strength = min(strength, 100)
        signal['strength'] = strength
        signal['reason'] = ' | '.join(reasons)
        return signal


    def execute_paper_trades(self, signals: List[Dict], risk_exits: List = None):
        """Execute paper trades based on signals and risk exits."""
        if not self.portfolio:
            logger.warning("No portfolio loaded — cannot execute trades")
            return

        executed = []

        # 1. Handle risk-triggered exits first (stop-loss, circuit breaker, etc.)
        if risk_exits:
            for exit_sig in risk_exits:
                symbol = exit_sig.symbol if hasattr(exit_sig, 'symbol') else str(exit_sig)
                if symbol in self.portfolio.positions:
                    reason = exit_sig.reason if hasattr(exit_sig, 'reason') else 'risk trigger'
                    exit_type = exit_sig.exit_type if hasattr(exit_sig, 'exit_type') else 'risk_exit'
                    self.portfolio.set_trade_context(symbol, exit_reason=exit_type)
                    logger.info(f"Risk exit: selling {symbol} — {reason}")
                    self.portfolio.execute_sell_all(symbol)
                    executed.append(f"RISK_SELL {symbol}")

        # 2. Execute signal-driven trades
        portfolio_value = self.portfolio.get_portfolio_value()

        for sig in signals:
            symbol = sig['symbol']
            action = sig['action']
            strength = sig['strength']

            # Determine signal type from reason text
            reason = sig.get('reason', '')
            if 'oversold' in reason or 'overbought' in reason:
                sig_type = 'mean_reversion'
            elif 'Trend' in reason:
                sig_type = 'trend_following'
            elif 'Momentum' in reason or 'momentum' in reason:
                sig_type = 'momentum'
            else:
                sig_type = 'unknown'

            self.portfolio.set_trade_context(
                symbol, signal_type=sig_type, signal_strength=strength,
                exit_reason='signal',
            )
            # Rich decision_context for signal engine trades
            import json as _json
            strategy_name = sig.get("_strategy_name")
            self.portfolio._pending_decision_context = _json.dumps({
                "signal_type": sig_type,
                "signal_strength": round(strength, 2),
                "action": action,
                "reason": reason,
                "strategy": strategy_name,
                "timeframe": sig.get("_timeframe"),
            })
            # Write strategy_id for warehouse attribution
            if strategy_name:
                sid = _resolve_strategy_id(strategy_name)
                if sid:
                    self.portfolio._pending_strategy_id = sid

            if action == 'BUY':
                # Skip if we already hold this symbol (avoid doubling down)
                if symbol in self.portfolio.positions:
                    logger.info(f"Already hold {symbol} — skipping BUY signal")
                    continue

                # Dedup: skip if same symbol was bought in the last 60 seconds
                last_buy_time = self.trade_state.get(f'last_buy_{symbol}')
                if last_buy_time:
                    try:
                        elapsed = (datetime.now() - datetime.fromisoformat(last_buy_time)).total_seconds()
                        if elapsed < 60:
                            logger.info(f"Dedup: {symbol} BUY skipped ({elapsed:.0f}s since last buy)")
                            continue
                    except Exception:
                        pass

                # Position size: scale with signal strength
                # strength < 50  → skip (too weak after fixes)
                # strength 50-65 → 3-5% of portfolio (low conviction)
                # strength 65-80 → 5-10% of portfolio (moderate)
                # strength 80+   → 10-15% of portfolio (high conviction)
                if strength < 50:
                    logger.info(f"Signal too weak for {symbol}: strength={strength:.1f} < 50")
                    continue
                if strength >= 80:
                    alloc_pct = 0.10 + min((strength - 80) / 200, 0.05)
                elif strength >= 65:
                    alloc_pct = 0.05 + (strength - 65) / 300
                else:
                    alloc_pct = 0.03 + (strength - 50) / 750

                # Cap at per-asset-class position limit
                _prof = get_asset_class_profile(symbol) if get_asset_class_profile else {}
                max_pct = _prof.get("max_position_pct", 0.25)
                alloc_pct = min(alloc_pct, max_pct)

                buy_amount = portfolio_value * alloc_pct
                buy_amount = min(buy_amount, self.portfolio.capital * 0.95)  # keep 5% cash reserve

                min_trade = _prof.get("min_trade_usd", 10.0)
                if buy_amount < min_trade:
                    logger.info(f"Buy amount ${buy_amount:.2f} below min ${min_trade:.0f} for {symbol}")
                    continue

                logger.info(f"Signal BUY {symbol}: strength={strength:.1f}, amount=${buy_amount:.2f}")
                if self.portfolio.execute_buy(symbol, buy_amount):
                    executed.append(f"BUY {symbol} ${buy_amount:.2f}")
                    self.trade_state[f'last_buy_{symbol}'] = datetime.now().isoformat()
                    # Mark signal as executed in tracker
                    if self.strategy_tracker and '_signal_id' in sig:
                        price = self.portfolio.get_current_price(symbol)
                        self.strategy_tracker.mark_signal_executed(sig['_signal_id'], price)

            elif action == 'SELL':
                if symbol not in self.portfolio.positions:
                    logger.info(f"No position in {symbol} — skipping SELL signal")
                    continue

                # Minimum holding period (asset-class-aware, prevents fee-destroying churn)
                # Risk-driven exits (stop-loss, circuit breaker) bypass this
                _sell_prof = get_asset_class_profile(symbol) if get_asset_class_profile else {}
                min_hold = _sell_prof.get("min_hold_hours", 12)
                entry_date_str = self.portfolio.entry_prices.get(f"{symbol}_date")
                if entry_date_str and sig.get('_strategy_name', '') != 'risk_exit':
                    try:
                        entry_dt = datetime.fromisoformat(entry_date_str)
                        held_hours = (datetime.now() - entry_dt).total_seconds() / 3600
                        if held_hours < min_hold:
                            logger.info(f"Min hold: {symbol} held {held_hours:.1f}h < {min_hold}h — skipping SELL")
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

                logger.info(f"Signal SELL {symbol}: strength={strength:.1f}, pct={sell_pct*100:.0f}%")
                entry_price = self.portfolio.positions.get(symbol, {}).get('avg_price', 0)
                exit_price = self.portfolio.get_current_price(symbol)
                if sell_pct >= 1.0:
                    self.portfolio.execute_sell_all(symbol)
                else:
                    self.portfolio.execute_partial_sell(symbol, sell_pct)
                executed.append(f"SELL {symbol} {sell_pct*100:.0f}%")
                # Update signal outcome in tracker
                if self.strategy_tracker and '_signal_id' in sig and entry_price > 0:
                    outcome_pct = (exit_price - entry_price) / entry_price
                    self.strategy_tracker.update_signal_outcome(
                        sig['_signal_id'], outcome_pct, exit_price, 'signal_sell'
                    )

        if executed:
            logger.info(f"Executed {len(executed)} paper trades: {', '.join(executed)}")
            # Save updated trade state
            self.trade_state['last_trade_time'] = datetime.now().isoformat()
            self.trade_state['last_trades'] = executed
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

    print(f"\n{'='*60}")
    print(f"  {name} ({trading_system.portfolio_mode})")
    print(f"{'='*60}")

    if risk_report:
        print(risk_report)

    if risk_exits:
        print(f"\n⚠️  {len(risk_exits)} risk-triggered exits:")
        for ex in risk_exits:
            label = ex.label if hasattr(ex, 'label') else str(ex)
            reason = ex.reason if hasattr(ex, 'reason') else ''
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
    parser.add_argument('--check', action='store_true',
                        help='Check signals and execute trades')
    parser.add_argument('--test', action='store_true',
                        help='Dry run: check signals but do not trade')
    parser.add_argument('--execute', action='store_true',
                        help='(Alias for --check, kept for backwards compat)')
    parser.add_argument('--portfolio', type=str, default=None,
                        help='Run only this portfolio (by name). Default: all active.')
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
