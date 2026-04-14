#!/usr/bin/env python3
"""
Shared LLM service module for Edoras.

Provides a single, reusable LLMChain class that encapsulates:
  - 5-tier provider fallback: DeepSeek → Nous Research → Claude → GPT-4o → MLX
  - Per-call timeout (configurable; default 30s for trading agent, 15s for gatekeeper)
  - Response caching with TTL (avoids redundant LLM calls within a cycle)
  - Per-provider rate limiting (requests/minute)
  - JSON parsing with markdown fence stripping
  - Structured logging of provider used, latency, response size
  - Static fallback JSON guarantee — callers never receive None on total failure

Usage:
    from llm.llm_chain import LLMChain

    chain = LLMChain(
        system_prompt="You are a data analyst. Always respond in valid JSON.",
        timeout=15,
        cache_ttl=300,
    )
    result = chain.call_with_parse(prompt)   # -> dict (or fallback_json on failure)
    raw    = chain.call(prompt)              # -> str  (or fallback_str on failure)
"""

import hashlib
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Provider configuration ────────────────────────────────────────────────────

_PROVIDER_CONFIGS = [
    {
        "name": "DeepSeek",
        "env_key": ["DEEPSEEK_API_KEY", "DEEPSEEK_API"],
        "backend": "openai_compat",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "rpm_limit": 60,
    },
    {
        "name": "Nous Research",
        "env_key": ["NOUS_RESEARCH_API_KEY"],
        "backend": "openai_compat",
        "base_url": "https://openrouter.ai/api/v1",
        "model": "nousresearch/hermes-3-llama-3.1-405b",
        "rpm_limit": 20,
    },
    {
        "name": "Claude",
        "env_key": ["ANTHROPIC_API_KEY"],
        "backend": "anthropic",
        "model": "claude-3-5-sonnet-20241022",
        "rpm_limit": 50,
    },
    {
        "name": "OpenAI",
        "env_key": ["OPENAI_API_KEY"],
        "backend": "openai_compat",
        "base_url": None,  # uses default openai endpoint
        "model": "gpt-4o",
        "rpm_limit": 60,
    },
    {
        "name": "MLX",
        "env_key": [],  # no key needed (local)
        "backend": "openai_compat",
        "base_url_env": "MLX_BASE_URL",
        "base_url_default": "http://192.168.1.50:8008/v1",
        "model_env": "MLX_MODEL",
        "model_default": "reasoning",
        "rpm_limit": 120,
    },
]

_DEFAULT_FALLBACK_JSON: Dict[str, Any] = {
    "market_assessment": "LLM services temporarily unavailable.",
    "risk_level": "moderate",
    "trades": [],
    "rebalance_recommended": False,
    "hold_rationale": "All LLM providers failed. Maintaining positions.",
    "watchlist": [],
}


# ── Rate limiter ──────────────────────────────────────────────────────────────


