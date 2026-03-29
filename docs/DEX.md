# DEX Trading — Arwen Portfolio

Last updated: 2026-03-29 | Verified against code: 2026-03-29

## Overview

Edoras supports on-chain DEX trading on Base and Ethereum via the Bankr API. The Arwen portfolio (ID 4) is the live DEX portfolio, operated by Aleph (AI agent) with operator oversight.

## Architecture

```
Signal Engine / Aleph
        |
        v
  dex_trading_agent.py    (LLM-driven decision: data -> indicators -> LLM -> execute)
        |
        v
  dex_executor.py         (execution: safety checks, Bankr API calls, DB sync)
        |
        v
  bankr_client.py         (API client: prompt/poll pattern, balance, swap)
        |
        v
  Bankr API               (https://api.bankr.bot)
        |
        v
  Base / Ethereum DEX     (on-chain swap execution)
```

## Key Modules

| Module | Purpose |
|--------|---------|
| `bankr_client.py` | Bankr API client: prompt/poll pattern, balance queries, swap execution |
| `dex_executor.py` | Trade execution with safety checks, slippage protection, DB position sync |
| `dex_trading_agent.py` | LLM-driven trading orchestrator: data -> indicators -> decision -> execute |
| `dex_risk_rules.py` | DEX-specific risk rules: liquidity, slippage, holder count, position vs pool |
| `dex_data_collector.py` | Token OHLCV + metadata collection via GeckoTerminal (every 2h timer) |

## Safety Limits (`config.DEX_CONFIG`)

| Parameter | Value | Purpose |
|-----------|-------|---------|
| `min_liquidity_usd` | $100,000 | Skip illiquid tokens |
| `min_volume_24h_usd` | $50,000 | Skip low-volume tokens |
| `max_slippage_percent` | 5% | Cancel if slippage exceeds |
| `max_position_size_percent` | 10% | Max portfolio per token |
| `min_token_age_days` | 7 | Skip brand-new tokens |
| `min_holder_count` | 100 | Skip concentrated tokens |
| `max_single_order_usd` | $100 | Per-order limit |
| `max_daily_volume_usd` | $500 | Daily volume cap |

## Current Symbols

`VVV-BASE`, `BNKR-BASE`, `WETH-BASE`, `USDC-BASE`

## CLI Commands

```bash
cd ~/.openclaw/workspace/projects/edoras
python3 cli.py dex balance              # Wallet balance
python3 cli.py dex buy BNKR-BASE 50    # Buy $50 worth
python3 cli.py dex sell WETH-BASE 0.01  # Sell 0.01 WETH
python3 cli.py dex txns --hours 24      # Recent transactions
python3 cli.py dex health               # DEX health check
```

## Database Tables

- `dex_tokens` — DEX token metadata (chain, contract, liquidity, holders)
- `dex_transactions` — On-chain transaction records (tx_hash, gas, slippage)
- `securities` — Tokens registered with `is_dex=1`, `chain`, `contract_address`
- `trades` — DEX trades have `tx_hash`, `block_number`, `gas_used`, `slippage_bps` columns

## Systemd Timer

`dex-data-collection.timer` — runs every 2h, collects token OHLCV + metadata via GeckoTerminal.

## Wallet

Arwen uses an on-chain wallet on Base. Wallet address and Bankr config are in `~/.bankr/config.json`.

---

For historical planning docs, see `docs/archive/DEX_INTEGRATION_ARCHITECTURE.md` and `docs/archive/DEX_INTEGRATION_REQUIREMENTS.md`.
