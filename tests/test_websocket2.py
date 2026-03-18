#!/usr/bin/env python3
"""
Test Coinbase WebSocket connection with timeout.
"""

import asyncio
import json
import websockets
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def test_websocket():
    """Test WebSocket connection and print a few ticks"""
    symbols = ["BTC-USD"]
    ws_url = "wss://ws-feed.exchange.coinbase.com"
    
    try:
        async with websockets.connect(ws_url, ping_timeout=10) as websocket:
            # Subscribe
            subscription = {
                "type": "subscribe",
                "product_ids": symbols,
                "channels": ["ticker"]
            }
            await websocket.send(json.dumps(subscription))
            logger.info(f"Subscribed to {symbols}")
            
            # Receive messages for 3 seconds
            start_time = asyncio.get_event_loop().time()
            tick_count = 0
            
            while asyncio.get_event_loop().time() - start_time < 3:
                try:
                    message = await asyncio.wait_for(websocket.recv(), timeout=1)
                    data = json.loads(message)
                    
                    if data.get("type") == "ticker":
                        tick_count += 1
                        print(f"Tick {tick_count}: {data['product_id']} ${data['price']}")
                    elif data.get("type") == "subscriptions":
                        print(f"Subscription confirmed")
                    elif data.get("type") == "heartbeat":
                        pass  # Ignore heartbeats
                    else:
                        print(f"Other: {data.get('type')}")
                        
                except asyncio.TimeoutError:
                    continue
                    
            logger.info(f"Received {tick_count} ticks in 3 seconds")
            
    except Exception as e:
        logger.error(f"WebSocket error: {e}")

if __name__ == "__main__":
    asyncio.run(test_websocket())