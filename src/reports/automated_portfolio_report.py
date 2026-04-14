#!/usr/bin/env python3
"""
Automated portfolio report for scheduled runs.
Sends report directly to Telegram without user interaction.
"""

import os
import sys
import json
from datetime import datetime
from coinbase.rest import RESTClient


class AutomatedPortfolioReporter:
    """Generate and send portfolio reports automatically"""

    def __init__(self, api_key: str, api_secret: str):
        """Initialize with Coinbase API credentials"""
        # Fix newlines in EC private key if needed
        if api_secret and "-----BEGIN EC PRIVATE KEY-----" in api_secret:
            api_secret = api_secret.replace("\\n", "\n")

        self.client = RESTClient(api_key=api_key, api_secret=api_secret)
        self.portfolio_data = None
        self.portfolio_value = 0
        self.report_date = datetime.now()
        self.telegram_target = os.getenv("TELEGRAM_CHAT_ID", "")

    def get_portfolio_snapshot(self):
        """Get current portfolio snapshot"""
        accounts = self.client.get_accounts()

        portfolio = []
        total_value = 0

        for account in accounts.accounts:
            value = float(account.available_balance["value"])
            currency = account.available_balance["currency"]

            if value > 0 and currency:
                usd_value = value
                current_price = 1.0
                symbol = f"{currency}-USD"

                if currency != "USD":
                    try:
                        product = self.client.get_product(symbol)
                        current_price = float(product.price)
                        usd_value = value * current_price
                    except Exception as e:
                        print(f"Warning: Could not get price for {symbol}: {e}")
                        current_price = None

                portfolio.append(
                    {
                        "name": account.name,
                        "currency": currency,
                        "amount": value,
                        "current_price": current_price,
                        "usd_value": usd_value,
                        "symbol": symbol if currency != "USD" else "USD",
                    }
                )

                total_value += usd_value

        # Sort by USD value descending
        portfolio.sort(key=lambda x: x["usd_value"], reverse=True)
        self.portfolio_data = portfolio
        self.portfolio_value = total_value

        return portfolio, total_value

    def load_previous_day_data(self, data_file: str = "portfolio_history.json"):
        """Load previous day's portfolio data for comparison"""
        try:
            if os.path.exists(data_file):
                with open(data_file, "r") as f:
                    history = json.load(f)
                    # Get most recent entry
                    if history and "entries" in history and history["entries"]:
                        return history["entries"][-1]
        except Exception as e:
            print(f"Warning: Could not load previous data: {e}")
        return None

    def save_current_data(self, data_file: str = "portfolio_history.json"):
        """Save current portfolio data for future comparison"""
        try:
            history = {"entries": []}
            if os.path.exists(data_file):
                with open(data_file, "r") as f:
                    history = json.load(f)

            # Add current entry
            entry = {
                "date": self.report_date.strftime("%Y-%m-%d"),
                "timestamp": self.report_date.isoformat(),
                "total_value": self.portfolio_value,
                "assets": [],
            }

            for item in self.portfolio_data:
                entry["assets"].append(
                    {
                        "currency": item["currency"],
                        "amount": item["amount"],
                        "usd_value": item["usd_value"],
                    }
                )

            # Keep last 90 days of history
            history["entries"].append(entry)
            if len(history["entries"]) > 90:
                history["entries"] = history["entries"][-90:]

            with open(data_file, "w") as f:
                json.dump(history, f, indent=2)

            return True
        except Exception as e:
            print(f"Warning: Could not save current data: {e}")
            return False

    def calculate_daily_change(self, prev_data):
        """Calculate change from previous day"""
        if not prev_data:
            return None, None

        prev_value = prev_data["total_value"]
        current_value = self.portfolio_value

        if prev_value > 0:
            change_amount = current_value - prev_value
            change_percent = (change_amount / prev_value) * 100
            return change_amount, change_percent

        return None, None

    def get_market_sentiment(self):
        """Get brief market sentiment for major coins"""
        major_coins = ["BTC-USD", "ETH-USD", "XRP-USD", "SOL-USD"]
        sentiment = []

        for symbol in major_coins:
            try:
                product = self.client.get_product(symbol)
                change_24h = float(product.price_percentage_change_24h)

                # Simple sentiment indicator
                if change_24h > 3:
                    emoji = "🚀"
                elif change_24h > 1:
                    emoji = "📈"
                elif change_24h < -3:
                    emoji = "📉"
                elif change_24h < -1:
                    emoji = "🔻"
                else:
                    emoji = "➡️"

                coin_name = symbol.split("-")[0]
                sentiment.append(f"{coin_name}: {emoji} {change_24h:+.1f}%")

            except Exception as e:
                print(f"Could not get {symbol} sentiment: {e}")

        return sentiment

    def generate_report(self) -> str:
        """Generate formatted report for Telegram"""
        # Get portfolio data
        portfolio, total_value = self.get_portfolio_snapshot()

        # Load previous data for comparison
        prev_data = self.load_previous_day_data()

        # Calculate daily change
        change_amount, change_percent = self.calculate_daily_change(prev_data)

        # Get market sentiment
        market_sentiment = self.get_market_sentiment()

        # Save current data for tomorrow's comparison
        self.save_current_data()

        # Build report
        report_lines = []

        # Header with random emoji for variety
        random_emojis = ["📊", "📈", "💰", "💹", "🏦", "💎", "🚀", "📉", "💼"]
        import random

        header_emoji = random.choice(random_emojis)

        report_date = self.report_date.strftime("%Y-%m-%d %I:%M %p")
        report_lines.append(f"{header_emoji} **Portfolio Snapshot**")
        report_lines.append(f"🕐 {report_date}")
        report_lines.append("")

        # Portfolio Summary
        report_lines.append("**PORTFOLIO SUMMARY**")
        report_lines.append(f"Total Value: ${total_value:,.2f}")

        if change_amount is not None and change_percent is not None:
            change_emoji = "📈" if change_amount >= 0 else "📉"
            report_lines.append(
                f"Daily Change: {change_emoji} ${change_amount:+,.2f} ({change_percent:+.1f}%)"
            )

        report_lines.append(f"Assets: {len(portfolio)}")
        report_lines.append("")

        # Top Holdings (top 3)
        report_lines.append("**TOP HOLDINGS**")
        for i, item in enumerate(portfolio[:3], 1):
            pct = (item["usd_value"] / total_value) * 100
            if item["currency"] == "USD":
                report_lines.append(
                    f"{i}. {item['currency']}: ${item['usd_value']:,.2f} ({pct:.1f}%)"
                )
            else:
                report_lines.append(
                    f"{i}. {item['currency']}: {item['amount']:,.4f} (${item['usd_value']:,.2f}, {pct:.1f}%)"
                )

        # Concentration Warning
        if len(portfolio) > 0:
            top_asset_pct = (portfolio[0]["usd_value"] / total_value) * 100
            if top_asset_pct > 50:
                report_lines.append("")
                report_lines.append(
                    f"⚠️ {portfolio[0]['currency']} is {top_asset_pct:.1f}% of portfolio"
                )

        # Market Sentiment
        if market_sentiment:
            report_lines.append("")
            report_lines.append("**MARKET (24h)**")
            for sentiment in market_sentiment:
                report_lines.append(f"• {sentiment}")

        # Risk Level (simplified)
        report_lines.append("")
        report_lines.append("**RISK**")

        if len(portfolio) >= 1:
            top_3_pct = (
                sum(item["usd_value"] for item in portfolio[:3]) / total_value * 100
            )
            if top_3_pct > 90:
                risk_level = "🔴 HIGH"
                risk_reason = "Extreme concentration"
            elif top_3_pct > 70:
                risk_level = "🟡 MEDIUM"
                risk_reason = "High concentration"
            else:
                risk_level = "🟢 LOW"
                risk_reason = "Well diversified"

            report_lines.append(f"{risk_level}: {risk_reason}")

        # Random tip of the day
        tips = [
            "Diversification reduces unsystematic risk.",
            "Consider dollar-cost averaging during volatility.",
            "Rebalance when any asset exceeds target allocation.",
            "Keep an emergency fund outside crypto.",
            "Past performance ≠ future results.",
            "Only invest what you can afford to lose.",
            "Consider taking profits during bull markets.",
            "Stablecoins provide liquidity during downturns.",
        ]
        report_lines.append("")
        report_lines.append("**TIP**")
        report_lines.append(f"• {random.choice(tips)}")

        # Footer
        report_lines.append("")
        report_lines.append("---")
        report_lines.append("_Auto-report • Schedule random_")

        # Join with newlines
        report = "\n".join(report_lines)

        # Ensure under Telegram's 4096 character limit
        if len(report) > 4000:
            report = report[:3997] + "..."

        return report

    def send_via_telegram(self, report_text: str) -> bool:
        """Send report via Telegram Bot API"""
        try:
            # Truncate if too long
            if len(report_text) > 4000:
                report_text = report_text[:3997] + "..."

            import urllib.request
            import urllib.parse

            token = os.getenv(
                "TELEGRAM_BOT_TOKEN", "8724014451:AAGpisAWj86i8qmkOtfb4mCBSpiPfZd0ROI"
            )
            chat_id = os.getenv("TELEGRAM_CHAT_ID", "1806720995")
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            data = urllib.parse.urlencode(
                {"chat_id": chat_id, "text": report_text}
            ).encode()
            req = urllib.request.Request(url, data=data, method="POST")
            resp = urllib.request.urlopen(req, timeout=30)

            if resp.status == 200:
                print(
                    f"✅ Report sent to Telegram at {datetime.now().strftime('%H:%M:%S')}"
                )
                return True
            else:
                print(f"❌ Error sending to Telegram: HTTP {resp.status}")
                return False

        except Exception as e:
            print(f"❌ Exception sending to Telegram: {e}")
            return False

    def run_and_send(self) -> bool:
        """Main function: generate and send report"""
        print(
            f"Automated Portfolio Report - {self.report_date.strftime('%Y-%m-%d %H:%M')}"
        )
        print("-" * 50)

        try:
            # Generate report
            report = self.generate_report()

            # Send via Telegram
            success = self.send_via_telegram(report)

            # Log result
            self.log_execution(success)

            return success

        except Exception as e:
            print(f"❌ Error in automated report: {e}")
            import traceback

            traceback.print_exc()
            self.log_execution(False, str(e))
            return False

    def log_execution(self, success: bool, error_msg: str = ""):
        """Log report execution result"""
        log_file = "logs/automated_reports.log"
        os.makedirs("logs", exist_ok=True)
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "success": success,
            "error": error_msg,
        }

        try:
            # Read existing log
            logs = []
            if os.path.exists(log_file):
                with open(log_file, "r") as f:
                    try:
                        logs = json.load(f)
                    except:
                        logs = []

            # Add new entry
            logs.append(log_entry)

            # Keep only last 100 entries
            if len(logs) > 100:
                logs = logs[-100:]

            # Write back
            with open(log_file, "w") as f:
                json.dump(logs, f, indent=2)

        except Exception as e:
            print(f"Warning: Could not log execution: {e}")


def main():
    """Main execution function"""
    # Get credentials from environment
    api_key = os.getenv("COINBASE_API_KEY")
    api_secret = os.getenv("COINBASE_API_SECRET")

    if not api_key or not api_secret:
        print(
            "ERROR: COINBASE_API_KEY and COINBASE_API_SECRET environment variables not set"
        )
        print("Set them in your environment or .zshrc file")
        sys.exit(1)

    print("Starting automated portfolio report...")

    try:
        # Initialize reporter
        reporter = AutomatedPortfolioReporter(api_key, api_secret)

        # Run and send
        success = reporter.run_and_send()

        if success:
            print("\n✅ Automated report completed successfully!")
            sys.exit(0)
        else:
            print("\n❌ Automated report failed")
            sys.exit(1)

    except Exception as e:
        print(f"❌ Fatal error in automated report: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
