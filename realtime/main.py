#!/usr/bin/env python3
"""
Main real-time trading service.
Orchestrates WebSocket ingestion, risk management, and portfolio tracking.
"""

import asyncio
import signal
import logging
from datetime import datetime

from ingest.coinbase_websocket import CoinbaseWebSocketClient
import config

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class RealTimeService:
    """Main service orchestrating real-time components"""
    
    def __init__(self):
        self.websocket_client = None
        self.running = False
        
    async def start(self):
        """Start all components"""
        logger.info("Starting RealTimeService...")
        self.running = True
        
        # Start WebSocket client
        self.websocket_client = CoinbaseWebSocketClient(
            symbols=config.CRYPTO_SYMBOLS[:2],  # Start with BTC-USD and ETH-USD
            db_path=config.DB_PATH,
            heartbeat_interval=config.WEBSOCKET_HEARTBEAT_INTERVAL
        )
        
        # Register signal handlers
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self.stop)
        
        # Start WebSocket connection
        await self.websocket_client.connect()
    
    def stop(self):
        """Graceful shutdown"""
        logger.info("Shutting down RealTimeService...")
        self.running = False
        
        if self.websocket_client:
            asyncio.create_task(self.websocket_client.disconnect())
    
    async def run(self):
        """Main service loop"""
        try:
            await self.start()
            
            # Keep service running
            while self.running:
                await asyncio.sleep(1)
                
                # Periodic tasks (every 10 seconds)
                # In production, this would trigger risk checks, portfolio updates, etc.
                
        except KeyboardInterrupt:
            logger.info("Received keyboard interrupt")
        except Exception as e:
            logger.error(f"Service crashed: {e}", exc_info=True)
        finally:
            self.stop()
            await asyncio.sleep(1)  # Allow cleanup

async def main():
    """Entry point"""
    service = RealTimeService()
    await service.run()

if __name__ == "__main__":
    asyncio.run(main())