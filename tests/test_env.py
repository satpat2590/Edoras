#!/usr/bin/env python3
"""
Test environment variable loading for intraday script.
"""
import os
import sys

# Simulate what intraday_update.py does
api_key = os.getenv("COINBASE_API_KEY")
api_secret = os.getenv("COINBASE_API_SECRET")

print(f"Raw API_KEY length: {len(api_key) if api_key else 'None'}")
print(f"Raw API_SECRET length: {len(api_secret) if api_secret else 'None'}")

if api_key and api_secret:
    # Strip quotes
    if api_key.startswith('"') and api_key.endswith('"'):
        api_key = api_key[1:-1]
    if api_secret.startswith('"') and api_secret.endswith('"'):
        api_secret = api_secret[1:-1]
    
    print(f"Stripped API_KEY first 30 chars: {api_key[:30] if len(api_key) > 30 else api_key}")
    print(f"Stripped API_SECRET first 50 chars: {api_secret[:50] if len(api_secret) > 50 else api_secret}")
    
    # Check for EC key marker
    if "-----BEGIN EC PRIVATE KEY-----" in api_secret:
        print("✓ EC private key detected")
        # Fix newlines
        api_secret = api_secret.replace('\\n', '\n')
        print(f"After newline fix, first line: {api_secret.splitlines()[0]}")
    else:
        print("✗ EC private key not found")
    
    # Try to import RESTClient (but don't initialize)
    try:
        from coinbase.rest import RESTClient
        print("✓ coinbase.rest import successful")
        # Optionally test client init (will not make network call)
        # client = RESTClient(api_key=api_key, api_secret=api_secret)
        # print("✓ RESTClient initialized")
    except ImportError as e:
        print(f"✗ Import error: {e}")
    except Exception as e:
        print(f"✗ Other error: {e}")
else:
    print("✗ Environment variables not set")
    sys.exit(1)