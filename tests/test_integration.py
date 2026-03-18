#!/usr/bin/env python3
"""
Test integration of WebSocket client and database.
"""

import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from realtime.ingest.coinbase_websocket import CoinbaseWebSocketClient
import config

async def test_integration():
    """Test WebSocket client with database storage"""
    symbols = ["BTC-USD"]  # Start with just BTC
    
    client = CoinbaseWebSocketClient(
        symbols=symbols,
        db_path=config.DB_PATH
    )
    
    print("Starting WebSocket client...")
    print("Press Ctrl+C to stop after 10 seconds")
    
    try:
        # Run for 10 seconds
        await asyncio.wait_for(client.connect(), timeout=10)
    except asyncio.TimeoutError:
        print("Test completed after 10 seconds")
    except KeyboardInterrupt:
        print("Interrupted by user")
    finally:
        await client.disconnect()
        print("Client disconnected")
        
        # Check database
        import sqlite3
        conn = sqlite3.connect(config.DB_PATH)
        cursor = conn.cursor()
        
        # Count ticks
        cursor.execute("SELECT COUNT(*) FROM ticks WHERE symbol = 'BTC-USD'")
        tick_count = cursor.fetchone()[0]
        print(f"Stored {tick_count} ticks in database")
        
        # Show recent ticks
        cursor.execute("SELECT timestamp, price FROM ticks WHERE symbol = 'BTC-USD' ORDER BY timestamp DESC LIMIT 5")
        recent = cursor.fetchall()
        print("Recent ticks:")
        for ts, price in recent:
            print(f"  {ts}: ${price:.2f}")
        
        conn.close()

if __name__ == "__main__":
    asyncio.run(test_integration())