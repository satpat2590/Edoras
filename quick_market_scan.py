#!/usr/bin/env python3
"""Quick market scan of all crypto symbols for promising opportunities."""

import sys
import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

sys.path.insert(0, '.')

from config import TOP_CRYPTO_SYMBOLS, DB_PATH

def get_latest_indicators(symbol, timeframe='1d'):
    """Get latest indicators for a symbol."""
    conn = sqlite3.connect(DB_PATH)
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
    LIMIT 1
    '''
    df = pd.read_sql_query(query, conn, params=(symbol, timeframe))
    conn.close()
    if df.empty:
        return None
    return df.iloc[0].to_dict()

def compute_simple_score(ind):
    """Compute a simple score (0-100) based on multiple factors."""
    if ind is None:
        return 0.0
    
    score = 50.0  # neutral
    
    # 1. RSI momentum (30-70 range ideal)
    rsi = ind.get('rsi_14')
    if rsi is not None:
        if rsi < 30:
            # oversold: higher score the more oversold
            score += (30 - rsi) * 2.0  # up to +60
        elif rsi > 70:
            # overbought: penalize
            score -= (rsi - 70) * 2.0
        else:
            # in middle range: prefer 40-60 (neutral bullish)
            if 40 <= rsi <= 60:
                score += 10.0
    
    # 2. MACD histogram strength
    macd_hist = ind.get('macd_histogram')
    if macd_hist is not None:
        # Positive histogram bullish, negative bearish
        score += macd_hist * 1000  # scale factor
    
    # 3. Trend: price vs SMAs
    price = ind.get('price')
    sma20 = ind.get('sma_20')
    sma50 = ind.get('sma_50')
    sma200 = ind.get('sma_200')
    
    if price is not None and sma20 is not None:
        if price > sma20:
            score += 5.0
    if price is not None and sma50 is not None:
        if price > sma50:
            score += 8.0
    if price is not None and sma200 is not None:
        if price > sma200:
            score += 12.0
    
    # 4. ADX trend strength
    adx = ind.get('adx_14')
    if adx is not None and adx > 25:
        score += (adx - 25) * 0.5
    
    # 5. Volume ratio
    vol_ratio = ind.get('volume_ratio')
    if vol_ratio is not None and vol_ratio > 1.0:
        score += (vol_ratio - 1.0) * 10.0
    
    # 6. Volatility (BB width) - moderate is good
    bb_width = ind.get('bb_width')
    if bb_width is not None:
        if 0.02 < bb_width < 0.10:
            score += 5.0
    
    # Clamp to reasonable range
    return max(0.0, min(100.0, score))

def analyze_symbol(symbol):
    """Analyze a symbol and return dict of metrics."""
    ind_daily = get_latest_indicators(symbol, '1d')
    ind_4h = get_latest_indicators(symbol, '4h')
    
    if ind_daily is None:
        return None
    
    score_daily = compute_simple_score(ind_daily)
    score_4h = compute_simple_score(ind_4h) if ind_4h else 50.0
    
    # Composite score weighted 70% daily, 30% 4h
    composite = score_daily * 0.7 + score_4h * 0.3
    
    # Determine signal
    signal = "NEUTRAL"
    rsi = ind_daily.get('rsi_14')
    macd_hist = ind_daily.get('macd_histogram')
    
    if rsi is not None and macd_hist is not None:
        if rsi < 35 and macd_hist > 0:
            signal = "BULLISH"
        elif rsi > 65 and macd_hist < 0:
            signal = "BEARISH"
    
    return {
        'symbol': symbol,
        'price': ind_daily.get('price'),
        'rsi': rsi,
        'macd_hist': macd_hist,
        'sma_20': ind_daily.get('sma_20'),
        'sma_50': ind_daily.get('sma_50'),
        'sma_200': ind_daily.get('sma_200'),
        'adx': ind_daily.get('adx_14'),
        'volume_ratio': ind_daily.get('volume_ratio'),
        'score_daily': round(score_daily, 1),
        'score_4h': round(score_4h, 1) if ind_4h else None,
        'composite_score': round(composite, 1),
        'signal': signal,
        'timestamp': ind_daily.get('timestamp'),
    }

def main():
    print("🔍 Scanning crypto market for promising coins...\n")
    
    results = []
    for symbol in TOP_CRYPTO_SYMBOLS:
        analysis = analyze_symbol(symbol)
        if analysis:
            results.append(analysis)
    
    # Sort by composite score descending
    results.sort(key=lambda x: x['composite_score'], reverse=True)
    
    # Print top 10
    print("🏆 **Top 10 Promising Coins (Daily + 4h Composite Score)**\n")
    print(f"{'Symbol':<10} {'Price':<12} {'RSI':<6} {'MACD Hist':<10} {'Score':<6} {'Signal':<10}")
    print("-" * 60)
    
    for r in results[:10]:
        price_str = f"${r['price']:,.2f}" if r['price'] else "N/A"
        rsi_str = f"{r['rsi']:.1f}" if r['rsi'] else "N/A"
        macd_str = f"{r['macd_hist']:.4f}" if r['macd_hist'] else "N/A"
        print(f"{r['symbol']:<10} {price_str:<12} {rsi_str:<6} {macd_str:<10} {r['composite_score']:<6.1f} {r['signal']:<10}")
    
    # Additional insights
    print("\n📊 **Market Insights**")
    
    # Count bullish signals
    bullish = sum(1 for r in results if r['signal'] == 'BULLISH')
    bearish = sum(1 for r in results if r['signal'] == 'BEARISH')
    neutral = len(results) - bullish - bearish
    
    print(f"• Bullish signals: {bullish}/{len(results)}")
    print(f"• Bearish signals: {bearish}/{len(results)}")
    print(f"• Neutral: {neutral}/{len(results)}")
    
    # Check for oversold/overbought extremes
    oversold = [r for r in results if r['rsi'] and r['rsi'] < 30]
    overbought = [r for r in results if r['rsi'] and r['rsi'] > 70]
    
    if oversold:
        print(f"• Oversold (RSI < 30): {', '.join([r['symbol'] for r in oversold])}")
    if overbought:
        print(f"• Overbought (RSI > 70): {', '.join([r['symbol'] for r in overbought])}")
    
    # Check price above SMA200 (long-term trend)
    above_sma200 = [r for r in results if r['price'] and r['sma_200'] and r['price'] > r['sma_200']]
    print(f"• Trading above SMA200: {len(above_sma200)}/{len(results)}")
    
    # Top 3 recommendations
    print("\n🎯 **Top 3 Recommendations for Portfolio Addition**")
    for i, r in enumerate(results[:3], 1):
        reason = []
        if r['rsi'] and r['rsi'] < 35:
            reason.append(f"RSI {r['rsi']:.1f} (near oversold)")
        if r['macd_hist'] and r['macd_hist'] > 0:
            reason.append("MACD bullish")
        if r['price'] and r['sma_200'] and r['price'] > r['sma_200']:
            reason.append("above SMA200")
        if r['adx'] and r['adx'] > 25:
            reason.append(f"strong trend (ADX {r['adx']:.1f})")
        
        reason_str = ", ".join(reason) if reason else "neutral metrics"
        print(f"{i}. **{r['symbol']}** (Score: {r['composite_score']:.1f}) - {reason_str}")
    
    # Data freshness
    if results:
        latest_ts = max(r['timestamp'] for r in results if r['timestamp'])
        age = datetime.now().timestamp() - latest_ts
        if age > 86400:
            print(f"\n⚠️  Data may be stale: latest timestamp {age/3600:.1f}h old")
        else:
            print(f"\n✅ Data fresh: latest timestamp {age/3600:.1f}h old")

if __name__ == '__main__':
    main()