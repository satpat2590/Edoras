#!/usr/bin/env python3
"""
Price alert system for crypto portfolio.
Monitors configured price thresholds and sends alerts via Telegram.
"""

import os
import sys
import json
import time
from datetime import datetime
from typing import Dict, List, Optional
from coinbase.rest import RESTClient

class PriceAlertSystem:
    """Monitor crypto prices and send alerts when thresholds are crossed"""
    
    def __init__(self, api_key: str, api_secret: str, alert_file: str = "price_alerts.json"):
        """Initialize with Coinbase API credentials"""
        # Fix newlines in EC private key if needed
        if api_secret and "-----BEGIN EC PRIVATE KEY-----" in api_secret:
            api_secret = api_secret.replace('\\n', '\n')
        
        self.client = RESTClient(api_key=api_key, api_secret=api_secret)
        self.alert_file = alert_file
        self.alerts = self.load_alerts()
        self.alert_history = self.load_alert_history()
        
        # Default thresholds if not configured
        self.default_thresholds = {
            "BTC-USD": {
                "high": 75000,  # Alert if above $75k
                "low": 60000,   # Alert if below $60k
                "change_24h": 5,  # Alert if 24h change > 5%
            },
            "ETH-USD": {
                "high": 2500,
                "low": 1800,
                "change_24h": 7,
            },
            "XRP-USD": {
                "high": 1.5,
                "low": 1.2,
                "change_24h": 10,
            }
        }
    
    def load_alerts(self) -> Dict:
        """Load configured alerts from file"""
        try:
            if os.path.exists(self.alert_file):
                with open(self.alert_file, 'r') as f:
                    return json.load(f)
        except Exception as e:
            print(f"Warning: Could not load alerts file: {e}")
        
        # Return empty dict if file doesn't exist or error
        return {}
    
    def save_alerts(self):
        """Save alerts to file"""
        try:
            with open(self.alert_file, 'w') as f:
                json.dump(self.alerts, f, indent=2)
            return True
        except Exception as e:
            print(f"Error saving alerts: {e}")
            return False
    
    def load_alert_history(self) -> Dict:
        """Load alert history to avoid duplicate alerts"""
        history_file = "alert_history.json"
        try:
            if os.path.exists(history_file):
                with open(history_file, 'r') as f:
                    return json.load(f)
        except Exception as e:
            print(f"Warning: Could not load alert history: {e}")
        
        return {"alerts": [], "last_check": None}
    
    def save_alert_history(self):
        """Save alert history"""
        history_file = "alert_history.json"
        try:
            # Keep only last 100 alerts to prevent file from growing too large
            if len(self.alert_history.get("alerts", [])) > 100:
                self.alert_history["alerts"] = self.alert_history["alerts"][-100:]
            
            with open(history_file, 'w') as f:
                json.dump(self.alert_history, f, indent=2)
            return True
        except Exception as e:
            print(f"Error saving alert history: {e}")
            return False
    
    def get_current_prices(self, symbols: List[str]) -> Dict[str, Dict]:
        """Get current prices for symbols"""
        prices = {}
        
        for symbol in symbols:
            try:
                product = self.client.get_product(symbol)
                price = float(product.price)
                change_24h = float(product.price_percentage_change_24h)
                
                prices[symbol] = {
                    "price": price,
                    "change_24h": change_24h,
                    "currency": symbol.split('-')[0],
                    "timestamp": datetime.now().isoformat()
                }
                
            except Exception as e:
                print(f"  Error getting price for {symbol}: {e}")
        
        return prices
    
    def check_alerts(self, prices: Dict[str, Dict]) -> List[Dict]:
        """Check prices against alert thresholds"""
        triggered_alerts = []
        
        for symbol, price_data in prices.items():
            price = price_data["price"]
            change_24h = price_data["change_24h"]
            currency = price_data["currency"]
            
            # Get thresholds for this symbol
            thresholds = self.alerts.get(symbol, self.default_thresholds.get(symbol, {}))
            
            # Check high threshold
            if "high" in thresholds and price > thresholds["high"]:
                alert = {
                    "symbol": symbol,
                    "currency": currency,
                    "price": price,
                    "threshold": thresholds["high"],
                    "type": "HIGH",
                    "message": f"{currency} price ${price:,.2f} crossed HIGH threshold (${thresholds['high']:,.2f})",
                    "timestamp": datetime.now().isoformat()
                }
                triggered_alerts.append(alert)
            
            # Check low threshold
            if "low" in thresholds and price < thresholds["low"]:
                alert = {
                    "symbol": symbol,
                    "currency": currency,
                    "price": price,
                    "threshold": thresholds["low"],
                    "type": "LOW",
                    "message": f"{currency} price ${price:,.2f} crossed LOW threshold (${thresholds['low']:,.2f})",
                    "timestamp": datetime.now().isoformat()
                }
                triggered_alerts.append(alert)
            
            # Check 24h change threshold
            if "change_24h" in thresholds and abs(change_24h) > thresholds["change_24h"]:
                direction = "UP" if change_24h > 0 else "DOWN"
                alert = {
                    "symbol": symbol,
                    "currency": currency,
                    "price": price,
                    "change_24h": change_24h,
                    "threshold": thresholds["change_24h"],
                    "type": f"CHANGE_{direction}",
                    "message": f"{currency} {change_24h:+.1f}% in 24h ({direction} > {thresholds['change_24h']}% threshold)",
                    "timestamp": datetime.now().isoformat()
                }
                triggered_alerts.append(alert)
        
        return triggered_alerts
    
    def filter_duplicate_alerts(self, alerts: List[Dict]) -> List[Dict]:
        """Filter out alerts that were recently sent"""
        filtered = []
        now = time.time()
        
        for alert in alerts:
            alert_key = f"{alert['symbol']}_{alert['type']}"
            
            # Check if similar alert was sent recently (within last 6 hours)
            recent_alert = False
            for hist_alert in self.alert_history.get("alerts", []):
                if (hist_alert.get("symbol") == alert['symbol'] and 
                    hist_alert.get("type") == alert['type']):
                    
                    # Parse timestamp
                    try:
                        alert_time = datetime.fromisoformat(hist_alert['timestamp'].replace('Z', '+00:00')).timestamp()
                        if now - alert_time < 6 * 3600:  # 6 hours
                            recent_alert = True
                            break
                    except:
                        pass
            
            if not recent_alert:
                filtered.append(alert)
                # Add to history
                self.alert_history.setdefault("alerts", []).append(alert)
        
        return filtered
    
    def generate_alert_message(self, alerts: List[Dict]) -> str:
        """Generate Telegram message for alerts"""
        if not alerts:
            return ""
        
        lines = []
        
        lines.append("🚨 **CRYPTO PRICE ALERTS**")
        lines.append(f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        lines.append("")
        
        for i, alert in enumerate(alerts, 1):
            emoji = "📈" if alert['type'] == 'HIGH' else "📉" if alert['type'] == 'LOW' else "⚡"
            
            lines.append(f"{emoji} **{alert['currency']} Alert**")
            lines.append(f"{alert['message']}")
            
            if alert['type'].startswith('CHANGE'):
                lines.append(f"Current: ${alert['price']:,.2f}")
            
            if i < len(alerts):
                lines.append("")
        
        lines.append("")
        lines.append("_Configure thresholds in price_alerts.json_")
        
        return "\n".join(lines)
    
    def send_alert_via_openclaw(self, message: str, target: str = None) -> bool:
        target = target or os.getenv("TELEGRAM_CHAT_ID", "")
        """Send alert via OpenClaw message command"""
        try:
            import subprocess
            
            # Truncate if too long (Telegram limit ~4096 chars)
            if len(message) > 4000:
                message = message[:3997] + "..."
            
            cmd = ["openclaw", "message", "send", "--target", target, "--message", message]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0:
                print(f"Alert sent to Telegram (target: {target}).")
                return True
            else:
                print(f"Error sending to Telegram: {result.stderr}")
                return False
                
        except Exception as e:
            print(f"Exception sending to Telegram: {e}")
            return False
    
    def run_check(self, symbols: Optional[List[str]] = None, target: str = None) -> bool:
        target = target or os.getenv("TELEGRAM_CHAT_ID", "")
        """Run a single check and send alerts if triggered"""
        print(f"Running price alert check at {datetime.now()}")
        
        # Use default symbols if not specified
        if symbols is None:
            symbols = list(self.default_thresholds.keys())
        
        # Get current prices
        print(f"Checking prices for: {', '.join(symbols)}")
        prices = self.get_current_prices(symbols)
        
        if not prices:
            print("No prices retrieved, skipping alert check")
            return False
        
        # Check against thresholds
        triggered_alerts = self.check_alerts(prices)
        
        if not triggered_alerts:
            print("No alerts triggered")
            # Update last check time
            self.alert_history["last_check"] = datetime.now().isoformat()
            self.save_alert_history()
            return True
        
        # Filter duplicate alerts
        filtered_alerts = self.filter_duplicate_alerts(triggered_alerts)
        
        if not filtered_alerts:
            print(f"Alerts filtered out (duplicates within 6 hours)")
            self.alert_history["last_check"] = datetime.now().isoformat()
            self.save_alert_history()
            return True
        
        # Generate and send alert message
        print(f"Sending {len(filtered_alerts)} alert(s)")
        alert_message = self.generate_alert_message(filtered_alerts)
        
        success = self.send_alert_via_openclaw(alert_message, target)
        
        # Save history
        self.alert_history["last_check"] = datetime.now().isoformat()
        self.save_alert_history()
        
        return success
    
    def configure_alert(self, symbol: str, high: Optional[float] = None, 
                       low: Optional[float] = None, change_24h: Optional[float] = None):
        """Configure alert thresholds for a symbol"""
        if symbol not in self.alerts:
            self.alerts[symbol] = {}
        
        if high is not None:
            self.alerts[symbol]["high"] = high
        
        if low is not None:
            self.alerts[symbol]["low"] = low
        
        if change_24h is not None:
            self.alerts[symbol]["change_24h"] = change_24h
        
        self.save_alerts()
        print(f"Configured alerts for {symbol}: {self.alerts[symbol]}")


def main():
    """Main execution function"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Crypto price alert system')
    parser.add_argument('--check', action='store_true', help='Run a single check')
    parser.add_argument('--configure', action='store_true', help='Configure alerts')
    parser.add_argument('--symbol', help='Symbol to configure (e.g., BTC-USD)')
    parser.add_argument('--high', type=float, help='High price threshold')
    parser.add_argument('--low', type=float, help='Low price threshold')
    parser.add_argument('--change', type=float, help='24h change threshold (percent)')
    parser.add_argument('--target', default=os.getenv("TELEGRAM_CHAT_ID", ""), help='Telegram target chat ID')
    parser.add_argument('--symbols', help='Comma-separated list of symbols to check')
    
    args = parser.parse_args()
    
    # Get credentials from environment
    api_key = os.getenv("COINBASE_API_KEY")
    api_secret = os.getenv("COINBASE_API_SECRET")
    
    if not api_key or not api_secret:
        print("ERROR: COINBASE_API_KEY and COINBASE_API_SECRET environment variables not set")
        print("Set them in your environment or .zshrc file")
        sys.exit(1)
    
    try:
        alert_system = PriceAlertSystem(api_key, api_secret)
        
        if args.configure:
            # Configure mode
            if not args.symbol:
                print("ERROR: --symbol required for configuration")
                sys.exit(1)
            
            alert_system.configure_alert(
                symbol=args.symbol,
                high=args.high,
                low=args.low,
                change_24h=args.change
            )
        
        elif args.check:
            # Check mode
            symbols = None
            if args.symbols:
                symbols = [s.strip() for s in args.symbols.split(',')]
            
            success = alert_system.run_check(symbols=symbols, target=args.target)
            
            if success:
                print("✅ Price check completed")
            else:
                print("❌ Price check failed")
                sys.exit(1)
        
        else:
            # Interactive mode
            print("Price Alert System")
            print("=" * 40)
            print("Current configuration:")
            for symbol, config in alert_system.alerts.items():
                print(f"  {symbol}: {config}")
            
            print("\nDefault thresholds (used if not configured):")
            for symbol, config in alert_system.default_thresholds.items():
                print(f"  {symbol}: {config}")
            
            print("\nOptions:")
            print("1. Run price check")
            print("2. Configure alerts")
            print("3. View alert history")
            
            choice = input("\nEnter choice (1-3): ").strip()
            
            if choice == "1":
                symbols_input = input("Symbols to check (comma-separated, blank for default): ").strip()
                symbols = [s.strip() for s in symbols_input.split(',')] if symbols_input else None
                
                success = alert_system.run_check(symbols=symbols, target=args.target)
                
                if success:
                    print("✅ Price check completed")
                else:
                    print("❌ Price check failed")
            
            elif choice == "2":
                symbol = input("Symbol (e.g., BTC-USD): ").strip()
                
                high_input = input("High threshold (blank to skip): ").strip()
                high = float(high_input) if high_input else None
                
                low_input = input("Low threshold (blank to skip): ").strip()
                low = float(low_input) if low_input else None
                
                change_input = input("24h change threshold % (blank to skip): ").strip()
                change = float(change_input) if change_input else None
                
                alert_system.configure_alert(symbol, high, low, change)
            
            elif choice == "3":
                history = alert_system.alert_history
                print(f"\nAlert History (last {len(history.get('alerts', []))} alerts):")
                print(f"Last check: {history.get('last_check', 'Never')}")
                
                if history.get('alerts'):
                    print("\nRecent alerts:")
                    for alert in history['alerts'][-5:]:  # Last 5 alerts
                        print(f"  {alert.get('timestamp', 'Unknown')}: {alert.get('message', 'No message')}")
                else:
                    print("No alert history")
            
            else:
                print("Invalid choice")
    
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()