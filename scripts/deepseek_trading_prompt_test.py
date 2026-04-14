#!/usr/bin/env python3
"""
Test DeepSeek with the EXACT trading prompt from trading_agent.py
"""

import os
import sys
import json
import time
from openai import OpenAI

# Get API key
api_key = os.getenv("DEEPSEEK_API") or os.getenv("DEEPSEEK_API_KEY")
if not api_key:
    sys.exit("No API key")

client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")

# Test 1: With trading system's EXACT system prompt
print("="*80)
print("TEST 1: Trading system's EXACT prompt")
print("="*80)

system_prompt_trading = "You are a quantitative trading agent. Always respond with valid JSON. Be analytical and precise."
user_prompt = """You are Regi, a systematic quantitative trading agent.
You manage a paper portfolio and learn from every trade outcome.
Your decisions should be informed by the TRADE JOURNAL data below -- favor signal types
and regimes where you have historically performed well. Avoid repeating losing patterns.

HARD RULES (enforced by execution engine -- violations are blocked):
- Stop-loss: 10% below entry (automatic, cannot be overridden)
- Circuit breaker: 15% portfolio drawdown (liquidates all)
- Max position size: 20% of portfolio per symbol
- Max sector exposure: 30% (crypto, equity, etc.)
- No leverage, no options, no futures

PORTFOLIO STATE:
- Total value: $940.02
- Cash: $894.03
- Positions: 4 (details in trade journal)
- Risk state: No circuit breakers active

MARKET CONTEXT:
- Regime: neutral (VIX 24.8)
- Active signals: None
- Sentiment: Neutral across assets

TRADE JOURNAL (recent trades):
No recent trades.

YOUR TASK:
Analyze the market context and portfolio state.
Decide whether to:
1. Enter new positions (BUY)
2. Exit existing positions (SELL)
3. Hold and wait for better opportunities

Provide your analysis in valid JSON format with trade recommendations."""

print(f"System prompt: {system_prompt_trading}")
print(f"User prompt length: {len(user_prompt)} chars")

try:
    start = time.time()
    response = client.chat.completions.create(
        model="deepseek-reasoner",
        messages=[
            {"role": "system", "content": system_prompt_trading},
            {"role": "user", "content": user_prompt}
        ],
        temperature=0.2,
        max_tokens=1500,
        timeout=30
    )
    elapsed = time.time() - start
    
    content = response.choices[0].message.content.strip()
    print(f"\nResponse time: {elapsed:.2f}s")
    print(f"Response length: {len(content)} chars")
    
    if len(content) == 0:
        print("⚠️  EMPTY RESPONSE!")
    else:
        print(f"Response preview: {content[:300]}...")
        
        # Try to parse JSON
        try:
            parsed = json.loads(content)
            print(f"✓ JSON parsed successfully!")
        except json.JSONDecodeError as e:
            print(f"✗ JSON parse error: {e}")
            # Look for JSON in response
            import re
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                print("Found JSON-like content in response")
    
    print(f"Finish reason: {response.choices[0].finish_reason}")
    
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()

# Test 2: With simpler system prompt (what works)
print("\n" + "="*80)
print("TEST 2: Simpler system prompt (what works in our test)")
print("="*80)

system_prompt_simple = "You are a helpful assistant that outputs valid JSON."

print(f"System prompt: {system_prompt_simple}")

try:
    start = time.time()
    response = client.chat.completions.create(
        model="deepseek-reasoner",
        messages=[
            {"role": "system", "content": system_prompt_simple},
            {"role": "user", "content": user_prompt}
        ],
        temperature=0.2,
        max_tokens=1500,
        timeout=30
    )
    elapsed = time.time() - start
    
    content = response.choices[0].message.content.strip()
    print(f"\nResponse time: {elapsed:.2f}s")
    print(f"Response length: {len(content)} chars")
    
    if len(content) == 0:
        print("⚠️  EMPTY RESPONSE!")
    else:
        print(f"Response preview: {content[:300]}...")
        
        # Try to parse JSON
        try:
            parsed = json.loads(content)
            print(f"✓ JSON parsed successfully!")
        except json.JSONDecodeError as e:
            print(f"✗ JSON parse error: {e}")
    
    print(f"Finish reason: {response.choices[0].finish_reason}")
    
except Exception as e:
    print(f"Error: {e}")

# Test 3: Different model
print("\n" + "="*80)
print("TEST 3: Different model (deepseek-chat)")
print("="*80)

try:
    start = time.time()
    response = client.chat.completions.create(
        model="deepseek-chat",  # Try different model
        messages=[
            {"role": "system", "content": system_prompt_trading},
            {"role": "user", "content": user_prompt}
        ],
        temperature=0.2,
        max_tokens=1500,
        timeout=30
    )
    elapsed = time.time() - start
    
    content = response.choices[0].message.content.strip()
    print(f"\nResponse time: {elapsed:.2f}s")
    print(f"Response length: {len(content)} chars")
    
    if len(content) == 0:
        print("⚠️  EMPTY RESPONSE!")
    else:
        print(f"Response preview: {content[:300]}...")
    
    print(f"Finish reason: {response.choices[0].finish_reason}")
    
except Exception as e:
    print(f"Error: {e}")

print("\n" + "="*80)
print("CONCLUSION")
print("="*80)
print("\nThe issue is likely:")
print("1. DeepSeek Reasoner model filtering trading/financial content")
print("2. Specific system prompt triggering empty responses")
print("3. Need to use simpler prompts or different model")