class _RateLimiter:
    """Simple token-bucket rate limiter (requests per minute)."""

    def __init__(self, rpm: int):
        self.rpm = rpm
        self._tokens = float(rpm)
        self._last_refill = time.monotonic()

    def acquire(self) -> bool:
        """Return True if a request slot is available (consume one token)."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self.rpm, self._tokens + elapsed * (self.rpm / 60.0))
        self._last_refill = now
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False


# ── Response cache ────────────────────────────────────────────────────────────


class _ResponseCache:
    """In-process LRU-ish cache keyed on prompt hash with TTL expiry."""

    def __init__(self, ttl: int, max_entries: int = 64):
        self.ttl = ttl
        self.max_entries = max_entries
        self._store: Dict[str, tuple] = {}  # key -> (value, expiry_time)

    def _key(self, prompt: str) -> str:
        return hashlib.sha256(prompt.encode()).hexdigest()[:16]

    def get(self, prompt: str) -> Optional[str]:
        k = self._key(prompt)
        entry = self._store.get(k)
        if entry and time.monotonic() < entry[1]:
            return entry[0]
        self._store.pop(k, None)
        return None

    def set(self, prompt: str, value: str):
        if len(self._store) >= self.max_entries:
            # Evict oldest entry
            oldest = min(self._store.items(), key=lambda x: x[1][1])
            del self._store[oldest[0]]
        self._store[self._key(prompt)] = (value, time.monotonic() + self.ttl)


# ── Main LLMChain class ───────────────────────────────────────────────────────


class LLMChain:
    """
    Reusable LLM service with 5-tier fallback, caching, and rate limiting.

    Args:
        system_prompt:  System message sent to every provider.
        temperature:    Sampling temperature (default 0.2 — low variance for JSON).
        max_tokens:     Max response tokens (default 2000).
        timeout:        Per-provider call timeout in seconds (default 30).
        cache_ttl:      Cache TTL in seconds. 0 = no caching (default 0).
        fallback_json:  Dict returned by call_with_parse() when all providers fail.
                        Defaults to a safe "hold all positions" response.
    """

    def __init__(
        self,
        system_prompt: str = "You are a data analyst. Always respond with valid JSON. Be analytical and precise.",
        temperature: float = 0.2,
        max_tokens: int = 2000,
        timeout: int = 30,
        cache_ttl: int = 0,
        fallback_json: Optional[Dict] = None,
    ):
        self.system_prompt = system_prompt
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.fallback_json = fallback_json or _DEFAULT_FALLBACK_JSON

        self._cache = _ResponseCache(ttl=cache_ttl) if cache_ttl > 0 else None
        self._rate_limiters: Dict[str, _RateLimiter] = {}
        self._providers = self._resolve_providers()

        names = [p["name"] for p in self._providers]
        logger.info(
            f"LLMChain initialised — providers: {', '.join(names)} | timeout={timeout}s cache_ttl={cache_ttl}s"
        )

    # ── Provider resolution ───────────────────────────────────────────────

    def _resolve_providers(self) -> List[Dict]:
        """Build the live provider list, resolving env vars and skipping unavailable ones."""
        live = []
        for cfg in _PROVIDER_CONFIGS:
            resolved = dict(cfg)
            # Resolve API key
            api_key = None
            for env_var in cfg.get("env_key", []):
                api_key = os.getenv(env_var)
                if api_key:
                    break
            resolved["api_key"] = api_key

            # MLX has no required key — always include
            if cfg["name"] == "MLX":
                resolved["base_url"] = os.getenv(
                    cfg.get("base_url_env", "MLX_BASE_URL"),
                    cfg.get("base_url_default", "http://192.168.1.50:8008/v1"),
                )
                resolved["model"] = os.getenv(
                    cfg.get("model_env", "MLX_MODEL"),
                    cfg.get("model_default", "reasoning"),
                )
                live.append(resolved)
                continue

            # Keyed providers need an API key to be included
            if api_key:
                live.append(resolved)

        return live

    # ── Core call ─────────────────────────────────────────────────────────

    def call(self, prompt: str) -> str:
        """
        Call LLM with the full 5-tier fallback. Returns raw string response.
        On total failure, returns a JSON-serialised fallback dict.
        Never raises.
        """
        # Cache hit
        if self._cache:
            cached = self._cache.get(prompt)
            if cached is not None:
                logger.debug("LLMChain: cache hit")
                return cached

        for provider in self._providers:
            name = provider["name"]

            # Rate limit check
            rl = self._rate_limiters.setdefault(
                name, _RateLimiter(provider.get("rpm_limit", 60))
            )
            if not rl.acquire():
                logger.debug(f"LLMChain: {name} rate-limited, skipping")
                continue

            t0 = time.monotonic()
            try:
                response = self._call_provider(provider, prompt)
                if response and response.strip():
                    elapsed = time.monotonic() - t0
                    logger.info(
                        f"LLMChain: {name} responded in {elapsed:.1f}s "
                        f"({len(response)} chars)"
                    )
                    if self._cache:
                        self._cache.set(prompt, response)
                    return response
                logger.warning(f"LLMChain: {name} returned empty response")
            except Exception as e:
                elapsed = time.monotonic() - t0
                logger.warning(f"LLMChain: {name} failed after {elapsed:.1f}s — {e}")

        logger.error("LLMChain: all providers failed — returning static fallback")
        return json.dumps(self.fallback_json)

    def call_with_parse(self, prompt: str) -> Dict:
        """
        Call LLM and parse the JSON response. Returns a dict.
        On parse failure or total provider failure, returns fallback_json.
        Never raises.
        """
        raw = self.call(prompt)
        parsed = self._parse_json(raw)
        if parsed is None:
            logger.warning("LLMChain: JSON parse failed — returning fallback_json")
            return self.fallback_json
        return parsed

    # ── Provider dispatch ─────────────────────────────────────────────────

    def _call_provider(self, cfg: Dict, prompt: str) -> str:
        backend = cfg["backend"]
        if backend == "openai_compat":
            return self._call_openai_compat(cfg, prompt)
        elif backend == "anthropic":
            return self._call_anthropic(cfg, prompt)
        raise ValueError(f"Unknown backend: {backend}")

    def _call_openai_compat(self, cfg: Dict, prompt: str) -> str:
        import openai  # lazy import — not installed in all envs

        kwargs: Dict[str, Any] = {"api_key": cfg.get("api_key", "not-needed")}
        if cfg.get("base_url"):
            kwargs["base_url"] = cfg["base_url"]

        client = openai.OpenAI(**kwargs)
        resp = client.chat.completions.create(
            model=cfg["model"],
            messages=[
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": prompt},
            ],
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            timeout=self.timeout,
        )
        return resp.choices[0].message.content.strip()

    def _call_anthropic(self, cfg: Dict, prompt: str) -> str:
        import anthropic  # lazy import

        client = anthropic.Anthropic(api_key=cfg["api_key"])
        resp = client.messages.create(
            model=cfg["model"],
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            system=self.system_prompt,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()

    # ── JSON parsing ──────────────────────────────────────────────────────

    @staticmethod
    def _parse_json(text: str) -> Optional[Dict]:
        """Strip markdown fences and parse JSON. Returns None on failure."""
        if not text:
            return None
        t = text.strip()
        if t.startswith("```"):
            lines = t.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            t = "\n".join(lines)
        try:
            return json.loads(t)
        except json.JSONDecodeError:
            start = t.find("{")
            end = t.rfind("}") + 1
            if start >= 0 and end > start:
                try:
                    return json.loads(t[start:end])
                except json.JSONDecodeError:
                    pass
        return None

    # ── Convenience ───────────────────────────────────────────────────────

    def available_providers(self) -> List[str]:
        """Return names of configured (available) providers."""
        return [p["name"] for p in self._providers]
