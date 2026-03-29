#!/usr/bin/env python3
"""
Daily crypto portfolio report for Telegram delivery.
Sends concise summary of portfolio performance and risk metrics.
"""

import os
import sys
import json
from datetime import datetime, timedelta
from coinbase.rest import RESTClient

class DailyPortfolioReporter:
    """Generate and send daily portfolio reports"""
    
    def __init__(self, api_key: str, api_secret: str):
        """Initialize with Coinbase API credentials"""
        # Fix newlines in EC private key if needed
        if api_secret and "-----BEGIN EC PRIVATE KEY-----" in api_secret:
            api_secret = api_secret.replace('\\n', '\n')
        
        self.client = RESTClient(api_key=api_key, api_secret=api_secret)
        self.portfolio_data = None
        self.portfolio_value = 0
        self.previous_day_data = None
        self.report_date = datetime.now()
    
    def load_previous_day_data(self, data_file: str = "portfolio_history.json"):
        """Load previous day's portfolio data for comparison"""
        try:
            if os.path.exists(data_file):
                with open(data_file, 'r') as f:
                    history = json.load(f)
                    # Get most recent entry
                    if history and 'entries' in history and history['entries']:
                        self.previous_day_data = history['entries'][-1]
                        return True
        except Exception as e:
            print(f"Warning: Could not load previous data: {e}")
        return False
    
    def save_current_data(self, data_file: str = "portfolio_history.json"):
        """Save current portfolio data for future comparison"""
        try:
            history = {'entries': []}
            if os.path.exists(data_file):
                with open(data_file, 'r') as f:
                    history = json.load(f)
            
            # Add current entry
            entry = {
                'date': self.report_date.strftime('%Y-%m-%d'),
                'timestamp': self.report_date.isoformat(),
                'total_value': self.portfolio_value,
                'assets': []
            }
            
            for item in self.portfolio_data:
                entry['assets'].append({
                    'currency': item['currency'],
                    'amount': item['amount'],
                    'usd_value': item['usd_value']
                })
            
            # Keep last 90 days of history
            history['entries'].append(entry)
            if len(history['entries']) > 90:
                history['entries'] = history['entries'][-90:]
            
            with open(data_file, 'w') as f:
                json.dump(history, f, indent=2)
            
            return True
        except Exception as e:
            print(f"Warning: Could not save current data: {e}")
            return False
    
    def get_portfolio_snapshot(self):
        """Get current portfolio snapshot"""
        print("Getting portfolio snapshot...")
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
                        print(f"  Warning: Could not get price for {symbol}: {e}")
                        current_price = None
                
                portfolio.append({
                    "name": account.name,
                    "currency": currency,
                    "amount": value,
                    "current_price": current_price,
                    "usd_value": usd_value,
                    "symbol": symbol if currency != "USD" else "USD"
                })
                
                total_value += usd_value
        
        # Sort by USD value descending
        portfolio.sort(key=lambda x: x["usd_value"], reverse=True)
        self.portfolio_data = portfolio
        self.portfolio_value = total_value
        
        return portfolio, total_value
    
    def calculate_daily_change(self):
        """Calculate change from previous day"""
        if not self.previous_day_data:
            return None, None
        
        prev_value = self.previous_day_data['total_value']
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
                price = float(product.price)
                change_24h = float(product.price_percentage_change_24h)
                
                # Simple sentiment indicator
                if change_24h > 3:
                    emoji = "🚀"
                    sentiment_desc = "Strong bullish"
                elif change_24h > 1:
                    emoji = "📈"
                    sentiment_desc = "Bullish"
                elif change_24h < -3:
                    emoji = "📉"
                    sentiment_desc = "Strong bearish"
                elif change_24h < -1:
                    emoji = "🔻"
                    sentiment_desc = "Bearish"
                else:
                    emoji = "➡️"
                    sentiment_desc = "Neutral"
                
                coin_name = symbol.split('-')[0]
                sentiment.append(f"{coin_name}: {emoji} {change_24h:+.1f}%")
                
            except Exception as e:
                print(f"  Could not get {symbol} sentiment: {e}")
        
        return sentiment
    
    def generate_telegram_report(self) -> str:
        """Generate formatted report for Telegram"""
        # Get portfolio data
        portfolio, total_value = self.get_portfolio_snapshot()
        
        # Load previous data for comparison
        self.load_previous_day_data()
        
        # Calculate daily change
        change_amount, change_percent = self.calculate_daily_change()
        
        # Get market sentiment
        market_sentiment = self.get_market_sentiment()
        
        # Save current data for tomorrow's comparison
        self.save_current_data()
        
        # Build report
        report_lines = []
        
        # Header
        report_date = self.report_date.strftime('%Y-%m-%d')
        report_lines.append(f"📊 **Daily Crypto Portfolio Report**")
        report_lines.append(f"📅 {report_date}")
        report_lines.append("")
        
        # Portfolio Summary
        report_lines.append("**PORTFOLIO SUMMARY**")
        report_lines.append(f"Total Value: ${total_value:,.2f}")
        
        if change_amount is not None and change_percent is not None:
            change_emoji = "📈" if change_amount >= 0 else "📉"
            report_lines.append(f"Daily Change: {change_emoji} ${change_amount:+,.2f} ({change_percent:+.1f}%)")
        
        report_lines.append(f"Assets: {len(portfolio)}")
        report_lines.append("")
        
        # Top Holdings (top 3)
        report_lines.append("**TOP HOLDINGS**")
        for i, item in enumerate(portfolio[:3], 1):
            pct = (item['usd_value'] / total_value) * 100
            if item['currency'] == 'USD':
                report_lines.append(f"{i}. {item['currency']}: ${item['usd_value']:,.2f} ({pct:.1f}%)")
            else:
                report_lines.append(f"{i}. {item['currency']}: {item['amount']:,.4f} (${item['usd_value']:,.2f}, {pct:.1f}%)")
        
        # Concentration Warning
        if len(portfolio) > 0:
            top_asset_pct = (portfolio[0]['usd_value'] / total_value) * 100
            if top_asset_pct > 50:
                report_lines.append("")
                report_lines.append(f"⚠️ **Concentration Risk**: {portfolio[0]['currency']} is {top_asset_pct:.1f}% of portfolio")
        
        # Market Sentiment
        if market_sentiment:
            report_lines.append("")
            report_lines.append("**MARKET SENTIMENT (24h)**")
            for sentiment in market_sentiment:
                report_lines.append(f"• {sentiment}")
        
        # Risk Level (simplified)
        report_lines.append("")
        report_lines.append("**RISK LEVEL**")
        
        # Simple risk assessment based on concentration
        if len(portfolio) >= 1:
            top_3_pct = sum(item['usd_value'] for item in portfolio[:3]) / total_value * 100
            if top_3_pct > 90:
                risk_level = "🔴 HIGH"
                risk_reason = "Extreme concentration (>90% in top 3)"
            elif top_3_pct > 70:
                risk_level = "🟡 MEDIUM"
                risk_reason = "High concentration (>70% in top 3)"
            else:
                risk_level = "🟢 LOW"
                risk_reason = "Well diversified"
            
            report_lines.append(f"{risk_level}: {risk_reason}")
        
        # Recommendations
        report_lines.append("")
        report_lines.append("**RECOMMENDATIONS**")
        
        if len(portfolio) > 0 and portfolio[0]['usd_value'] / total_value > 0.5:
            report_lines.append("• Consider reducing largest position below 50%")
        
        if len(portfolio) < 5:
            report_lines.append("• Add more assets for better diversification")
        
        if any(item['currency'] == 'USD' and item['usd_value'] > 100 for item in portfolio):
            report_lines.append("• Good USD buffer for opportunities")
        else:
            report_lines.append("• Consider adding stablecoin buffer")
        
        # Footer
        report_lines.append("")
        report_lines.append("---")
        report_lines.append("_Generated by Aleph • Coinbase Portfolio Analysis_")
        
        # Join with newlines and ensure it's under Telegram's 4096 character limit
        report = "\n".join(report_lines)
        if len(report) > 4000:
            # Truncate if too long
            report = report[:3997] + "..."
        
        return report
    
    def send_via_openclaw(self, report_text: str, target: str = None):
        target = target or os.getenv("TELEGRAM_CHAT_ID", "")
        """Send report via OpenClaw message command"""
        try:
            # Truncate if too long (Telegram limit ~4096 chars)
            if len(report_text) > 4000:
                report_text = report_text[:3997] + "..."
            
            # Use OpenClaw CLI to send message
            import subprocess
            cmd = ["openclaw", "message", "send", "--target", target, "--message", report_text]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0:
                print(f"Report sent to Telegram (target: {target}).")
                return True
            else:
                print(f"Error sending to Telegram: {result.stderr}")
                return False
                
        except Exception as e:
            print(f"Exception sending to Telegram: {e}")
            return False


