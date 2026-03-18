#!/usr/bin/env python3
"""
Test Coinbase WebSocket connection.
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
        async with websockets.connect(ws_url) as websocket:
            # Subscribe
            subscription = {
                "type": "subscribe",
                "product_ids": symbols,
                "channels": ["ticker"]
            }
            await websocket.send(json.dumps(subscription))
            logger.info(f"Subscribed to {symbols}")
            
            # Receive a few messages
            for i in range(10):
                message = await websocket.recv()
                data = json.loads(message)
                
                if data.get("type") == "ticker":
                    print(f"Tick {i+1}: {data['product_id']} ${data['price']} at {data.get('time', 'N/A')}")
                elif data.get("type") == "subscriptions":
                    print(f"Subscription confirmed: {data}")
                else:
                    print(f"Other message: {data.get('type')}")
                
    except Exception as e:
        logger.error(f"WebSocket error: {e}")

if __name__ == "__main__":
    asyncio.run(test_websocket())