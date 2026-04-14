#!/usr/bin/env python3
"""
Daily data collection and technical analysis pipeline.
Runs full data collection, calculates indicators, generates analysis report.
"""

import os
import sys
import logging
from datetime import datetime
from data.crypto_data_collector import CryptoDataCollector
from config import DB_PATH

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler("logs/data_collection.log"), logging.StreamHandler()],
)
logger = logging.getLogger(__name__)


def send_via_telegram(report_text: str, target: str = None) -> bool:
    """Send report via Telegram Bot API"""
    target = target or os.environ.get("TELEGRAM_CHAT_ID", "1806720995")
    try:
        # Truncate if too long (Telegram limit ~4096 chars)
        if len(report_text) > 4000:
            report_text = report_text[:3997] + "..."

        import urllib.request
        import urllib.parse

        token = os.getenv(
            "TELEGRAM_BOT_TOKEN", "8724014451:AAGpisAWj86i8qmkOtfb4mCBSpiPfZd0ROI"
        )
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode({"chat_id": target, "text": report_text}).encode()
        req = urllib.request.Request(url, data=data, method="POST")
        resp = urllib.request.urlopen(req, timeout=30)

        if resp.status == 200:
            logger.info(
                f"✅ Report sent to Telegram at {datetime.now().strftime('%H:%M:%S')}"
            )
            return True
        else:
            logger.error(f"❌ Error sending to Telegram: HTTP {resp.status}")
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
        logger.error(
            "ERROR: COINBASE_API_KEY and COINBASE_API_SECRET environment variables not set"
        )
        sys.exit(1)

    try:
        # Initialize collector
        collector = CryptoDataCollector(
            db_path=DB_PATH, api_key=api_key, api_secret=api_secret
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
            cursor.execute(
                """
            SELECT COUNT(*) FROM candlesticks WHERE symbol = ? AND timeframe = '1d'
            """,
                (symbol,),
            )
            count = cursor.fetchone()[0]
            conn.close()

            if count < 50:  # Not enough data
                logger.info(f"  Backfilling {symbol} (1d) for {backfill_days} days...")
                try:
                    saved = collector.backfill_symbol(
                        symbol, timeframe="1d", days_back=backfill_days
                    )
                    logger.info(f"    Saved {saved} daily candles")
                except Exception as e:
                    logger.warning(f"    Error backfilling {symbol}: {e}")

        # Step 3: Update latest data for all symbols and timeframes
        logger.info("Step 3: Updating latest data...")
        api_timeframes = ["1d", "1h"]  # fetch from Coinbase API

        for symbol in symbols:
            for timeframe in api_timeframes:
                try:
                    logger.info(f"  Updating {symbol} {timeframe}...")
                    saved = collector.update_latest_data(
                        symbol, timeframe, lookback_days=7
                    )
                    if saved > 0:
                        logger.info(f"    Added {saved} new candles")
                except Exception as e:
                    logger.warning(f"    Error updating {symbol} {timeframe}: {e}")

        # Step 3b: Aggregate 1h → true 4h candles
        logger.info("Step 3b: Aggregating 1h → 4h candles...")
        for symbol in symbols:
            try:
                agg = collector.aggregate_4h_candles(symbol, lookback_days=14)
                if agg > 0:
                    logger.info(f"  Aggregated {agg} 4h candles for {symbol}")
            except Exception as e:
                logger.warning(f"  Error aggregating 4h for {symbol}: {e}")

        # Step 4: Calculate technical indicators
        logger.info("Step 4: Calculating technical indicators...")
        all_timeframes = ["1d", "4h", "1h"]
        for symbol in symbols:
            for timeframe in all_timeframes:
                try:
                    indicators = collector.calculate_indicators(symbol, timeframe)
                    if indicators > 0:
                        logger.info(
                            f"  Calculated {indicators} indicator rows for {symbol} {timeframe}"
                        )
                except Exception as e:
                    logger.warning(
                        f"  Error calculating indicators for {symbol} {timeframe}: {e}"
                    )

        # Step 5: Run portfolio analysis
        logger.info("Step 5: Running portfolio analysis...")
        collector.analyze_portfolio_signals()

        # Step 6: Generate report
        logger.info("Step 6: Generating technical analysis report...")
        report = collector.generate_portfolio_report()

        # Save report to file
        report_filename = f"technical_analysis_{datetime.now().strftime('%Y%m%d')}.txt"
        with open(report_filename, "w") as f:
            f.write(report)
        logger.info(f"Report saved to {report_filename}")

        # Step 7: Send report via Telegram (optional)
        logger.info("Step 7: Sending report to Telegram...")
        send_success = send_via_telegram(report)

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
        lines = report.split("\n")[:30]
        for line in lines:
            print(line)

        if len(report.split("\n")) > 30:
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
