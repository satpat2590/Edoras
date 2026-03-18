#!/usr/bin/env python3
"""Test optimizer with new thresholds"""

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
    print("Testing Optimizer with Adjusted Thresholds")
    print("=" * 60)
    
    try:
        api_key, api_secret = load_credentials()
        print("Credentials loaded")
        
        sys.path.insert(0, os.path.dirname(__file__))
        from portfolio_optimizer import PortfolioOptimizer
        
        optimizer = PortfolioOptimizer(
            db_path="crypto_data.db",
            api_key=api_key,
            api_secret=api_secret
        )
        
        # Use fallback portfolio
        holdings = {
            "ETH": 2148.37,
            "BTC": 498.94,
            "XRP": 230.99,
            "TROLL": 30.0,
            "BONK": 15.0,
            "FET": 12.0,
            "AMP": 10.0,
            "GRT": 8.0
        }
        
        # Scale to match total
        current_total = sum(holdings.values())
        target_total = 2952.84
        if current_total > 0:
            scale = target_total / current_total
            holdings = {k: v * scale for k, v in holdings.items()}
        
        print(f"Portfolio value: ${sum(holdings.values()):.2f}")
        
        # Get top symbols from database
        import sqlite3
        conn = sqlite3.connect("crypto_data.db")
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT symbol FROM candlesticks WHERE symbol LIKE '%-USD'")
        all_symbols = [row[0] for row in cursor.fetchall()]
        conn.close()
        
        known_top = ["BTC-USD", "ETH-USD", "BNB-USD", "SOL-USD", "XRP-USD",
                    "ADA-USD", "AVAX-USD", "DOGE-USD", "DOT-USD", "TRX-USD",
                    "LINK-USD", "MATIC-USD", "SHIB-USD", "LTC-USD", "UNI-USD"]
        
        top_symbols = [s for s in known_top if s in all_symbols]
        print(f"Top symbols: {len(top_symbols)}")
        
        # Generate suggestions
        portfolio_symbols = [f"{currency}-USD" for currency in holdings.keys()]
        portfolio_value = sum(holdings.values())
        
        suggestions = optimizer.generate_optimization_suggestions(
            portfolio_symbols, top_symbols, portfolio_value
        )
        
        print(f"\nGenerated {len(suggestions)} suggestions")
        
        if suggestions:
            print("\nRecommendations:")
            for i, s in enumerate(suggestions[:10], 1):
                print(f"{i}. {s['action']} {s['symbol']} (score: {s['score']}, priority: {s['priority']})")
                print(f"   Reason: {s['reason']}")
                print()
        else:
            print("\nNo suggestions generated. Debugging...")
            
            # Calculate scores for all symbols
            all_symbols_set = list(set(portfolio_symbols + top_symbols))
            symbol_scores = {}
            for symbol in all_symbols_set:
                data = optimizer.get_symbol_data(symbol)
                if data:
                    score = optimizer.calculate_symbol_score(data)
                    symbol_scores[symbol] = score
            
            if symbol_scores:
                print("\nAll symbol scores:")
                for symbol, score in sorted(symbol_scores.items(), key=lambda x: x[1], reverse=True):
                    in_portfolio = symbol in portfolio_symbols
                    prefix = "P" if in_portfolio else "T"
                    print(f"{prefix} {symbol}: {score:.1f}")
                
                portfolio_scores = {s: symbol_scores.get(s, 0) for s in portfolio_symbols}
                avg_portfolio = sum(portfolio_scores.values()) / len(portfolio_scores) if portfolio_scores else 50
                print(f"\nPortfolio average score: {avg_portfolio:.1f}")
                
                # Check thresholds
                print("\nThreshold checks:")
                for symbol, score in portfolio_scores.items():
                    if score < avg_portfolio * 0.9:
                        print(f"  {symbol}: SELL candidate (score {score} < {avg_portfolio * 0.9:.1f})")
                
                top_crypto_scores = {s: symbol_scores.get(s, 0) for s in top_symbols if s not in portfolio_symbols}
                for symbol, score in top_crypto_scores.items():
                    if score > avg_portfolio * 1.1:
                        print(f"  {symbol}: BUY candidate (score {score} > {avg_portfolio * 1.1:.1f})")
                
                for symbol, score in portfolio_scores.items():
                    if score > avg_portfolio * 1.15:
                        print(f"  {symbol}: HOLD/ADD candidate (score {score} > {avg_portfolio * 1.15:.1f})")
        
        print("\n" + "=" * 60)
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)