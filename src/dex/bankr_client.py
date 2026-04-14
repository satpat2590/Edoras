#!/usr/bin/env python3
"""
Bankr API client for DEX operations.

Bankr uses a prompt-based async API:
  1. POST /agent/prompt  → submit a natural language command
  2. GET  /agent/job/{id} → poll until completed/failed
  3. GET  /agent/balances → wallet token balances

All swap execution, price queries, and portfolio operations
go through this prompt→poll pattern.
"""

import json
import logging
import os
import re
import time
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)


def _load_bankr_api_key(config_path: str = None) -> str:
    """Load Bankr API key from config file or environment."""
    # Environment variable takes precedence
    key = os.getenv("BANKR_API_KEY")
    if key:
        return key

    # Fall back to config file
    path = config_path or os.path.expanduser("~/.bankr/config.json")
    try:
        with open(path) as f:
            data = json.load(f)
        return data.get("apiKey", "")
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.warning(f"Could not load Bankr config from {path}: {e}")
        return ""


class BankrClient:
    """Bankr DEX API client — prompt-based trading on EVM chains."""

    def __init__(self, api_key: str = None, base_url: str = "https://api.bankr.bot"):
        from config import DEX_CONFIG
        self.api_key = api_key or _load_bankr_api_key(DEX_CONFIG.get("bankr_config_path"))
        self.base_url = base_url.rstrip("/")
        self.poll_interval = DEX_CONFIG.get("job_poll_interval_sec", 3)
        self.poll_max_wait = DEX_CONFIG.get("job_poll_max_wait_sec", 60)
        self._session = None
        self._request_count = 0  # daily counter (reset manually)

    @property
    def session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update({
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            })
        return self._session

    # ── Core API ───────────────────────────────────────────────────────────

    def submit_prompt(self, prompt: str) -> dict:
        """Submit a natural language prompt to Bankr.

        Returns: {"jobId": str, "threadId": str, "status": str}
        """
        self._request_count += 1
        logger.info(f"[bankr] Submitting prompt (req #{self._request_count}): {prompt[:80]}...")

        resp = self.session.post(
            f"{self.base_url}/agent/prompt",
            json={"prompt": prompt},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        logger.debug(f"[bankr] Submit response: {data}")
        return data

    def poll_job(self, job_id: str, timeout: int = None) -> dict:
        """Poll a job until it completes or times out.

        Returns the full job response including result text.
        """
        timeout = timeout or self.poll_max_wait
        start = time.time()
        interval = self.poll_interval

        while time.time() - start < timeout:
            self._request_count += 1
            resp = self.session.get(
                f"{self.base_url}/agent/job/{job_id}",
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()

            status = data.get("status", "unknown")
            if status in ("completed", "done", "success"):
                logger.info(f"[bankr] Job {job_id} completed")
                return data
            if status in ("failed", "error", "cancelled"):
                logger.error(f"[bankr] Job {job_id} failed: {data}")
                return data

            logger.debug(f"[bankr] Job {job_id} status={status}, polling in {interval}s...")
            time.sleep(interval)
            # Backoff: 3s, 5s, 8s, 10s (cap)
            interval = min(interval * 1.5, 10)

        logger.warning(f"[bankr] Job {job_id} timed out after {timeout}s")
        return {"status": "timeout", "jobId": job_id}

    def prompt_and_wait(self, prompt: str, timeout: int = None) -> dict:
        """Submit a prompt and wait for the result. Returns full job response."""
        submit = self.submit_prompt(prompt)
        job_id = submit.get("jobId")
        if not job_id:
            return {"status": "error", "error": "No jobId in submit response", "raw": submit}
        return self.poll_job(job_id, timeout)

    # ── Balance queries ────────────────────────────────────────────────────

    def get_balances(self) -> dict:
        """Get wallet token balances across chains.

        Returns: {"balances": [{"token": str, "chain": str, "amount": float, "usd_value": float}]}
        """
        self._request_count += 1
        try:
            resp = self.session.get(
                f"{self.base_url}/agent/balances",
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            return self._normalize_balances(data)
        except requests.RequestException as e:
            logger.error(f"[bankr] Balance query failed: {e}")
            # Fallback: ask via prompt
            return self._get_balances_via_prompt()

    def _get_balances_via_prompt(self) -> dict:
        """Fallback: get balances by asking the agent."""
        result = self.prompt_and_wait("what are my current wallet balances on all chains?")
        return {
            "balances": self._parse_balance_text(result.get("result", "")),
            "raw": result,
        }

    def _normalize_balances(self, data: dict) -> dict:
        """Normalize the balances API response into a standard format.

        Bankr returns: {
          "evmAddress": "0x...", "solAddress": "...",
          "balances": {
            "base": {"nativeBalance": "0", "nativeUsd": "0.00", "tokenBalances": [...], "total": "0"},
            "mainnet": {"nativeBalance": "0.0467", "nativeUsd": "98.00", "tokenBalances": [...], "total": "98.00"},
            ...
          }
        }
        """
        balances = []
        raw_balances = data.get("balances", {})

        # Bankr chain-keyed format
        if isinstance(raw_balances, dict) and not isinstance(raw_balances, list):
            chain_map = {"mainnet": "ethereum", "base": "base", "solana": "solana",
                         "polygon": "polygon", "arbitrum": "arbitrum", "optimism": "optimism"}
            for chain_key, chain_data in raw_balances.items():
                if not isinstance(chain_data, dict):
                    continue
                chain_name = chain_map.get(chain_key, chain_key)

                # Native token (ETH, SOL, etc.)
                native_bal = float(chain_data.get("nativeBalance", 0) or 0)
                native_usd = float(chain_data.get("nativeUsd", 0) or 0)
                if native_bal > 0:
                    native_token = "SOL" if chain_name == "solana" else "ETH"
                    balances.append({
                        "token": native_token,
                        "chain": chain_name,
                        "amount": native_bal,
                        "usd_value": native_usd,
                    })

                # ERC-20 / SPL tokens
                for tok in chain_data.get("tokenBalances", []):
                    if isinstance(tok, dict):
                        amount = float(tok.get("balance") or tok.get("amount", 0) or 0)
                        usd = float(tok.get("valueUsd") or tok.get("usd_value", 0) or 0)
                        if amount > 0 or usd > 0:
                            balances.append({
                                "token": tok.get("symbol") or tok.get("token", "?"),
                                "chain": chain_name,
                                "amount": amount,
                                "usd_value": usd,
                                "contract": tok.get("contractAddress") or tok.get("mint", ""),
                            })

        # Flat list format (fallback)
        elif isinstance(raw_balances, list):
            for item in raw_balances:
                if isinstance(item, dict):
                    balances.append({
                        "token": item.get("token") or item.get("symbol", "?"),
                        "chain": item.get("chain") or item.get("network", "unknown"),
                        "amount": float(item.get("amount") or item.get("balance", 0)),
                        "usd_value": float(item.get("usd_value") or item.get("valueUsd", 0)),
                    })

        # Include wallet addresses
        result = {
            "balances": balances,
            "evm_address": data.get("evmAddress"),
            "sol_address": data.get("solAddress"),
            "raw": data,
        }
        return result

    # ── Price queries ──────────────────────────────────────────────────────

    def get_token_price(self, symbol: str, chain: str = "base") -> Optional[float]:
        """Get current token price via Bankr prompt."""
        # Clean symbol for the prompt
        clean = symbol.replace("-BASE", "").replace("-ETH", "").replace("-USD", "")
        result = self.prompt_and_wait(f"what is the current price of {clean} on {chain}?")
        text = result.get("result", "")
        return self._parse_price(text)

    # ── Swap execution ─────────────────────────────────────────────────────

    def execute_swap(
        self,
        from_token: str,
        to_token: str,
        amount: float,
        chain: str = "base",
        max_slippage: float = 5.0,
    ) -> dict:
        """Execute a token swap via natural language prompt.

        Returns: {
            "status": "completed"|"failed"|"timeout",
            "tx_hash": str or None,
            "amount_out": float or None,
            "job_id": str,
            "raw": dict,
        }
        """
        prompt = (
            f"swap {amount} {from_token} for {to_token} on {chain} "
            f"with max {max_slippage}% slippage"
        )
        result = self.prompt_and_wait(prompt, timeout=90)
        text = result.get("result", "")
        parsed = self._parse_swap_result(text)
        parsed["job_id"] = result.get("jobId", "")
        parsed["status"] = result.get("status", "unknown")
        parsed["raw"] = result
        return parsed

    # ── Response parsing ───────────────────────────────────────────────────

    def _parse_price(self, text: str) -> Optional[float]:
        """Extract a numeric price from natural language response."""
        if not text:
            return None
        # Match patterns like "$1,234.56", "1234.56 USD", "$0.00123"
        patterns = [
            r'\$([0-9,]+\.?[0-9]*)',
            r'([0-9,]+\.?[0-9]*)\s*(?:USD|usd|dollars)',
            r'price[:\s]+([0-9,]+\.?[0-9]*)',
            r'([0-9]+\.?[0-9]*)',  # last resort: any number
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                try:
                    return float(match.group(1).replace(",", ""))
                except ValueError:
                    continue
        return None

    def _parse_swap_result(self, text: str) -> dict:
        """Extract tx_hash and amount_out from swap response text."""
        result = {"tx_hash": None, "amount_out": None}
        if not text:
            return result

        # TX hash: 0x followed by 64 hex chars
        tx_match = re.search(r'(0x[a-fA-F0-9]{64})', text)
        if tx_match:
            result["tx_hash"] = tx_match.group(1)

        # Amount received: look for "received X TOKEN" or "got X TOKEN"
        amount_patterns = [
            r'received\s+([0-9,]+\.?[0-9]*)',
            r'got\s+([0-9,]+\.?[0-9]*)',
            r'swapped.*?for\s+([0-9,]+\.?[0-9]*)',
            r'([0-9,]+\.?[0-9]*)\s+(?:tokens?|received)',
        ]
        for pattern in amount_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                try:
                    result["amount_out"] = float(match.group(1).replace(",", ""))
                    break
                except ValueError:
                    continue

        return result

    def _parse_balance_text(self, text: str) -> list:
        """Parse balance info from natural language response."""
        balances = []
        if not text:
            return balances
        # Look for patterns like "0.05 ETH" or "ETH: 0.05"
        for match in re.finditer(r'([0-9.]+)\s+([A-Z]{2,10})', text):
            try:
                balances.append({
                    "token": match.group(2),
                    "chain": "unknown",
                    "amount": float(match.group(1)),
                    "usd_value": 0,
                })
            except ValueError:
                continue
        return balances

    # ── Diagnostics ────────────────────────────────────────────────────────

    def health_check(self) -> dict:
        """Quick API connectivity check."""
        try:
            resp = self.session.get(f"{self.base_url}/agent/balances", timeout=10)
            return {
                "status": "ok" if resp.status_code == 200 else f"http_{resp.status_code}",
                "requests_today": self._request_count,
            }
        except requests.RequestException as e:
            return {"status": f"error: {e}", "requests_today": self._request_count}
