#!/usr/bin/env python3
"""Test the portfolio optimizer"""

import os
import sys
import re

def load_credentials():
    zshrc_path = os.path.expanduser("~/.zshrc")
    if not os.path.exists(zshrc_path):
        raise FileNotFoundError(f"{zshrc_path} not found")
    
    with open(zshrc_path, 'r') as f:
        content = f.read()
    
    # Extract using regex
    api_key_match = re.search(r'export COINBASE_API_KEY=["\']?([^"\'\s]+)', content)
    api_secret_match = re.search(r'export COINBASE_API_SECRET=["\']?([^"\'\s]+)', content)
    
    if not api_key_match or not api_secret_match:
        raise ValueError("Could not extract credentials from .zshrc")
    
    api_key = api_key_match.group(1)
    api_secret = api_secret_match.group(1)
    
    # Fix newlines in EC private key
    if "-----BEGIN EC PRIVATE KEY-----" in api_secret:
        api_secret = api_secret.replace('\\n', '\n')
    
    os.environ['COINBASE_API_KEY'] = api_key
    os.environ['COINBASE_API_SECRET'] = api_secret
    return api_key, api_secret

if __name__ == "__main__":
    print("Testing Portfolio Optimizer...")
    
    try:
        api_key, api_secret = load_credentials()
        print("Credentials loaded")
        
        sys.path.insert(0, os.path.dirname(__file__))
        from portfolio_optimizer import PortfolioOptimizer
        
        print("\nInitializing optimizer...")
        optimizer = PortfolioOptimizer(
            db_path="crypto_data.db",
            api_key=api_key,
            api_secret=api_secret
        )
        
        print("\nTesting symbol scoring...")
        
        # Test a few symbols
        test_symbols = ["BTC-USD", "ETH-USD", "XRP-USD", "ADA-USD", "SOL-USD"]
        
        for symbol in test_symbols:
            data = optimizer.get_symbol_data(symbol)
            if data:
                score = optimizer.calculate_symbol_score(data)
                print(f"{symbol}: Score = {score}/100")
                if 'price' in data:
                    print(f"  Price: ${data['price']:.2f}")
                if '1h_rsi' in data:
                    print(f"  RSI (1h): {data['1h_rsi']:.1f}")
                if '1h_macd' in data:
                    print(f"  MACD (1h): {data['1h_macd']:.4f}")
            else:
                print(f"{symbol}: No data available")
        
        print("\nTesting portfolio concentration analysis...")
        # Create dummy holdings for testing
        dummy_holdings = {
            "ETH": 2148.37,  # From earlier portfolio snapshot
            "BTC": 498.94,
            "XRP": 230.99,
            "TROLL": 50.0,
            "BONK": 30.0,
            "FET": 25.0,
            "AMP": 20.0,
            "GRT": 15.0
        }
        
        concentration = optimizer.analyze_portfolio_concentration(dummy_holdings)
        if concentration:
            print(f"Total value: ${concentration['total_value']:.2f}")
            print(f"Assets: {concentration['asset_count']}")
            print(f"Concentration risk: {concentration['concentration_risk']} (HHI: {concentration['hhi']})")
            print(f"Top 3: {concentration['top_percentage']}% of portfolio")
        
        print("\n✅ Optimizer test completed")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)