#!/usr/bin/env python3
"""
Signal-based alerts for crypto portfolio.
Checks technical indicators and sends Telegram alerts for significant signals.
"""

import os
import sys
import sqlite3
import logging
from datetime import datetime, timedelta
import json

from config import DB_PATH

# Try to import sentiment analyzer
try:
    from llm.sentiment import CryptoSentiment

    SENTIMENT_AVAILABLE = True
except ImportError:
    SENTIMENT_AVAILABLE = False

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


class SignalAlertSystem:
    """Detect and alert on technical signals"""

    def __init__(self, db_path: str = DB_PATH, use_sentiment: bool = False):
        """Initialize with database path"""
        self.db_path = db_path
        self.alerts_file = "signal_alerts_state.json"
        self.use_sentiment = use_sentiment
        self.sentiment_analyzer = None

        if use_sentiment and SENTIMENT_AVAILABLE:
            try:
                self.sentiment_analyzer = CryptoSentiment(db_path=db_path)
                logger.info("Sentiment analyzer initialized")
            except Exception as e:
                logger.warning(f"Failed to initialize sentiment analyzer: {e}")
                self.sentiment_analyzer = None
        elif use_sentiment and not SENTIMENT_AVAILABLE:
            logger.warning(
                "Sentiment module not available. Install feedparser and openai."
            )

        self.load_alert_state()

    def load_alert_state(self):
        """Load previous alert state to avoid duplicates"""
        self.alert_state = {}
        try:
            if os.path.exists(self.alerts_file):
                with open(self.alerts_file, "r") as f:
                    self.alert_state = json.load(f)
        except Exception as e:
            logger.warning(f"Could not load alert state: {e}")
            self.alert_state = {}

    def save_alert_state(self):
        """Save current alert state"""
        try:
            with open(self.alerts_file, "w") as f:
                json.dump(self.alert_state, f, indent=2)
        except Exception as e:
            logger.warning(f"Could not save alert state: {e}")

    def get_latest_indicators(self, symbol: str, timeframe: str = "1d"):
        """Get latest indicators for a symbol/timeframe"""
        conn = sqlite3.connect(self.db_path)
        query = """
        SELECT i.timestamp, i.rsi_14, i.macd_histogram, c.close
        FROM indicators i 
        JOIN candlesticks c ON i.symbol = c.symbol 
            AND i.timeframe = c.timeframe 
            AND i.timestamp = c.timestamp
        WHERE i.symbol = ? AND i.timeframe = ?
        ORDER BY i.timestamp DESC LIMIT 1
        """

        cursor = conn.cursor()
        cursor.execute(query, (symbol, timeframe))
        row = cursor.fetchone()
        conn.close()

        if row:
            return {
                "timestamp": row[0],
                "rsi": row[1],
                "macd_hist": row[2],
                "price": row[3],
            }
        return None

    def check_signals(self, symbol: str, indicators: dict) -> list:
        """Check for signals based on indicators"""
        signals = []

        # RSI signals
        rsi = indicators.get("rsi")
        if rsi is not None:
            if rsi < 30:
                signals.append(
                    {
                        "type": "RSI_OVERSOLD",
                        "message": f"RSI {rsi:.1f} < 30 (oversold)",
                        "strength": 100 - rsi * 3.33,  # 0-100 scale
                    }
                )
            elif rsi > 70:
                signals.append(
                    {
                        "type": "RSI_OVERBOUGHT",
                        "message": f"RSI {rsi:.1f} > 70 (overbought)",
                        "strength": (rsi - 70) * 3.33,
                    }
                )

        # MACD histogram crossover
        macd_hist = indicators.get("macd_hist")
        if macd_hist is not None:
            # Need previous value to detect crossover
            # For now, just check sign
            if macd_hist > 0:
                signals.append(
                    {
                        "type": "MACD_BULLISH",
                        "message": f"MACD histogram positive ({macd_hist:.4f})",
                        "strength": min(abs(macd_hist) * 100, 100),
                    }
                )
            else:
                signals.append(
                    {
                        "type": "MACD_BEARISH",
                        "message": f"MACD histogram negative ({macd_hist:.4f})",
                        "strength": min(abs(macd_hist) * 100, 100),
                    }
                )

        return signals

    def should_alert(self, symbol: str, signal_type: str, timeframe: str) -> bool:
        """Check if we should send alert (avoid duplicates)"""
        key = f"{symbol}_{timeframe}_{signal_type}"

        # Check if same alert sent in last 24 hours
        if key in self.alert_state:
            last_alert = datetime.fromisoformat(self.alert_state[key])
            if datetime.now() - last_alert < timedelta(hours=24):
                return False

        # Update state
        self.alert_state[key] = datetime.now().isoformat()
        self.save_alert_state()
        return True

    def generate_alert_message(
        self, symbol: str, timeframe: str, signals: list, sentiment: dict = None
    ) -> str:
        """Generate Telegram alert message with optional sentiment"""
        coin = symbol.replace("-USD", "")

        lines = []
        lines.append(f"🚨 **{coin} Alert** ({timeframe})")
        lines.append("")

        for signal in signals:
            strength = signal.get("strength", 0)
            if strength > 80:
                emoji = "🔴"
            elif strength > 60:
                emoji = "🟡"
            else:
                emoji = "🟢"

            lines.append(f"{emoji} {signal['message']}")

        # Add sentiment if available
        if sentiment and sentiment.get("score") is not None:
            score = sentiment["score"]
            if score > 0.3:
                sent_emoji = "📈"
                sent_text = "bullish"
            elif score < -0.3:
                sent_emoji = "📉"
                sent_text = "bearish"
            else:
                sent_emoji = "➡️"
                sent_text = "neutral"

            lines.append("")
            lines.append(f"{sent_emoji} **Sentiment**: {sent_text} ({score:.2f})")

            summary = sentiment.get("summary", "")
            if summary and len(summary) < 100:
                lines.append(f"• {summary}")

            headlines = sentiment.get("headline_count", 0)
            if headlines > 0:
                lines.append(f"• {headlines} recent news headlines")

        lines.append("")
        lines.append(
            f"_Signal strength: {max(s['strength'] for s in signals) if signals else 0:.0f}%_"
        )
        lines.append("---")
        lines.append(f"Time: {datetime.now().strftime('%H:%M')}")

        return "\n".join(lines)

    def get_sentiment_for_symbol(self, symbol: str) -> dict:
        """Get sentiment analysis for a symbol if analyzer is available"""
        if not self.sentiment_analyzer:
            return None

        try:
            sentiment = self.sentiment_analyzer.get_symbol_sentiment(symbol)
            return sentiment
        except Exception as e:
            logger.warning(f"Failed to get sentiment for {symbol}: {e}")
            return None

    def check_portfolio_signals(self):
        """Check signals for all portfolio symbols"""
        conn = sqlite3.connect(self.db_path)

        # Get unique symbols from indicators table
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT symbol FROM indicators")
        symbols = [row[0] for row in cursor.fetchall()]
        conn.close()

        alerts_sent = 0

        for symbol in symbols:
            # Check different timeframes
            for timeframe in ["1h", "4h", "1d"]:
                indicators = self.get_latest_indicators(symbol, timeframe)
                if not indicators:
                    continue

                signals = self.check_signals(symbol, indicators)
                if not signals:
                    continue

                # Filter signals by strength
                strong_signals = [s for s in signals if s.get("strength", 0) > 50]
                if not strong_signals:
                    continue

                # Check if we should alert
                signal_type = strong_signals[0]["type"]
                if self.should_alert(symbol, signal_type, timeframe):
                    # Get sentiment if analyzer is available
                    sentiment = None
                    if self.sentiment_analyzer:
                        sentiment = self.get_sentiment_for_symbol(symbol)

                    alert_msg = self.generate_alert_message(
                        symbol, timeframe, strong_signals, sentiment
                    )
                    logger.info(
                        f"Alert for {symbol} {timeframe}: {strong_signals[0]['type']}"
                        + (
                            f" (sentiment: {sentiment['score']:.2f})"
                            if sentiment
                            else ""
                        )
                    )

                    # Log only — Telegram delivery disabled to reduce noise
                    alerts_sent += 1

        logger.info(f"Sent {alerts_sent} alerts")
        return alerts_sent

    def send_telegram_alert(self, message: str) -> bool:
        """Send alert via Telegram Bot API"""
        try:
            # Truncate if too long
            if len(message) > 4000:
                message = message[:3997] + "..."

            import urllib.request
            import urllib.parse

            token = os.getenv(
                "TELEGRAM_BOT_TOKEN", "8724014451:AAGpisAWj86i8qmkOtfb4mCBSpiPfZd0ROI"
            )
            chat_id = os.getenv("TELEGRAM_CHAT_ID", "1806720995")
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            data = urllib.parse.urlencode(
                {"chat_id": chat_id, "text": message}
            ).encode()
            req = urllib.request.Request(url, data=data, method="POST")
            resp = urllib.request.urlopen(req, timeout=30)

            if resp.status == 200:
                logger.info("Alert sent to Telegram")
                return True
            else:
                logger.error(f"Failed to send alert: HTTP {resp.status}")
                return False

        except Exception as e:
            logger.error(f"Exception sending alert: {e}")
            return False

    def run_daily_check(self):
        """Run daily signal check"""
        logger.info("Running daily signal check...")
        alerts = self.check_portfolio_signals()
        logger.info(f"Daily check completed: {alerts} alerts sent")
        return alerts


def main():
    """Main execution function"""
    import argparse

    parser = argparse.ArgumentParser(description="Crypto Signal Alerts")
    parser.add_argument(
        "--check", action="store_true", help="Check signals and send alerts"
    )
    parser.add_argument(
        "--test", action="store_true", help="Test mode (print only, no send)"
    )
    parser.add_argument(
        "--sentiment",
        action="store_true",
        help="Enable sentiment analysis (requires feedparser, openai)",
    )

    args = parser.parse_args()

    print("Crypto Signal Alert System")
    print("=" * 50)

    try:
        alert_system = SignalAlertSystem(use_sentiment=args.sentiment)

        if args.check:
            alerts = alert_system.check_portfolio_signals()
            print(f"✅ Check completed: {alerts} alerts sent")
        else:
            # Interactive mode
            alerts = alert_system.run_daily_check()
            print(f"✅ Daily check completed: {alerts} alerts sent")

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
