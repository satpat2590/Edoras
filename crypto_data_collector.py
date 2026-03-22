#!/usr/bin/env python3
"""
Crypto data collector for Coinbase portfolio optimization.
Collects candlestick data and calculates technical indicators.
"""

import os
import sys
import json
import sqlite3
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import numpy as np
import pandas as pd
from coinbase.rest import RESTClient

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class CryptoDataCollector:
    """Collect and store crypto market data from Coinbase"""
    
    def __init__(self, db_path: str = "crypto_data.db", api_key: str = None, api_secret: str = None, include_top_cryptos: bool = True):
        """Initialize collector with database and API credentials"""
        self.db_path = db_path
        self.api_key = api_key or os.getenv("COINBASE_API_KEY")
        self.api_secret = api_secret or os.getenv("COINBASE_API_SECRET")
        self.include_top_cryptos = include_top_cryptos
        
        if not self.api_key or not self.api_secret:
            raise ValueError("Coinbase API credentials not provided and not in environment")
        
        # Fix newlines in EC private key
        if self.api_secret and "-----BEGIN EC PRIVATE KEY-----" in self.api_secret:
            self.api_secret = self.api_secret.replace('\\n', '\n')
        
        # Initialize Coinbase client
        self.client = RESTClient(api_key=self.api_key, api_secret=self.api_secret)
        
        # Initialize database
        self.init_database()
        
        # Portfolio symbols (will be populated from portfolio)
        self.portfolio_symbols = []
        
        # Timeframe definitions (Coinbase granularity strings)
        # Note: 4h is built by aggregating 1h candles (see aggregate_4h_candles).
        self.timeframes = {
            '1h': 'ONE_HOUR',
            '1d': 'ONE_DAY',
        }
    
    def _get_connection(self):
        """Get database connection"""
        return sqlite3.connect(self.db_path)
    
    def init_database(self):
        """Initialize SQLite database with schema"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        # Create candlestick data table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS candlesticks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            timestamp INTEGER NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume REAL NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(symbol, timeframe, timestamp)
        )
        ''')
        
        # Create technical indicators table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS indicators (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            timestamp INTEGER NOT NULL,
            sma_20 REAL,
            sma_50 REAL,
            sma_200 REAL,
            ema_12 REAL,
            ema_26 REAL,
            rsi_14 REAL,
            macd_line REAL,
            macd_signal REAL,
            macd_histogram REAL,
            bb_upper REAL,
            bb_middle REAL,
            bb_lower REAL,
            bb_width REAL,
            atr_14 REAL,
            volume_sma_20 REAL,
            volume_ratio REAL,
            adx_14 REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(symbol, timeframe, timestamp),
            FOREIGN KEY (symbol, timeframe, timestamp) 
                REFERENCES candlesticks(symbol, timeframe, timestamp) ON DELETE CASCADE
        )
        ''')
        
        # Create portfolio analysis table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS portfolio_analysis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            analysis_date TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            short_term_signal REAL,
            medium_term_signal REAL,
            long_term_signal REAL,
            trend_strength REAL,
            volatility_level REAL,
            support_1 REAL,
            support_2 REAL,
            resistance_1 REAL,
            resistance_2 REAL,
            action TEXT,
            confidence REAL,
            reasoning TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(analysis_date, symbol, timeframe)
        )
        ''')
        
        # Create collection log table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS collection_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            last_timestamp INTEGER,
            data_points INTEGER DEFAULT 0,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'active',
            error_message TEXT,
            UNIQUE(symbol, timeframe)
        )
        ''')
        
        conn.commit()
        conn.close()
        logger.info(f"Database initialized at {self.db_path}")
    
    def is_symbol_supported(self, symbol: str) -> bool:
        """Check if a symbol is supported by Coinbase candles API"""
        import time
        try:
            # Try to get a single candle (last hour) to validate symbol
            end_time = int(time.time())
            start_time = end_time - 3600  # 1 hour ago
            
            # Use ONE_HOUR granularity for quick test
            test_candles = self.client.get_candles(
                product_id=symbol,
                start=str(start_time),
                end=str(end_time),
                granularity='ONE_HOUR'
            )
            
            # If we get a response without INVALID_ARGUMENT error, symbol is supported
            if hasattr(test_candles, 'candles'):
                logger.debug(f"Symbol {symbol} is supported")
                return True
            else:
                logger.warning(f"Symbol {symbol}: no candles returned")
                return False
                
        except Exception as e:
            # Check if it's an INVALID_ARGUMENT error
            if "INVALID_ARGUMENT" in str(e) or "ProductID is invalid" in str(e):
                logger.warning(f"Symbol {symbol} not supported: {e}")
                return False
            # Other errors (rate limit, network) - assume supported
            logger.warning(f"Symbol {symbol} check error (assuming supported): {e}")
            return True
    
    def get_portfolio_symbols(self) -> List[str]:
        """Get symbols for current portfolio assets"""
        if self.portfolio_symbols:
            return self.portfolio_symbols
        
        # Hardcoded portfolio symbols (verified supported on Coinbase)
        # Based on actual portfolio holdings and working API validation
        portfolio_symbols = [
            "ETH-USD", "BTC-USD", "XRP-USD", "TROLL-USD",
            "BONK-USD", "FET-USD", "AMP-USD", "GRT-USD"
        ]
        
        # Filter out any that might be unsupported (safety check)
        supported_symbols = []
        for symbol in portfolio_symbols:
            # Quick check without API call - assume all are supported
            # as they've been verified in previous runs
            supported_symbols.append(symbol)
        
        self.portfolio_symbols = supported_symbols
        logger.info(f"Using {len(supported_symbols)} hardcoded portfolio symbols: {supported_symbols}")
        return supported_symbols
    
    def get_top_crypto_symbols(self) -> List[str]:
        """Get symbols for top cryptocurrencies by market cap (pre-verified supported)"""
        # Pre-verified supported top cryptocurrencies on Coinbase
        # TRX-USD and MATIC-USD are NOT supported on Coinbase Advanced
        supported_top_cryptos = [
            "BTC-USD", "ETH-USD", "BNB-USD", "SOL-USD", "XRP-USD",
            "ADA-USD", "AVAX-USD", "DOGE-USD", "DOT-USD",
            "LINK-USD", "SHIB-USD", "LTC-USD", "UNI-USD"
        ]
        
        logger.info(f"Using {len(supported_top_cryptos)} pre-verified top cryptocurrencies")
        return supported_top_cryptos
    
    def get_historical_candles(self, symbol: str, timeframe: str, 
                               start_time: int, end_time: int) -> List[Dict]:
        """Get historical candlestick data from Coinbase"""
        coinbase_granularity = self.timeframes.get(timeframe)
        
        if not coinbase_granularity:
            if timeframe == '4h':
                # 4h is built from 1h aggregation — fetch 1h instead
                logger.debug(f"4h requested for {symbol} — use aggregate_4h_candles() instead")
                return []
            else:
                coinbase_granularity = 'ONE_DAY'
        
        try:
            candles = self.client.get_candles(
                product_id=symbol,
                start=str(start_time),
                end=str(end_time),
                granularity=coinbase_granularity
            )
            
            if hasattr(candles, 'candles'):
                data = []
                for candle in candles.candles:
                    data.append({
                        'timestamp': getattr(candle, 'start', 0),
                        'open': float(getattr(candle, 'open', 0)),
                        'high': float(getattr(candle, 'high', 0)),
                        'low': float(getattr(candle, 'low', 0)),
                        'close': float(getattr(candle, 'close', 0)),
                        'volume': float(getattr(candle, 'volume', 0))
                    })
                return data
            else:
                logger.warning(f"No candles returned for {symbol} {timeframe}")
                return []
                
        except Exception as e:
            logger.error(f"Error getting candles for {symbol} {timeframe}: {e}")
            return []
    
    def save_candlesticks(self, symbol: str, timeframe: str, candles: List[Dict]):
        """Save candlestick data to database"""
        if not candles:
            return 0
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        saved_count = 0
        for candle in candles:
            try:
                cursor.execute('''
                INSERT OR IGNORE INTO candlesticks 
                (symbol, timeframe, timestamp, open, high, low, close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    symbol, timeframe,
                    candle['timestamp'],
                    candle['open'],
                    candle['high'],
                    candle['low'],
                    candle['close'],
                    candle['volume']
                ))
                if cursor.rowcount > 0:
                    saved_count += 1
            except Exception as e:
                logger.warning(f"Error saving candle {symbol} {timeframe} {candle['timestamp']}: {e}")
        
        # Update collection log
        if saved_count > 0:
            latest_timestamp = max(c['timestamp'] for c in candles)
            cursor.execute('''
            INSERT OR REPLACE INTO collection_log 
            (symbol, timeframe, last_timestamp, data_points, last_updated)
            VALUES (?, ?, ?, 
                COALESCE((SELECT data_points FROM collection_log WHERE symbol=? AND timeframe=?), 0) + ?,
                CURRENT_TIMESTAMP)
            ''', (symbol, timeframe, latest_timestamp, symbol, timeframe, saved_count))
        
        conn.commit()
        conn.close()
        
        if saved_count > 0:
            logger.info(f"Saved {saved_count} candles for {symbol} {timeframe}")
        
        return saved_count
    
    def aggregate_4h_candles(self, symbol: str, lookback_days: int = 14) -> int:
        """Build 4h candles by aggregating 1h candles at UTC boundaries (00,04,08,12,16,20).

        This replaces the old approach of fetching Coinbase SIX_HOUR candles and
        mislabeling them as 4h.  Call after 1h data has been refreshed.

        Returns the number of new 4h candles inserted.
        """
        conn = sqlite3.connect(self.db_path)
        cutoff_ts = int((datetime.now() - timedelta(days=lookback_days)).timestamp())

        # Pull 1h candles within the lookback window
        df = pd.read_sql_query(
            "SELECT timestamp, open, high, low, close, volume "
            "FROM candlesticks WHERE symbol=? AND timeframe='1h' AND timestamp>=? "
            "ORDER BY timestamp",
            conn,
            params=(symbol, cutoff_ts),
        )
        if df.empty:
            conn.close()
            return 0

        # Assign each 1h candle to its 4h bucket (floor to 4h boundary)
        df["bucket"] = (df["timestamp"] // (4 * 3600)) * (4 * 3600)

        agg = (
            df.groupby("bucket")
            .agg(
                open=("open", "first"),
                high=("high", "max"),
                low=("low", "min"),
                close=("close", "last"),
                volume=("volume", "sum"),
                count=("timestamp", "count"),
            )
            .reset_index()
        )

        # Only keep complete 4h bars (4 hourly candles)
        agg = agg[agg["count"] == 4]

        inserted = 0
        cursor = conn.cursor()
        for _, row in agg.iterrows():
            try:
                cursor.execute(
                    "INSERT OR REPLACE INTO candlesticks "
                    "(symbol, timeframe, timestamp, open, high, low, close, volume) "
                    "VALUES (?, '4h', ?, ?, ?, ?, ?, ?)",
                    (symbol, int(row["bucket"]), row["open"], row["high"],
                     row["low"], row["close"], row["volume"]),
                )
                if cursor.rowcount > 0:
                    inserted += 1
            except Exception as e:
                logger.error(f"Error inserting 4h candle for {symbol}: {e}")

        conn.commit()
        conn.close()

        if inserted > 0:
            logger.info(f"Aggregated {inserted} 4h candles for {symbol}")
        return inserted

    def backfill_symbol(self, symbol: str, timeframe: str = '1d', days_back: int = 365):
        """Backfill historical data for a symbol"""
        logger.info(f"Backfilling {symbol} {timeframe} for {days_back} days")
        
        end_time = int(datetime.now().timestamp())
        start_time = end_time - (days_back * 24 * 3600)
        
        # Get data in chunks to avoid API limits
        chunk_days = 30  # 30 days per request
        total_saved = 0
        
        current_start = start_time
        while current_start < end_time:
            current_end = min(current_start + (chunk_days * 24 * 3600), end_time)
            
            candles = self.get_historical_candles(symbol, timeframe, current_start, current_end)
            saved = self.save_candlesticks(symbol, timeframe, candles)
            total_saved += saved
            
            if saved == 0 and candles:
                # Already have this data
                break
            
            current_start = current_end
        
        logger.info(f"Backfilled {total_saved} candles for {symbol} {timeframe}")
        return total_saved
    
    def update_latest_data(self, symbol: str, timeframe: str = '1d', lookback_days: int = 7):
        """Update with latest data for a symbol"""
        # Get last timestamp from database
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
        SELECT MAX(timestamp) FROM candlesticks 
        WHERE symbol = ? AND timeframe = ?
        ''', (symbol, timeframe))
        
        result = cursor.fetchone()
        last_timestamp = result[0] if result[0] else 0
        conn.close()
        
        # If no data, backfill
        if last_timestamp == 0:
            return self.backfill_symbol(symbol, timeframe, lookback_days)
        
        # Get data from last timestamp to now
        start_time = last_timestamp
        end_time = int(datetime.now().timestamp())
        
        # Add small buffer to ensure overlap
        start_time = max(start_time - 3600, 0)
        
        candles = self.get_historical_candles(symbol, timeframe, start_time, end_time)
        saved = self.save_candlesticks(symbol, timeframe, candles)
        
        logger.info(f"Updated {saved} new candles for {symbol} {timeframe}")
        return saved
    
    def calculate_indicators(self, symbol: str, timeframe: str):
        """Calculate technical indicators for symbol/timeframe"""
        # Get data from database
        conn = sqlite3.connect(self.db_path)
        query = '''
        SELECT timestamp, open, high, low, close, volume
        FROM candlesticks
        WHERE symbol = ? AND timeframe = ?
        ORDER BY timestamp ASC
        '''
        
        df = pd.read_sql_query(query, conn, params=(symbol, timeframe))
        conn.close()
        
        if len(df) < 50:  # Need enough data for indicators
            logger.warning(f"Not enough data for indicators: {symbol} {timeframe} ({len(df)} rows)")
            return 0
        
        # Calculate indicators
        df['sma_20'] = df['close'].rolling(window=20).mean()
        df['sma_50'] = df['close'].rolling(window=50).mean()
        df['sma_200'] = df['close'].rolling(window=200).mean()
        df['ema_12'] = df['close'].ewm(span=12).mean()
        df['ema_26'] = df['close'].ewm(span=26).mean()
        
        # RSI
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df['rsi_14'] = 100 - (100 / (1 + rs))
        
        # MACD
        df['macd_line'] = df['ema_12'] - df['ema_26']
        df['macd_signal'] = df['macd_line'].ewm(span=9).mean()
        df['macd_histogram'] = df['macd_line'] - df['macd_signal']
        
        # Bollinger Bands
        df['bb_middle'] = df['close'].rolling(window=20).mean()
        bb_std = df['close'].rolling(window=20).std()
        df['bb_upper'] = df['bb_middle'] + (bb_std * 2)
        df['bb_lower'] = df['bb_middle'] - (bb_std * 2)
        df['bb_width'] = (df['bb_upper'] - df['bb_lower']) / df['bb_middle']
        
        # ATR (Average True Range)
        high_low = df['high'] - df['low']
        high_close = abs(df['high'] - df['close'].shift())
        low_close = abs(df['low'] - df['close'].shift())
        true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        df['atr_14'] = true_range.rolling(window=14).mean()
        
        # Volume indicators
        df['volume_sma_20'] = df['volume'].rolling(window=20).mean()
        df['volume_ratio'] = df['volume'] / df['volume_sma_20']
        
        # ADX (simplified)
        df['plus_dm'] = df['high'].diff().apply(lambda x: x if x > 0 else 0)
        df['minus_dm'] = -df['low'].diff().apply(lambda x: x if x < 0 else 0)
        df['tr'] = true_range
        df['plus_di'] = 100 * (df['plus_dm'].rolling(14).mean() / df['tr'].rolling(14).mean())
        df['minus_di'] = 100 * (df['minus_dm'].rolling(14).mean() / df['tr'].rolling(14).mean())
        df['dx'] = 100 * abs(df['plus_di'] - df['minus_di']) / (df['plus_di'] + df['minus_di'])
        df['adx_14'] = df['dx'].rolling(14).mean()
        
        # Save indicators to database
        conn = sqlite3.connect(self.db_path)
        saved = 0
        
        for _, row in df.iterrows():
            if pd.isna(row['sma_20']):  # Skip rows with incomplete indicators
                continue
            
            try:
                cursor = conn.cursor()
                cursor.execute('''
                INSERT OR REPLACE INTO indicators 
                (symbol, timeframe, timestamp, sma_20, sma_50, sma_200, 
                 ema_12, ema_26, rsi_14, macd_line, macd_signal, macd_histogram,
                 bb_upper, bb_middle, bb_lower, bb_width, atr_14,
                 volume_sma_20, volume_ratio, adx_14)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    symbol, timeframe, int(row['timestamp']),
                    float(row['sma_20']) if not pd.isna(row['sma_20']) else None,
                    float(row['sma_50']) if not pd.isna(row['sma_50']) else None,
                    float(row['sma_200']) if not pd.isna(row['sma_200']) else None,
                    float(row['ema_12']) if not pd.isna(row['ema_12']) else None,
                    float(row['ema_26']) if not pd.isna(row['ema_26']) else None,
                    float(row['rsi_14']) if not pd.isna(row['rsi_14']) else None,
                    float(row['macd_line']) if not pd.isna(row['macd_line']) else None,
                    float(row['macd_signal']) if not pd.isna(row['macd_signal']) else None,
                    float(row['macd_histogram']) if not pd.isna(row['macd_histogram']) else None,
                    float(row['bb_upper']) if not pd.isna(row['bb_upper']) else None,
                    float(row['bb_middle']) if not pd.isna(row['bb_middle']) else None,
                    float(row['bb_lower']) if not pd.isna(row['bb_lower']) else None,
                    float(row['bb_width']) if not pd.isna(row['bb_width']) else None,
                    float(row['atr_14']) if not pd.isna(row['atr_14']) else None,
                    float(row['volume_sma_20']) if not pd.isna(row['volume_sma_20']) else None,
                    float(row['volume_ratio']) if not pd.isna(row['volume_ratio']) else None,
                    float(row['adx_14']) if not pd.isna(row['adx_14']) else None
                ))
                saved += 1
            except Exception as e:
                logger.warning(f"Error saving indicator for {symbol}: {e}")
        
        conn.commit()
        conn.close()
        
        logger.info(f"Calculated indicators for {saved} candles of {symbol} {timeframe}")
        return saved
    
    def analyze_portfolio_signals(self):
        """Analyze portfolio for short-term and long-term signals"""
        symbols = self.get_portfolio_symbols()
        analysis_date = datetime.now().strftime('%Y-%m-%d')
        
        conn = sqlite3.connect(self.db_path)
        
        for symbol in symbols:
            for timeframe in ['1h', '4h', '1d']:
                # Get latest indicators
                query = '''
                SELECT * FROM indicators 
                WHERE symbol = ? AND timeframe = ?
                ORDER BY timestamp DESC LIMIT 1
                '''
                
                df = pd.read_sql_query(query, conn, params=(symbol, timeframe))
                
                if df.empty:
                    continue
                
                row = df.iloc[0]
                
                # Calculate signals
                short_term_signal = 0
                medium_term_signal = 0  
                long_term_signal = 0
                
                # Short-term (1h) signals
                if timeframe == '1h':
                    # RSI based
                    rsi = row.get('rsi_14', 50)
                    if rsi < 30:
                        short_term_signal = 80  # Oversold, bullish
                    elif rsi > 70:
                        short_term_signal = -80  # Overbought, bearish
                    else:
                        short_term_signal = 0
                
                # Medium-term (4h) signals  
                elif timeframe == '4h':
                    # MACD based
                    macd_hist = row.get('macd_histogram', 0)
                    if macd_hist > 0:
                        medium_term_signal = 60
                    else:
                        medium_term_signal = -40
                
                # Long-term (1d) signals
                elif timeframe == '1d':
                    # Trend and moving averages
                    sma_50 = row.get('sma_50')
                    sma_200 = row.get('sma_200')
                    current_price = row.get('close', 0) if 'close' in df.columns else 0
                    
                    # Check if we have both SMA values
                    if sma_50 is not None and sma_200 is not None and current_price > 0:
                        if sma_50 > sma_200 and current_price > sma_50:
                            long_term_signal = 90  # Strong uptrend
                        elif sma_50 < sma_200 and current_price < sma_50:
                            long_term_signal = -90  # Strong downtrend
                        else:
                            long_term_signal = 0
                    else:
                        # Not enough data for SMA comparison
                        long_term_signal = 0
                
                # Calculate trend strength
                adx = row.get('adx_14', 0) or 0
                trend_strength = min(float(adx), 100)
                
                # Calculate volatility
                atr = row.get('atr_14', 0) or 0
                current_price = row.get('close', 1) or 1
                volatility_level = (float(atr) / current_price) * 100 * np.sqrt(365)  # Annualized
                
                # Determine action
                if timeframe == '1d':  # Use daily for action decisions
                    if long_term_signal > 50:
                        action = "HOLD/ADD"
                        confidence = abs(long_term_signal)
                        reasoning = "Strong uptrend"
                    elif long_term_signal < -50:
                        action = "REDUCE/SELL"
                        confidence = abs(long_term_signal)
                        reasoning = "Strong downtrend"
                    else:
                        action = "HOLD"
                        confidence = 50
                        reasoning = "Sideways market"
                else:
                    action = None
                    confidence = 0
                    reasoning = ""
                
                # Save analysis
                cursor = conn.cursor()
                cursor.execute('''
                INSERT OR REPLACE INTO portfolio_analysis 
                (analysis_date, symbol, timeframe, short_term_signal, 
                 medium_term_signal, long_term_signal, trend_strength, 
                 volatility_level, action, confidence, reasoning)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    analysis_date, symbol, timeframe,
                    short_term_signal, medium_term_signal, long_term_signal,
                    trend_strength, volatility_level,
                    action, confidence, reasoning
                ))
        
        conn.commit()
        conn.close()
        logger.info(f"Portfolio analysis completed for {len(symbols)} symbols")
    
    def generate_portfolio_report(self) -> str:
        """Generate human-readable portfolio analysis report"""
        conn = sqlite3.connect(self.db_path)
        
        # Get latest analysis
        query = '''
        SELECT * FROM portfolio_analysis 
        WHERE analysis_date = (SELECT MAX(analysis_date) FROM portfolio_analysis)
        ORDER BY symbol, timeframe
        '''
        
        df = pd.read_sql_query(query, conn)
        conn.close()
        
        if df.empty:
            return "No portfolio analysis available. Run analysis first."
        
        report_lines = []
        report_lines.append("📊 **Crypto Portfolio Technical Analysis**")
        report_lines.append(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        report_lines.append("")
        
        # Group by symbol
        symbols = df['symbol'].unique()
        
        for symbol in symbols:
            if symbol.endswith('-USD'):
                coin = symbol.replace('-USD', '')
            else:
                coin = symbol
            
            symbol_df = df[df['symbol'] == symbol]
            
            report_lines.append(f"**{coin} ({symbol})**")
            
            for timeframe in ['1d', '4h', '1h']:
                tf_df = symbol_df[symbol_df['timeframe'] == timeframe]
                if not tf_df.empty:
                    row = tf_df.iloc[0]
                    
                    timeframe_name = {
                        '1h': '1-Hour',
                        '4h': '4-Hour', 
                        '1d': 'Daily'
                    }.get(timeframe, timeframe)
                    
                    # Get signal
                    if timeframe == '1h':
                        signal = row.get('short_term_signal', 0)
                    elif timeframe == '4h':
                        signal = row.get('medium_term_signal', 0)
                    else:
                        signal = row.get('long_term_signal', 0)
                    
                    # Signal interpretation
                    if signal > 60:
                        signal_text = "🚀 Strong Buy"
                    elif signal > 20:
                        signal_text = "📈 Buy"
                    elif signal < -60:
                        signal_text = "📉 Strong Sell"
                    elif signal < -20:
                        signal_text = "🔻 Sell"
                    else:
                        signal_text = "➡️ Neutral"
                    
                    report_lines.append(f"  {timeframe_name}: {signal_text} ({signal:.0f})")
            
            # Add action recommendation if available
            daily_df = symbol_df[symbol_df['timeframe'] == '1d']
            if not daily_df.empty and daily_df.iloc[0]['action']:
                action = daily_df.iloc[0]['action']
                confidence = daily_df.iloc[0]['confidence']
                reasoning = daily_df.iloc[0]['reasoning'] or ""
                
                report_lines.append(f"  💡 **Recommendation**: {action} ({confidence:.0f}% confidence)")
                if reasoning:
                    report_lines.append(f"  📝 *{reasoning}*")
            
            report_lines.append("")
        
        # Overall portfolio assessment
        report_lines.append("**📈 Overall Portfolio Assessment**")
        
        # Determine primary signal for each symbol (prioritize daily > 4h > 1h)
        symbol_signals = {}
        for symbol in df['symbol'].unique():
            symbol_df = df[df['symbol'] == symbol]
            
            # Try to get signal from daily timeframe first
            daily_df = symbol_df[symbol_df['timeframe'] == '1d']
            if not daily_df.empty:
                signal = daily_df.iloc[0]['long_term_signal']
                if signal != 0:
                    symbol_signals[symbol] = signal
                    continue
            
            # Try 4h timeframe
            fourh_df = symbol_df[symbol_df['timeframe'] == '4h']
            if not fourh_df.empty:
                signal = fourh_df.iloc[0]['medium_term_signal']
                if signal != 0:
                    symbol_signals[symbol] = signal
                    continue
            
            # Use 1h timeframe
            oneh_df = symbol_df[symbol_df['timeframe'] == '1h']
            if not oneh_df.empty:
                signal = oneh_df.iloc[0]['short_term_signal']
                symbol_signals[symbol] = signal
            else:
                symbol_signals[symbol] = 0  # No data
        
        # Count buy/sell signals (using same thresholds as display)
        buy_signals = sum(1 for s in symbol_signals.values() if s > 20)
        sell_signals = sum(1 for s in symbol_signals.values() if s < -20)
        neutral_signals = len(symbol_signals) - buy_signals - sell_signals
        
        report_lines.append(f"• Buy signals: {buy_signals}")
        report_lines.append(f"• Sell signals: {sell_signals}")
        report_lines.append(f"• Neutral: {neutral_signals}")
        
        if buy_signals > sell_signals:
            report_lines.append("• Overall bias: **Bullish** 🟢")
        elif sell_signals > buy_signals:
            report_lines.append("• Overall bias: **Bearish** 🔴")
        else:
            report_lines.append("• Overall bias: **Neutral** 🟡")
        
        report_lines.append("")
        report_lines.append("**💡 Key Insights**")
        report_lines.append("• Short-term (1h): Look for RSI extremes (<30 oversold, >70 overbought)")
        report_lines.append("• Medium-term (4h): MACD histogram crossing zero indicates momentum shift")
        report_lines.append("• Long-term (1d): Golden/Death Cross (SMA 50 vs 200) for major trends")
        report_lines.append("")
        report_lines.append("---")
        report_lines.append("_Generated by Crypto Data Collector • Multi-timeframe analysis_")
        
        return "\n".join(report_lines)
    
    def run_daily_collection(self):
        """Run daily data collection and analysis"""
        logger.info("Starting daily data collection...")
        
        # Get portfolio symbols
        portfolio_symbols = self.get_portfolio_symbols()
        
        # Get top crypto symbols if enabled
        all_symbols = list(portfolio_symbols)
        if self.include_top_cryptos:
            top_symbols = self.get_top_crypto_symbols()
            # Add top symbols that aren't already in portfolio
            for symbol in top_symbols:
                if symbol not in all_symbols:
                    all_symbols.append(symbol)
        
        logger.info(f"Processing {len(all_symbols)} symbols ({len(portfolio_symbols)} portfolio, {len(all_symbols)-len(portfolio_symbols)} top cryptos)")
        
        # Update data for all symbols and timeframes
        for symbol in all_symbols:
            for timeframe in ['1d', '4h', '1h']:
                try:
                    self.update_latest_data(symbol, timeframe, lookback_days=7)
                    
                    # Calculate indicators if we have enough data
                    self.calculate_indicators(symbol, timeframe)
                    
                except Exception as e:
                    logger.error(f"Error processing {symbol} {timeframe}: {e}")
        
        # Run portfolio analysis (only for portfolio symbols)
        self.analyze_portfolio_signals()
        
        # Generate report (portfolio-focused)
        report = self.generate_portfolio_report()
        
        logger.info("Daily data collection completed")
        return report


def main():
    """Main execution function"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Crypto Data Collector")
    parser.add_argument('--init', action='store_true', help='Initialize database')
    parser.add_argument('--backfill', action='store_true', help='Backfill historical data')
    parser.add_argument('--update', action='store_true', help='Update latest data')
    parser.add_argument('--analyze', action='store_true', help='Run portfolio analysis')
    parser.add_argument('--report', action='store_true', help='Generate report')
    parser.add_argument('--daily', action='store_true', help='Run full daily collection')
    parser.add_argument('--symbol', type=str, help='Specific symbol to process')
    parser.add_argument('--timeframe', type=str, default='1d', help='Timeframe (1h, 4h, 1d)')
    parser.add_argument('--days', type=int, default=365, help='Days to backfill')
    
    args = parser.parse_args()
    
    # Get credentials from environment
    api_key = os.getenv("COINBASE_API_KEY")
    api_secret = os.getenv("COINBASE_API_SECRET")
    
    if not api_key or not api_secret:
        print("ERROR: COINBASE_API_KEY and COINBASE_API_SECRET environment variables not set")
        sys.exit(1)
    
    try:
        collector = CryptoDataCollector(
            db_path="crypto_data.db",
            api_key=api_key,
            api_secret=api_secret
        )
        
        if args.init:
            print("Database initialized")
            
        elif args.backfill:
            symbols = [args.symbol] if args.symbol else collector.get_portfolio_symbols()
            for symbol in symbols[:10]:  # Limit to 10 symbols for backfill
                print(f"Backfilling {symbol}...")
                collector.backfill_symbol(symbol, args.timeframe, args.days)
        
        elif args.update:
            symbols = [args.symbol] if args.symbol else collector.get_portfolio_symbols()
            for symbol in symbols:
                print(f"Updating {symbol}...")
                collector.update_latest_data(symbol, args.timeframe)
        
        elif args.analyze:
            collector.analyze_portfolio_signals()
            print("Portfolio analysis completed")
        
        elif args.report:
            report = collector.generate_portfolio_report()
            print(report)
        
        elif args.daily:
            report = collector.run_daily_collection()
            print("=" * 60)
            print(report)
            print("=" * 60)
            
            # Save report to file
            with open("portfolio_technical_report.txt", "w") as f:
                f.write(report)
            print("Report saved to portfolio_technical_report.txt")
        
        else:
            # Interactive mode
            print("Crypto Data Collector")
            print("=" * 50)
            print("Available commands:")
            print("  python crypto_data_collector.py --daily    # Full daily collection")
            print("  python crypto_data_collector.py --report   # Generate report")
            print("  python crypto_data_collector.py --backfill # Backfill historical data")
            print("  python crypto_data_collector.py --help     # Show all options")
            
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()