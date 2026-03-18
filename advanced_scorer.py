#!/usr/bin/env python3
"""
Advanced scoring model for cryptocurrencies.
Incorporates multi-timeframe analysis, momentum, trend, volatility, volume, and risk-adjusted metrics.
"""

import sqlite3
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
import logging

logger = logging.getLogger(__name__)

class AdvancedScoringModel:
    """Advanced scoring model for crypto assets"""
    
    def __init__(self, db_path: str = "crypto_data.db"):
        self.db_path = db_path
        
        # Timeframe weights for composite scoring
        self.timeframe_weights = {
            '1d': 0.40,  # Daily - most important for trend
            '4h': 0.35,  # 4-hour - intermediate
            '1h': 0.25,  # 1-hour - short-term
        }
        
        # Component weights for final score
        self.component_weights = {
            'momentum': 0.40,
            'trend': 0.25,
            'volatility': 0.15,
            'volume': 0.10,
            'risk_adjusted': 0.10
        }
    
    def get_symbol_data(self, symbol: str) -> Dict[str, pd.DataFrame]:
        """Get all timeframe data for a symbol"""
        conn = sqlite3.connect(self.db_path)
        
        data = {}
        for timeframe in ['1h', '4h', '1d']:
            query = '''
            SELECT 
                i.timestamp,
                i.rsi_14,
                i.macd_histogram,
                i.macd_line,
                i.macd_signal,
                i.adx_14,
                i.atr_14,
                i.bb_width,
                i.sma_20,
                i.sma_50,
                i.sma_200,
                i.volume_ratio,
                c.close as price,
                c.volume,
                c.high,
                c.low
            FROM indicators i
            JOIN candlesticks c ON i.symbol = c.symbol 
                AND i.timeframe = c.timeframe 
                AND i.timestamp = c.timestamp
            WHERE i.symbol = ? AND i.timeframe = ?
            ORDER BY i.timestamp DESC
            LIMIT 100  -- Get last 100 periods for analysis
            '''
            
            df = pd.read_sql_query(query, conn, params=(symbol, timeframe))
            if not df.empty:
                # Convert timestamp to datetime
                df['timestamp'] = pd.to_datetime(df['timestamp'], unit='s')
                df.set_index('timestamp', inplace=True)
                df.sort_index(inplace=True)
                
                # Calculate returns
                df['returns'] = df['price'].pct_change()
                
                # Calculate moving average slopes (trend)
                for ma in [20, 50, 200]:
                    col = f'sma_{ma}'
                    if col in df.columns:
                        # Convert to numeric, fill NaN with forward/backward fill
                        sma_series = pd.to_numeric(df[col], errors='coerce')
                        sma_series = sma_series.ffill().bfill()
                        if not sma_series.empty:
                            df[f'{col}_slope'] = sma_series.diff().rolling(5).mean()
                
                data[timeframe] = df
        
        conn.close()
        return data
    
    def calculate_momentum_score(self, data: Dict[str, pd.DataFrame]) -> float:
        """Calculate momentum score (0-100)"""
        if not data:
            return 50.0
        
        scores = []
        weights = []
        
        for timeframe, weight in self.timeframe_weights.items():
            if timeframe not in data:
                continue
            
            df = data[timeframe]
            if df.empty:
                continue
            
            # Latest values
            latest = df.iloc[-1]
            
            # 1. RSI score (30-70 ideal)
            rsi = latest.get('rsi_14', 50)
            if 30 <= rsi <= 70:
                rsi_score = 80  # Neutral range
            elif rsi < 30:
                # Oversold: higher score but not max (could continue down)
                rsi_score = 60 + (30 - rsi) * 0.67  # 60-80 range
            else:  # rsi > 70
                # Overbought: lower score
                rsi_score = 60 - (rsi - 70) * 0.67  # 40-60 range
            
            rsi_score = max(20, min(100, rsi_score))
            scores.append(rsi_score)
            weights.append(weight * 0.4)  # RSI weight within momentum
            
            # 2. MACD score
            macd_hist = latest.get('macd_histogram', 0)
            macd_line = latest.get('macd_line', 0)
            macd_signal = latest.get('macd_signal', 0)
            
            # Positive histogram = bullish momentum
            if macd_hist > 0:
                macd_score = 60 + min(macd_hist * 100, 40)  # 60-100
            else:
                macd_score = 40 + max(macd_hist * 100, -40)  # 0-40
            
            # MACD line above signal = additional bullish
            if macd_line > macd_signal:
                macd_score += 10
            else:
                macd_score -= 10
            
            macd_score = max(0, min(100, macd_score))
            scores.append(macd_score)
            weights.append(weight * 0.3)  # MACD weight
            
            # 3. Price vs MA alignment
            price = latest.get('price', 0)
            sma_20 = latest.get('sma_20', price)
            sma_50 = latest.get('sma_50', price)
            sma_200 = latest.get('sma_200', price)
            
            # Convert to float, handle None
            try:
                price_f = float(price) if price is not None else 0
                sma_20_f = float(sma_20) if sma_20 is not None else price_f
                sma_50_f = float(sma_50) if sma_50 is not None else price_f
                sma_200_f = float(sma_200) if sma_200 is not None else price_f
            except (ValueError, TypeError):
                price_f = sma_20_f = sma_50_f = sma_200_f = 0
            
            ma_score = 50
            # Bullish alignment: price > sma20 > sma50 > sma200
            if price_f > sma_20_f and sma_20_f > sma_50_f and sma_50_f > sma_200_f:
                ma_score = 90
            # Bearish alignment: price < sma20 < sma50 < sma200
            elif price_f < sma_20_f and sma_20_f < sma_50_f and sma_50_f < sma_200_f:
                ma_score = 10
            # Mixed
            else:
                # Count how many MAs price is above
                above_count = sum([price_f > sma_20_f, price_f > sma_50_f, price_f > sma_200_f])
                ma_score = 30 + above_count * 20  # 30, 50, 70, 90
            
            scores.append(ma_score)
            weights.append(weight * 0.3)  # MA weight
        
        if not scores:
            return 50.0
        
        # Weighted average
        total_weight = sum(weights)
        if total_weight > 0:
            momentum_score = sum(s * w for s, w in zip(scores, weights)) / total_weight
        else:
            momentum_score = 50.0
        
        return max(0, min(100, momentum_score))
    
    def calculate_trend_score(self, data: Dict[str, pd.DataFrame]) -> float:
        """Calculate trend strength score (0-100)"""
        if not data:
            return 50.0
        
        scores = []
        weights = []
        
        for timeframe, weight in self.timeframe_weights.items():
            if timeframe not in data:
                continue
            
            df = data[timeframe]
            if df.empty:
                continue
            
            latest = df.iloc[-1]
            
            # 1. ADX trend strength
            adx = latest.get('adx_14', 0)
            # ADX > 25 = strong trend, > 50 = very strong
            if adx > 50:
                adx_score = 90
            elif adx > 25:
                adx_score = 70 + (adx - 25) * 0.8  # 70-90
            elif adx > 20:
                adx_score = 50 + (adx - 20) * 4  # 50-70
            else:
                adx_score = adx * 2.5  # 0-50
            
            adx_score = max(0, min(100, adx_score))
            scores.append(adx_score)
            weights.append(weight * 0.5)  # ADX weight within trend
            
            # 2. Moving average slopes (recent trend)
            slope_scores = []
            for ma in [20, 50, 200]:
                slope_col = f'sma_{ma}_slope'
                if slope_col in df.columns:
                    slope = df[slope_col].iloc[-1] if not df[slope_col].isna().iloc[-1] else 0
                    if slope > 0:
                        slope_score = 60 + min(slope * 1000, 40)  # 60-100
                    else:
                        slope_score = 40 + max(slope * 1000, -40)  # 0-40
                    slope_scores.append(slope_score)
            
            if slope_scores:
                avg_slope_score = np.mean(slope_scores)
                scores.append(avg_slope_score)
                weights.append(weight * 0.3)  # Slope weight
            
            # 3. Golden/Death cross
            sma_50 = latest.get('sma_50', 0)
            sma_200 = latest.get('sma_200', 0)
            
            # Convert to float safely
            try:
                sma_50_f = float(sma_50) if sma_50 is not None else 0
                sma_200_f = float(sma_200) if sma_200 is not None else 0
            except (ValueError, TypeError):
                sma_50_f = sma_200_f = 0
            
            if sma_50_f > 0 and sma_200_f > 0:
                if sma_50_f > sma_200_f:
                    cross_score = 80  # Golden cross bullish
                else:
                    cross_score = 20  # Death cross bearish
            else:
                cross_score = 50
            
            scores.append(cross_score)
            weights.append(weight * 0.2)  # Cross weight
        
        if not scores:
            return 50.0
        
        total_weight = sum(weights)
        if total_weight > 0:
            trend_score = sum(s * w for s, w in zip(scores, weights)) / total_weight
        else:
            trend_score = 50.0
        
        return max(0, min(100, trend_score))
    
    def calculate_volatility_score(self, data: Dict[str, pd.DataFrame]) -> float:
        """Calculate volatility score (lower volatility = higher score)"""
        if not data:
            return 50.0
        
        scores = []
        weights = []
        
        for timeframe, weight in self.timeframe_weights.items():
            if timeframe not in data:
                continue
            
            df = data[timeframe]
            if df.empty or len(df) < 10:
                continue
            
            latest = df.iloc[-1]
            price = latest.get('price', 1)
            
            # 1. ATR relative to price (lower = better for holding)
            atr = latest.get('atr_14', 0)
            if price > 0 and atr > 0:
                atr_ratio = atr / price
                # Convert to score: lower ratio = higher score
                atr_score = max(0, 100 - min(atr_ratio * 1000, 100))
            else:
                atr_score = 50
            
            scores.append(atr_score)
            weights.append(weight * 0.6)  # ATR weight
            
            # 2. Bollinger Band width (narrower bands = lower volatility)
            bb_width = latest.get('bb_width', 0)
            if bb_width > 0:
                # Historical BB width percentiles would be better
                # For now, assume narrower = better
                bb_score = max(0, 100 - min(bb_width * 100, 100))
            else:
                bb_score = 50
            
            scores.append(bb_score)
            weights.append(weight * 0.4)  # BB weight
        
        if not scores:
            return 50.0
        
        total_weight = sum(weights)
        if total_weight > 0:
            vol_score = sum(s * w for s, w in zip(scores, weights)) / total_weight
        else:
            vol_score = 50.0
        
        return max(0, min(100, vol_score))
    
    def calculate_volume_score(self, data: Dict[str, pd.DataFrame]) -> float:
        """Calculate volume score (higher/rising volume = higher score)"""
        if not data:
            return 50.0
        
        # Use daily timeframe for volume analysis
        if '1d' not in data:
            return 50.0
        
        df = data['1d']
        if df.empty or len(df) < 5:
            return 50.0
        
        latest = df.iloc[-1]
        
        # 1. Volume ratio (current vs average)
        volume_ratio = latest.get('volume_ratio', 1)
        if volume_ratio > 2:
            ratio_score = 90
        elif volume_ratio > 1.5:
            ratio_score = 80
        elif volume_ratio > 1:
            ratio_score = 70
        elif volume_ratio > 0.5:
            ratio_score = 40
        else:
            ratio_score = 20
        
        # 2. Volume trend (last 5 days)
        if 'volume' in df.columns and len(df) >= 5:
            volumes = df['volume'].tail(5).values
            if len(volumes) >= 2:
                # Simple trend: increasing = good
                volume_change = (volumes[-1] - volumes[0]) / volumes[0] if volumes[0] > 0 else 0
                if volume_change > 0.2:
                    trend_score = 90
                elif volume_change > 0.1:
                    trend_score = 80
                elif volume_change > 0:
                    trend_score = 70
                elif volume_change > -0.1:
                    trend_score = 50
                elif volume_change > -0.2:
                    trend_score = 30
                else:
                    trend_score = 10
            else:
                trend_score = 50
        else:
            trend_score = 50
        
        # 3. Volume-price confirmation
        price_returns = df['returns'].tail(5).values if 'returns' in df.columns else []
        if len(price_returns) >= 2 and len(volumes) >= 2:
            # Check if volume confirms price move
            recent_volume = volumes[-1]
            avg_volume = np.mean(volumes[:-1]) if len(volumes) > 1 else recent_volume
            recent_return = price_returns[-1] if len(price_returns) > 0 else 0
            
            if recent_return > 0 and recent_volume > avg_volume:
                confirmation_score = 90
            elif recent_return < 0 and recent_volume > avg_volume:
                confirmation_score = 30  # High volume on down move = bearish
            else:
                confirmation_score = 50
        else:
            confirmation_score = 50
        
        # Weighted average
        volume_score = (ratio_score * 0.4 + trend_score * 0.3 + confirmation_score * 0.3)
        return max(0, min(100, volume_score))
    
    def calculate_risk_adjusted_score(self, data: Dict[str, pd.DataFrame]) -> float:
        """Calculate risk-adjusted return score (0-100)"""
        if not data or '1d' not in data:
            return 50.0

        df = data['1d']
        if df.empty or len(df) < 10:
            return 50.0

        # Need returns data
        if 'returns' not in df.columns or df['returns'].isna().all():
            return 50.0

        returns = df['returns'].dropna()

        # Minimum data guards — metrics are meaningless with too few points
        MIN_FOR_SHARPE = 30
        MIN_FOR_VAR = 60
        MIN_FOR_DRAWDOWN = 30
        if len(returns) < MIN_FOR_SHARPE:
            logger.debug(f"Insufficient data for risk score: {len(returns)} < {MIN_FOR_SHARPE}")
            return 50.0
        
        # Calculate metrics
        try:
            # 1. Sharpe ratio (assuming 0 risk-free rate)
            mean_return = returns.mean()
            std_return = returns.std()
            
            if std_return > 0:
                sharpe_ratio = mean_return / std_return * np.sqrt(365)  # Annualized
                # Convert to score: higher Sharpe = higher score
                sharpe_score = 50 + min(sharpe_ratio * 10, 50)  # 0-100 range
                sharpe_score = max(0, min(100, sharpe_score))
            else:
                sharpe_score = 50
            
            # 2. Maximum drawdown
            cumulative = (1 + returns).cumprod()
            running_max = cumulative.expanding().max()
            drawdown = (cumulative - running_max) / running_max
            max_drawdown = drawdown.min() if not drawdown.empty else 0
            
            # Convert to score: smaller drawdown = higher score
            drawdown_score = 100 - min(abs(max_drawdown) * 200, 100)
            drawdown_score = max(0, min(100, drawdown_score))
            
            # 3. Value at Risk (95% confidence, 1-day)
            var_95 = np.percentile(returns, 5)  # 5th percentile
            # Convert to score: smaller (less negative) VaR = higher score
            var_score = 100 - min(abs(var_95) * 1000, 100)
            var_score = max(0, min(100, var_score))
            
            # Weighted average
            risk_score = (sharpe_score * 0.4 + drawdown_score * 0.4 + var_score * 0.2)
            return max(0, min(100, risk_score))
            
        except Exception as e:
            logger.warning(f"Error calculating risk-adjusted score: {e}")
            return 50.0
    
    def calculate_total_score(self, symbol: str) -> Dict[str, float]:
        """Calculate total advanced score for a symbol"""
        data = self.get_symbol_data(symbol)
        
        if not data:
            logger.warning(f"No data for symbol {symbol}")
            return {
                'total_score': 50.0,
                'momentum': 50.0,
                'trend': 50.0,
                'volatility': 50.0,
                'volume': 50.0,
                'risk_adjusted': 50.0
            }
        
        # Calculate component scores
        momentum = self.calculate_momentum_score(data)
        trend = self.calculate_trend_score(data)
        volatility = self.calculate_volatility_score(data)
        volume = self.calculate_volume_score(data)
        risk_adjusted = self.calculate_risk_adjusted_score(data)
        
        # Calculate weighted total
        total_score = (
            momentum * self.component_weights['momentum'] +
            trend * self.component_weights['trend'] +
            volatility * self.component_weights['volatility'] +
            volume * self.component_weights['volume'] +
            risk_adjusted * self.component_weights['risk_adjusted']
        )
        
        return {
            'total_score': round(total_score, 1),
            'momentum': round(momentum, 1),
            'trend': round(trend, 1),
            'volatility': round(volatility, 1),
            'volume': round(volume, 1),
            'risk_adjusted': round(risk_adjusted, 1)
        }
    
    def score_multiple_symbols(self, symbols: List[str]) -> pd.DataFrame:
        """Score multiple symbols and return DataFrame"""
        results = []
        for symbol in symbols:
            try:
                scores = self.calculate_total_score(symbol)
                scores['symbol'] = symbol
                results.append(scores)
            except Exception as e:
                logger.error(f"Error scoring {symbol}: {e}")
        
        if results:
            df = pd.DataFrame(results)
            df = df[['symbol', 'total_score', 'momentum', 'trend', 'volatility', 'volume', 'risk_adjusted']]
            df.sort_values('total_score', ascending=False, inplace=True)
            return df
        else:
            return pd.DataFrame()


def test_scoring():
    """Test the advanced scoring model"""
    print("🧪 Testing Advanced Scoring Model")
    print("=" * 60)
    
    scorer = AdvancedScoringModel("crypto_data.db")
    
    # Test with a few symbols
    test_symbols = ["BTC-USD", "ETH-USD", "XRP-USD", "ADA-USD", "SOL-USD"]
    
    for symbol in test_symbols:
        print(f"\n📊 Scoring {symbol}:")
        scores = scorer.calculate_total_score(symbol)
        for key, value in scores.items():
            if key != 'symbol':
                print(f"  {key}: {value}")
    
    # Score multiple symbols
    print("\n" + "=" * 60)
    print("🏆 Top 5 symbols by total score:")
    
    df = scorer.score_multiple_symbols(test_symbols)
    if not df.empty:
        print(df.to_string(index=False))
    
    return df


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    
    if len(sys.argv) > 1 and sys.argv[1] == 'test':
        test_scoring()
    else:
        print("Advanced Scoring Model")
        print("Usage: python3 advanced_scorer.py test")