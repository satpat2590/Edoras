#!/usr/bin/env python3
"""Test the report fix"""

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
    print("Testing report generation fix...")
    try:
        api_key, api_secret = load_credentials()
        print("Credentials loaded")
        
        sys.path.insert(0, os.path.dirname(__file__))
        from crypto_data_collector import CryptoDataCollector
        
        collector = CryptoDataCollector(db_path='crypto_data.db', api_key=api_key, api_secret=api_secret)
        report = collector.generate_portfolio_report()
        print("\n" + "="*60)
        print(report)
        print("="*60)
        
        # Check for the overall assessment section
        lines = report.split('\n')
        in_section = False
        for line in lines:
            if "Overall Portfolio Assessment" in line:
                in_section = True
            if in_section and line.startswith("•"):
                print(f"Assessment line: {line}")
                
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)