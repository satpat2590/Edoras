#!/usr/bin/env python3
"""
Test the crypto data collector
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
    print("Testing CryptoDataCollector...")
    
    # Initialize collector
    collector = CryptoDataCollector(
        db_path="test_crypto_data.db",
        api_key=api_key,
        api_secret=api_secret
    )
    
    print("1. Getting portfolio symbols...")
    symbols = collector.get_portfolio_symbols()
    print(f"   Symbols: {symbols[:5]}... (total: {len(symbols)})")
    
    print("\n2. Testing backfill for BTC-USD (1d timeframe)...")
    saved = collector.backfill_symbol("BTC-USD", timeframe='1d', days_back=30)
    print(f"   Saved {saved} candles")
    
    print("\n3. Calculating indicators...")
    indicators = collector.calculate_indicators("BTC-USD", '1d')
    print(f"   Calculated {indicators} indicator rows")
    
    print("\n4. Running portfolio analysis...")
    collector.analyze_portfolio_signals()
    print("   Analysis completed")
    
    print("\n5. Generating report...")
    report = collector.generate_portfolio_report()
    print("   Report generated")
    
    # Show first few lines of report
    print("\n" + "="*60)
    lines = report.split('\n')[:20]
    for line in lines:
        print(line)
    print("..." if len(report.split('\n')) > 20 else "")
    print("="*60)
    
    print("\n✅ Data collector test completed successfully!")
    
    # Clean up test database
    import os
    if os.path.exists("test_crypto_data.db"):
        os.remove("test_crypto_data.db")
        print("Test database cleaned up")
    
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)