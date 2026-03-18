#!/usr/bin/env python3
"""Test top crypto symbols detection"""

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
    print("Testing top crypto symbols...")
    try:
        api_key, api_secret = load_credentials()
        print("Credentials loaded")
        
        sys.path.insert(0, os.path.dirname(__file__))
        from crypto_data_collector import CryptoDataCollector
        
        collector = CryptoDataCollector(db_path='test_top.db', api_key=api_key, api_secret=api_secret, include_top_cryptos=True)
        
        print("\n=== Portfolio symbols ===")
        portfolio = collector.get_portfolio_symbols()
        print(f"Portfolio: {portfolio}")
        
        print("\n=== Top crypto symbols ===")
        top = collector.get_top_crypto_symbols()
        print(f"Top cryptos: {top}")
        
        print("\n=== Combined symbols ===")
        combined = list(portfolio)
        for sym in top:
            if sym not in combined:
                combined.append(sym)
        print(f"Total: {len(combined)} symbols")
        print(f"Symbols: {combined}")
        
        # Clean up
        if os.path.exists('test_top.db'):
            os.remove('test_top.db')
            
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)