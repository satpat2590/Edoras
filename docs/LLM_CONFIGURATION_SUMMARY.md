# LLM Trading Pipeline Configuration

> Last updated: 2026-04-07

## Architecture: Two-Stage Pipeline

The LLM trading system uses a two-stage pipeline where qualitative research
informs quantitative trading decisions.

```
Stage 1: Research Agent (src/edoras/llm/research_agent.py)
  Inputs:  RSS sentiment, vector store history, arXiv insights, regime data
  Output:  ResearchBrief → market narrative, per-symbol sentiment, risk flags, catalysts
  LLM use: One call to synthesize narrative from gathered data

Stage 2: Trading Agent (src/edoras/llm/trading_agent.py)
  Inputs:  ResearchBrief + quant signals + scores + portfolio + trade journal
  Output:  BUY/SELL decisions with structured reasoning (quant_support + research_support)
  LLM use: One call to analyze combined context and produce trades
```

Stage 1 failure is non-fatal — Stage 2 proceeds with quant-only data if research fails.

## LLM Provider Chain (`src/edoras/llm/llm_chain.py`)

5-tier fallback, shared by both stages:

| Priority | Provider | Model | RPM | Use Case |
|----------|----------|-------|-----|----------|
| 1 | DeepSeek | `deepseek-chat` | 60 | Primary — good analytical reasoning |
| 2 | Nous Research | `hermes-3-llama-3.1-405b` (OpenRouter) | 20 | Structured JSON output |
| 3 | Claude | `claude-3-5-sonnet-20241022` | 50 | Reliable instruction following |
| 4 | OpenAI | `gpt-4o` | 60 | Fallback — reliable JSON |
| 5 | MLX | `reasoning` (local @ 192.168.1.50:8008) | 120 | Last resort — free, local |

On total failure: returns static "hold all" JSON (no trades executed).

## Environment Variables

```bash
export DEEPSEEK_API_KEY="sk-..."    # or DEEPSEEK_API (both accepted)
export NOUS_RESEARCH_API_KEY="sk-..."
export ANTHROPIC_API_KEY="sk-ant-..."
export OPENAI_API_KEY="sk-..."      # Also used for embeddings (text-embedding-3-small)
export MLX_BASE_URL="http://192.168.1.50:8008/v1"  # Optional
```

## Self-Preservation (Dynamic)

The Trading Agent's prompt dynamically constrains behavior based on trade journal data:

| Condition | Rule Applied |
|-----------|-------------|
| LLM win rate < 30% | "MUST NOT trade unless you articulate what is DIFFERENT. Default to HOLD." |
| Current regime win rate < 40% | "Max allocation per trade: 5%. Prefer HOLD." |
| Cumulative PnL < -$10 | "You are in a drawdown. Size conservatively, require HIGH conviction." |
| Always | "If research and signals don't align, HOLD." |

These rules are generated from live data, not hardcoded text.

## Response Schema

Each trade requires structured reasoning with evidence from both sources:

```json
{
  "reasoning": {
    "thesis": "Core thesis combining quant + research",
    "trend_regime": "uptrend|downtrend|ranging",
    "quant_support": ["RSI oversold", "MACD cross", "score 62.5"],
    "research_support": ["bullish sentiment +0.35", "ETF inflows"],
    "contradicting": ["VIX elevated", "neutral regime 0% win rate"],
    "regime_consideration": "...",
    "similar_past_outcome": "...",
    "risk_note": "..."
  }
}
```

## Execution Guardrails (Code-Enforced)

| Guardrail | Value | Override |
|-----------|-------|---------|
| Max trades per session | 3 | No |
| Cash reserve | 10% of portfolio | No |
| Allocation per trade | 3-20% (default cap 15%) | 20% only with high conviction + confirmed trend |
| Hold period | 6-24h (default 12h) | LLM can request, clamped to range |
| Sell fraction | 25-100% | LLM can request, clamped to range |
| LOW conviction | Never executed | No |
| MEDIUM conviction | Requires signal engine confirmation | No |
| HIGH conviction | Executes unconditionally | No |

## Monitoring

```bash
# Check next run logs
tail -f ~/edoras/trading_agent.log

# Dry-run (shows both stages without executing)
cd ~/edoras && python3 trading_agent.py --dry-run

# Research agent standalone
cd ~/edoras && python3 -m edoras.llm.research_agent --run

# Key log messages to look for
# "Stage 1: Running Research Agent..."
# "Stage 1 complete: narrative=XXX chars, risk_flags=N, symbols=N"
# "LLMChain: DeepSeek responded in X.Xs (NNN chars)"
```

## Scheduling

| Timer | Schedule | What It Runs |
|-------|----------|-------------|
| `trading-agent` | 8:45 AM | `run_trading_agent.sh` → Stage 1 + Stage 2 (morning review) |
| `midday-trading-review` | 12:30 PM | Midday tactical check (high-conviction only, no Stage 1) |

## History

- **2026-04-01**: Fixed LLM chain — moved from 3-tier to 5-tier fallback (`llm_chain.py`)
- **2026-04-07**: Two-stage pipeline — added Research Agent (Stage 1), refactored Trading Agent (Stage 2) with dynamic self-preservation and dual-source reasoning requirements