#!/usr/bin/env python3
"""
Minimal test of crypto data collector
"""

import os
import sys
import logging

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

print("Testing Crypto Data Collector...")
print("=" * 50)

# Check environment variables
api_key = os.getenv("COINBASE_API_KEY")
api_secret = os.getenv("COINBASE_API_SECRET")

if not api_key or not api_secret:
    print("❌ ERROR: COINBASE_API_KEY and COINBASE_API_SECRET not set")
    print("Check your .zshrc file or set them manually")
    sys.exit(1)

print("✅ Coinbase credentials found")

try:
    # Import and test
    from crypto_data_collector import CryptoDataCollector
    
    print("\n1. Initializing collector...")
    collector = CryptoDataCollector(
        db_path="test_crypto_data_minimal.db",
        api_key=api_key,
        api_secret=api_secret
    )
    print("✅ Collector initialized")
    
    print("\n2. Getting portfolio symbols...")
    symbols = collector.get_portfolio_symbols()
    print(f"✅ Found {len(symbols)} symbols: {symbols[:5]}...")
    
    print("\n3. Testing BTC-USD backfill (7 days)...")
    # Test with just 7 days to avoid API limits
    saved = collector.backfill_symbol("BTC-USD", timeframe='1d', days_back=7)
    print(f"✅ Backfilled {saved} daily candles for BTC-USD")
    
    print("\n4. Calculating indicators...")
    indicators = collector.calculate_indicators("BTC-USD", '1d')
    print(f"✅ Calculated {indicators} indicator rows")
    
    print("\n5. Running portfolio analysis...")
    collector.analyze_portfolio_signals()
    print("✅ Portfolio analysis completed")
    
    print("\n6. Generating report...")
    report = collector.generate_portfolio_report()
    print("✅ Report generated")
    
    # Show first 15 lines of report
    print("\n" + "=" * 60)
    lines = report.split('\n')[:15]
    for line in lines:
        print(line)
    print("..." if len(report.split('\n')) > 15 else "")
    print("=" * 60)
    
    # Clean up test database
    import os
    if os.path.exists("test_crypto_data_minimal.db"):
        os.remove("test_crypto_data_minimal.db")
        print("\n✅ Test database cleaned up")
    
    print("\n" + "=" * 50)
    print("🎉 MINIMAL TEST COMPLETED SUCCESSFULLY!")
    print("=" * 50)
    
except ImportError as e:
    print(f"❌ Import error: {e}")
    print("Make sure crypto_data_collector.py is in the same directory")
    sys.exit(1)
except Exception as e:
    print(f"❌ Error during test: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)