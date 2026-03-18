#!/usr/bin/env python3
"""
Test daily data collection with smaller backfill
"""

import os
import sys

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from crypto_data_collector import CryptoDataCollector

# Get credentials from environment
api_key = os.getenv("COINBASE_API_KEY")
api_secret = os.getenv("COINBASE_API_SECRET")

if not api_key or not api_secret:
    print("ERROR: COINBASE_API_KEY and COINBASE_API_SECRET environment variables not set")
    sys.exit(1)

try:
    print("Testing Daily Data Collection Pipeline...")
    print("=" * 60)
    
    # Initialize collector with test database
    collector = CryptoDataCollector(
        db_path="test_daily_data.db",
        api_key=api_key,
        api_secret=api_secret
    )
    
    # Get portfolio symbols
    print("1. Getting portfolio symbols...")
    symbols = collector.get_portfolio_symbols()
    print(f"   Found {len(symbols)} symbols")
    
    # Test with just BTC-USD and ETH-USD for speed
    test_symbols = ["BTC-USD", "ETH-USD"]
    
    # Backfill 60 days (enough for SMA 50)
    print("\n2. Backfilling test data (60 days)...")
    for symbol in test_symbols:
        print(f"   Backfilling {symbol}...")
        saved = collector.backfill_symbol(symbol, timeframe='1d', days_back=60)
        print(f"     Saved {saved} daily candles")
    
    # Update latest data
    print("\n3. Updating latest data...")
    for symbol in test_symbols:
        for timeframe in ['1d', '4h']:
            print(f"   Updating {symbol} {timeframe}...")
            saved = collector.update_latest_data(symbol, timeframe, lookback_days=7)
            if saved > 0:
                print(f"     Added {saved} new candles")
    
    # Calculate indicators
    print("\n4. Calculating indicators...")
    for symbol in test_symbols:
        for timeframe in ['1d', '4h']:
            print(f"   Calculating indicators for {symbol} {timeframe}...")
            indicators = collector.calculate_indicators(symbol, timeframe)
            print(f"     Calculated {indicators} indicator rows")
    
    # Run portfolio analysis
    print("\n5. Running portfolio analysis...")
    collector.analyze_portfolio_signals()
    print("   Analysis completed")
    
    # Generate report
    print("\n6. Generating report...")
    report = collector.generate_portfolio_report()
    
    # Show report
    print("\n" + "=" * 60)
    print("TECHNICAL ANALYSIS REPORT")
    print("=" * 60)
    print(report[:2000] + "..." if len(report) > 2000 else report)
    print("=" * 60)
    
    print("\n✅ Daily data collection test completed successfully!")
    
    # Clean up test database
    import os
    if os.path.exists("test_daily_data.db"):
        os.remove("test_daily_data.db")
        print("Test database cleaned up")
    
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)