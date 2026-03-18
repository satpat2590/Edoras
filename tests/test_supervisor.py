#!/usr/bin/env python3
"""
Test the real-time supervisor.
"""

import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from realtime.supervisor import RealTimeSupervisor

async def test_supervisor():
    """Test supervisor for 30 seconds"""
    supervisor = RealTimeSupervisor()
    
    print("Starting supervisor test (30 seconds)...")
    print("Press Ctrl+C to stop early")
    
    try:
        # Run for 30 seconds
        await asyncio.wait_for(supervisor.run(), timeout=30)
    except asyncio.TimeoutError:
        print("Test completed after 30 seconds")
    except KeyboardInterrupt:
        print("Interrupted by user")
    
    print("Supervisor test complete")

if __name__ == "__main__":
    asyncio.run(test_supervisor())