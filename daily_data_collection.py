#!/usr/bin/env python3
"""
Daily data collection and technical analysis pipeline.
Runs full data collection, calculates indicators, generates analysis report.
"""

import os
import sys
import logging
from datetime import datetime
from crypto_data_collector import CryptoDataCollector

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('data_collection.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def send_via_openclaw(report_text: str, target: str = None) -> bool:
    target = target or os.environ.get("TELEGRAM_CHAT_ID", "")
    """Send report via OpenClaw message command"""
    try:
        # Truncate if too long (Telegram limit ~4096 chars)
        if len(report_text) > 4000:
            report_text = report_text[:3997] + "..."
        
        # Use OpenClaw CLI to send message
        import subprocess
        
        # Set up environment for nvm
        env = os.environ.copy()
        nvm_dir = os.path.expanduser("~/.nvm")
        if os.path.exists(f"{nvm_dir}/nvm.sh"):
            # Try to source nvm
            import subprocess
            cmd = f'source {nvm_dir}/nvm.sh && nvm use default && which node'
            result = subprocess.run(cmd, shell=True, executable='/bin/bash', 
                                  capture_output=True, text=True)
            if result.returncode == 0:
                # Get node path and update PATH
                node_path = result.stdout.strip()
                if node_path:
                    node_bin_dir = os.path.dirname(node_path)
                    env['PATH'] = f"{node_bin_dir}:{env['PATH']}"
        
        cmd = ["openclaw", "message", "send", "--target", target, "--message", report_text]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, env=env)
        
        if result.returncode == 0:
            logger.info(f"✅ Report sent to Telegram at {datetime.now().strftime('%H:%M:%S')}")
            return True
        else:
            logger.error(f"❌ Error sending to Telegram: {result.stderr[:100]}")
            return False
            
    except subprocess.TimeoutExpired:
        logger.error("❌ Timeout sending to Telegram")
        return False
    except Exception as e:
        logger.error(f"❌ Exception sending to Telegram: {e}")
        return False

def main():
    """Main execution function"""
    logger.info("=" * 60)
    logger.info("Starting daily crypto data collection pipeline")
    logger.info(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)
    
    # Get credentials from environment
    api_key = os.getenv("COINBASE_API_KEY")
    api_secret = os.getenv("COINBASE_API_SECRET")
    
    if not api_key or not api_secret:
        logger.error("ERROR: COINBASE_API_KEY and COINBASE_API_SECRET environment variables not set")
        sys.exit(1)
    
    try:
        # Initialize collector
        collector = CryptoDataCollector(
            db_path="crypto_data.db",
            api_key=api_key,
            api_secret=api_secret
        )
        
        # Step 1: Get portfolio symbols
        logger.info("Step 1: Getting portfolio symbols...")
        symbols = collector.get_portfolio_symbols()
        logger.info(f"Found {len(symbols)} symbols: {', '.join(symbols[:5])}...")
        
        # Step 2: Backfill historical data for new symbols
        logger.info("Step 2: Backfilling historical data...")
        backfill_days = 200  # Enough for SMA 200
        
        for symbol in symbols:
            # Check if we have data for this symbol
            conn = collector._get_connection()
            cursor = conn.cursor()
            cursor.execute('''
            SELECT COUNT(*) FROM candlesticks WHERE symbol = ? AND timeframe = '1d'
            ''', (symbol,))
            count = cursor.fetchone()[0]
            conn.close()
            
            if count < 50:  # Not enough data
                logger.info(f"  Backfilling {symbol} (1d) for {backfill_days} days...")
                try:
                    saved = collector.backfill_symbol(symbol, timeframe='1d', days_back=backfill_days)
                    logger.info(f"    Saved {saved} daily candles")
                except Exception as e:
                    logger.warning(f"    Error backfilling {symbol}: {e}")
        
        # Step 3: Update latest data for all symbols and timeframes
        logger.info("Step 3: Updating latest data...")
        timeframes = ['1d', '4h', '1h']
        
        for symbol in symbols:
            for timeframe in timeframes:
                try:
                    logger.info(f"  Updating {symbol} {timeframe}...")
                    saved = collector.update_latest_data(symbol, timeframe, lookback_days=7)
                    if saved > 0:
                        logger.info(f"    Added {saved} new candles")
                except Exception as e:
                    logger.warning(f"    Error updating {symbol} {timeframe}: {e}")
        
        # Step 4: Calculate technical indicators
        logger.info("Step 4: Calculating technical indicators...")
        for symbol in symbols:
            for timeframe in timeframes:
                try:
                    indicators = collector.calculate_indicators(symbol, timeframe)
                    if indicators > 0:
                        logger.info(f"  Calculated {indicators} indicator rows for {symbol} {timeframe}")
                except Exception as e:
                    logger.warning(f"  Error calculating indicators for {symbol} {timeframe}: {e}")
        
        # Step 5: Run portfolio analysis
        logger.info("Step 5: Running portfolio analysis...")
        collector.analyze_portfolio_signals()
        
        # Step 6: Generate report
        logger.info("Step 6: Generating technical analysis report...")
        report = collector.generate_portfolio_report()
        
        # Save report to file
        report_filename = f"technical_analysis_{datetime.now().strftime('%Y%m%d')}.txt"
        with open(report_filename, 'w') as f:
            f.write(report)
        logger.info(f"Report saved to {report_filename}")
        
        # Step 7: Send report via Telegram (optional)
        logger.info("Step 7: Sending report to Telegram...")
        send_success = send_via_openclaw(report)
        
        # Step 8: Log completion
        logger.info("=" * 60)
        logger.info("Daily data collection pipeline COMPLETED")
        logger.info(f"Report sent: {'✅ SUCCESS' if send_success else '❌ FAILED'}")
        logger.info("=" * 60)
        
        # Print report summary
        print("\n" + "=" * 60)
        print("TECHNICAL ANALYSIS REPORT SUMMARY")
        print("=" * 60)
        
        # Show first 30 lines of report
        lines = report.split('\n')[:30]
        for line in lines:
            print(line)
        
        if len(report.split('\n')) > 30:
            print("... (full report saved to file and sent to Telegram)")
        
        print("=" * 60)
        
        return 0 if send_success else 1
        
    except Exception as e:
        logger.error(f"❌ Fatal error in data collection pipeline: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    sys.exit(main())