def main():
    """Main execution function"""
    import argparse
    
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Generate daily crypto portfolio report')
    parser.add_argument('--auto-send', action='store_true', help='Automatically send report via Telegram')
    parser.add_argument('--save-only', action='store_true', help='Save report to file only')
    parser.add_argument('--test', action='store_true', help='Test mode (print only)')
    parser.add_argument('--target', default=os.getenv("TELEGRAM_CHAT_ID", ""), help='Telegram target chat ID')
    
    args = parser.parse_args()
    
    # Get credentials from environment
    api_key = os.getenv("COINBASE_API_KEY")
    api_secret = os.getenv("COINBASE_API_SECRET")
    
    if not api_key or not api_secret:
        print("ERROR: COINBASE_API_KEY and COINBASE_API_SECRET environment variables not set")
        print("Set them in your environment or .zshrc file")
        sys.exit(1)
    
    print("Generating daily portfolio report...")
    
    try:
        # Initialize reporter
        reporter = DailyPortfolioReporter(api_key, api_secret)
        
        # Generate report
        report = reporter.generate_telegram_report()
        
        # Print to console for verification
        print("\n" + "="*60)
        print("DAILY PORTFOLIO REPORT")
        print("="*60)
        print(report)
        print("="*60)
        
        # Determine action based on arguments
        if args.auto_send:
            # Auto-send mode
            print("\n🔄 Auto-send mode: Sending report via Telegram...")
            success = reporter.send_via_openclaw(report, args.target)
            if success:
                print("✅ Report sent successfully to Telegram!")
            else:
                print("❌ Failed to send report to Telegram")
                # Save to file as backup
                with open("daily_report_backup.txt", "w") as f:
                    f.write(report)
                print("📁 Report saved to daily_report_backup.txt as backup")
        
        elif args.save_only:
            # Save to file only
            with open("daily_report.txt", "w") as f:
                f.write(report)
            print("📁 Report saved to daily_report.txt")
        
        elif args.test:
            # Test mode - print only
            print("📝 Test mode: Report printed to console only")
        
        else:
            # Interactive mode
            print("\nOptions:")
            print("1. Send report via Telegram")
            print("2. Save to file only")
            print("3. Test mode (print only)")
            
            choice = input("\nEnter choice (1-3): ").strip()
            
            if choice == "1":
                # Send via Telegram
                success = reporter.send_via_openclaw(report, args.target)
                if success:
                    print("✅ Report sent successfully!")
                else:
                    print("❌ Failed to send report")
                    # Save to file as backup
                    with open("daily_report_backup.txt", "w") as f:
                        f.write(report)
                    print("📁 Report saved to daily_report_backup.txt")
            
            elif choice == "2":
                # Save to file
                with open("daily_report.txt", "w") as f:
                    f.write(report)
                print("📁 Report saved to daily_report.txt")
            
            else:
                print("📝 Report printed to console only")
        
        # Always save JSON data for history
        print("\n📊 Portfolio data saved to portfolio_history.json for future comparisons")
        
    except Exception as e:
        print(f"ERROR during report generation: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()