#!/usr/bin/env python3
"""
Data Freshness Monitor

Checks per-symbol, per-timeframe data staleness in the candlesticks table and
alerts via Telegram when feeds go stale.  Also detects missing candle gaps
within continuous time ranges.

Designed to run every 15 minutes via a systemd timer.

Usage:
    python3 data_freshness_monitor.py            # check all, alert on failures
    python3 data_freshness_monitor.py --report   # print full report to stdout
    python3 data_freshness_monitor.py --gaps     # also run gap detection
    python3 data_freshness_monitor.py --symbol BTC-USD --timeframe 4h
"""

import os
import sys
import sqlite3
import subprocess
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    DB_PATH,
    PORTFOLIO_SYMBOLS,
    TOP_CRYPTO_SYMBOLS,
    EQUITY_SYMBOLS,
    INDEX_SYMBOLS,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ── Staleness thresholds (seconds) ───────────────────────────────────────────
# How old the latest candle can be before we treat the feed as stale.
# Thresholds are deliberately generous to avoid false positives.

THRESHOLDS: Dict[str, int] = {
    "5m": 60 * 20,  # 20 min  (data should arrive every 5 min)
    "1h": 3600 * 3,  # 3 hours (data updates every 1–2 hours)
    "4h": 3600 * 10,  # 10 hours (slightly more than 2 periods + safety margin)
    "1d": 3600 * 28,  # 28 hours (daily candle + market close lag)
}

# Crypto feeds are 24/7; equity feeds are market-hours only.
# During weekends / after-hours, equity 1h data will naturally be stale.
# Equity thresholds are loosened to avoid weekend false positives.
EQUITY_THRESHOLDS: Dict[str, int] = {
    "1h": 3600 * 96,  # 4 days (covers weekend + 1 holiday)
    "4h": 3600 * 96,
    "1d": 3600 * 120,  # 5 days (covers a full long weekend)
}

# Symbols that should be checked per timeframe.
CRYPTO_SYMBOLS = list(dict.fromkeys(PORTFOLIO_SYMBOLS + TOP_CRYPTO_SYMBOLS))
EQUITY_ALL = list(dict.fromkeys(EQUITY_SYMBOLS + INDEX_SYMBOLS))

# Timeframes to check per asset class
CRYPTO_TIMEFRAMES = ["1h", "4h", "1d"]
EQUITY_TIMEFRAMES = ["1h", "4h", "1d"]

# Gap detection: maximum acceptable consecutive missing candles
GAP_THRESHOLD = {
    "1h": 3,  # alert if 3+ consecutive 1h candles are missing
    "4h": 2,  # alert if 2+ consecutive 4h candles are missing
    "1d": 2,  # alert if 2+ consecutive daily candles are missing
}
CANDLE_SECONDS = {"1h": 3600, "4h": 14400, "1d": 86400}
GAP_LOOKBACK_DAYS = 7  # how far back to check for gaps


# ── Core check logic ─────────────────────────────────────────────────────────


class FreshnessResult:
    """Result for a single (symbol, timeframe) check."""

    def __init__(
        self,
        symbol: str,
        timeframe: str,
        latest_ts: Optional[int],
        threshold_secs: int,
        is_equity: bool = False,
    ):
        self.symbol = symbol
        self.timeframe = timeframe
        self.latest_ts = latest_ts
        self.threshold_secs = threshold_secs
        self.is_equity = is_equity

        now = int(datetime.now(timezone.utc).timestamp())
        if latest_ts is None:
            self.age_secs = None
            self.is_stale = True
            self.status = "MISSING"
        else:
            self.age_secs = now - latest_ts
            self.is_stale = self.age_secs > threshold_secs
            self.status = "STALE" if self.is_stale else "OK"

    @property
    def age_hours(self) -> Optional[float]:
        return self.age_secs / 3600 if self.age_secs is not None else None

    def __str__(self):
        if self.age_hours is None:
            return f"{self.symbol}/{self.timeframe}: MISSING"
        return (
            f"{self.symbol}/{self.timeframe}: {self.status} "
            f"({self.age_hours:.1f}h old, threshold {self.threshold_secs / 3600:.0f}h)"
        )


class GapResult:
    """Detected gap in candle data."""

    def __init__(
        self,
        symbol: str,
        timeframe: str,
        gap_start: int,
        gap_end: int,
        missing_count: int,
    ):
        self.symbol = symbol
        self.timeframe = timeframe
        self.gap_start = gap_start
        self.gap_end = gap_end
        self.missing_count = missing_count

    def __str__(self):
        start_str = datetime.utcfromtimestamp(self.gap_start).strftime("%Y-%m-%d %H:%M")
        end_str = datetime.utcfromtimestamp(self.gap_end).strftime("%Y-%m-%d %H:%M")
        return (
            f"{self.symbol}/{self.timeframe}: {self.missing_count} missing candles "
            f"({start_str} → {end_str})"
        )


class DataFreshnessMonitor:
    """Check and report on data freshness and gaps."""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path

    # ── Freshness checks ─────────────────────────────────────────────────

    def check_symbol(
        self,
        symbol: str,
        timeframe: str,
        is_equity: bool = False,
    ) -> FreshnessResult:
        """Check freshness for a single (symbol, timeframe) pair."""
        threshold_map = EQUITY_THRESHOLDS if is_equity else THRESHOLDS
        threshold = threshold_map.get(timeframe, THRESHOLDS.get(timeframe, 3600 * 6))

        try:
            conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
            row = conn.execute(
                "SELECT MAX(timestamp) FROM candlesticks WHERE symbol=? AND timeframe=?",
                (symbol, timeframe),
            ).fetchone()
            conn.close()
            latest_ts = row[0] if row and row[0] else None
        except Exception as e:
            logger.warning(f"DB query failed for {symbol}/{timeframe}: {e}")
            latest_ts = None

        return FreshnessResult(symbol, timeframe, latest_ts, threshold, is_equity)

    def check_all(self) -> Tuple[List[FreshnessResult], List[FreshnessResult]]:
        """Check freshness for all tracked symbols.

        Returns (ok_results, stale_results).
        """
        ok: List[FreshnessResult] = []
        stale: List[FreshnessResult] = []

        for symbol in CRYPTO_SYMBOLS:
            for tf in CRYPTO_TIMEFRAMES:
                r = self.check_symbol(symbol, tf, is_equity=False)
                (stale if r.is_stale else ok).append(r)

        for symbol in EQUITY_ALL:
            for tf in EQUITY_TIMEFRAMES:
                r = self.check_symbol(symbol, tf, is_equity=True)
                (stale if r.is_stale else ok).append(r)

        return ok, stale

    # ── Gap detection ────────────────────────────────────────────────────

    def detect_gaps(
        self,
        symbol: str,
        timeframe: str,
        lookback_days: int = GAP_LOOKBACK_DAYS,
    ) -> List[GapResult]:
        """Detect missing candle intervals for a symbol/timeframe.

        Skips equity symbols outside market hours (weekends / overnight).
        Returns list of GapResult objects, one per contiguous gap block.
        """
        interval = CANDLE_SECONDS.get(timeframe)
        if not interval:
            return []  # gap detection only for standard timeframes

        now = int(datetime.now(timezone.utc).timestamp())
        cutoff = now - (lookback_days * 86400)

        try:
            conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
            rows = conn.execute(
                "SELECT timestamp FROM candlesticks "
                "WHERE symbol=? AND timeframe=? AND timestamp>=? "
                "ORDER BY timestamp",
                (symbol, timeframe, cutoff),
            ).fetchall()
            conn.close()
        except Exception as e:
            logger.warning(f"Gap detection DB error for {symbol}/{timeframe}: {e}")
            return []

        if len(rows) < 2:
            return []

        timestamps = [r[0] for r in rows]
        gaps: List[GapResult] = []
        threshold = GAP_THRESHOLD.get(timeframe, 3)

        for i in range(1, len(timestamps)):
            expected = timestamps[i - 1] + interval
            actual = timestamps[i]
            missing_count = (actual - expected) // interval
            if missing_count >= threshold:
                gaps.append(
                    GapResult(
                        symbol=symbol,
                        timeframe=timeframe,
                        gap_start=expected,
                        gap_end=actual,
                        missing_count=missing_count,
                    )
                )

        return gaps

    def detect_all_gaps(self) -> List[GapResult]:
        """Detect gaps across all tracked crypto symbols for 1h/4h/1d."""
        all_gaps: List[GapResult] = []
        check_tfs = [tf for tf in CRYPTO_TIMEFRAMES if tf in CANDLE_SECONDS]
        for symbol in CRYPTO_SYMBOLS:
            for tf in check_tfs:
                all_gaps.extend(self.detect_gaps(symbol, tf))
        return all_gaps

    # ── Reporting ────────────────────────────────────────────────────────

    def format_report(
        self,
        ok: List[FreshnessResult],
        stale: List[FreshnessResult],
        gaps: List[GapResult] = None,
    ) -> str:
        """Format a human-readable freshness report."""
        lines = [
            "=" * 60,
            f"DATA FRESHNESS REPORT — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
            "=" * 60,
            f"Checked: {len(ok) + len(stale)} feeds  |  OK: {len(ok)}  |  STALE: {len(stale)}",
        ]

        if stale:
            lines.append("")
            lines.append(f"STALE / MISSING ({len(stale)}):")
            for r in sorted(stale, key=lambda r: (r.symbol, r.timeframe)):
                lines.append(f"  {r}")

        if ok:
            lines.append("")
            lines.append(f"OK ({len(ok)}):")
            # Group by symbol for readability
            by_sym: Dict[str, List[str]] = {}
            for r in sorted(ok, key=lambda r: (r.symbol, r.timeframe)):
                by_sym.setdefault(r.symbol, []).append(
                    f"{r.timeframe}:{r.age_hours:.1f}h"
                    if r.age_hours is not None
                    else f"{r.timeframe}:OK"
                )
            for sym, tfs in by_sym.items():
                lines.append(f"  {sym}: {', '.join(tfs)}")

        if gaps:
            lines.append("")
            lines.append(f"GAPS DETECTED ({len(gaps)}):")
            for g in gaps:
                lines.append(f"  {g}")
        elif gaps is not None:
            lines.append("")
            lines.append("No gaps detected.")

        lines.append("=" * 60)
        return "\n".join(lines)

    # ── Telegram alerting ────────────────────────────────────────────────

    @staticmethod
    def _send_telegram(message: str):
        """Send a Telegram message using the bot token from environment."""
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        if not bot_token or not chat_id:
            logger.warning("Telegram env vars not set — alert not sent")
            return

        # Truncate to Telegram's 4096 char limit
        if len(message) > 3900:
            message = message[:3900] + "\n... (truncated)"

        try:
            result = subprocess.run(
                [
                    "curl",
                    "-s",
                    "-X",
                    "POST",
                    f"https://api.telegram.org/bot{bot_token}/sendMessage",
                    "-d",
                    f"chat_id={chat_id}",
                    "-d",
                    f"text={message}",
                    "-d",
                    "parse_mode=Markdown",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                logger.info("Freshness alert sent to Telegram")
            else:
                logger.warning(
                    f"Telegram send failed (exit {result.returncode}): {result.stderr}"
                )
        except Exception as e:
            logger.warning(f"Telegram send exception: {e}")

    def build_alert_message(
        self,
        stale: List[FreshnessResult],
        gaps: List[GapResult] = None,
    ) -> str:
        """Build a concise Telegram alert for stale feeds and gaps."""
        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        lines = [f"*EDORAS DATA ALERT* — {now_str}", ""]

        if stale:
            lines.append(f"*Stale / Missing Feeds ({len(stale)}):*")
            for r in sorted(stale, key=lambda r: (r.symbol, r.timeframe)):
                if r.age_hours is None:
                    lines.append(f"  `{r.symbol}/{r.timeframe}`: NO DATA")
                else:
                    lines.append(
                        f"  `{r.symbol}/{r.timeframe}`: {r.age_hours:.1f}h old"
                        f" (max {r.threshold_secs // 3600}h)"
                    )

        if gaps:
            lines.append("")
            lines.append(f"*Data Gaps Detected ({len(gaps)}):*")
            for g in gaps[:10]:  # cap at 10 to avoid message explosion
                lines.append(
                    f"  `{g.symbol}/{g.timeframe}`: {g.missing_count} missing candles"
                )
            if len(gaps) > 10:
                lines.append(f"  ... and {len(gaps) - 10} more")

        lines.append("")
        lines.append("Check: `python3 cli.py health`")
        return "\n".join(lines)


# ── Entry point ───────────────────────────────────────────────────────────────


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Data Freshness Monitor")
    parser.add_argument(
        "--report", action="store_true", help="Print full report to stdout"
    )
    parser.add_argument(
        "--gaps", action="store_true", help="Run gap detection in addition to freshness"
    )
    parser.add_argument("--symbol", type=str, help="Check a single symbol")
    parser.add_argument(
        "--timeframe", type=str, help="Timeframe for single-symbol check"
    )
    parser.add_argument(
        "--no-alert", action="store_true", help="Suppress Telegram alerts"
    )
    parser.add_argument(
        "--db", type=str, default=DB_PATH, help="Override database path"
    )
    args = parser.parse_args()

    monitor = DataFreshnessMonitor(db_path=args.db)

    # ── Single-symbol check ──────────────────────────────────────────────
    if args.symbol:
        tf = args.timeframe or "1h"
        is_eq = args.symbol in EQUITY_ALL
        result = monitor.check_symbol(args.symbol, tf, is_equity=is_eq)
        print(result)
        if args.gaps:
            gaps = monitor.detect_gaps(args.symbol, tf)
            if gaps:
                for g in gaps:
                    print(f"  GAP: {g}")
            else:
                print("  No gaps detected.")
        sys.exit(0 if not result.is_stale else 1)

    # ── Full check ───────────────────────────────────────────────────────
    ok, stale = monitor.check_all()
    gaps: List[GapResult] = []
    if args.gaps:
        gaps = monitor.detect_all_gaps()

    if args.report:
        print(monitor.format_report(ok, stale, gaps if args.gaps else None))

    # Summary to logger always
    logger.info(
        f"Freshness check complete: {len(ok)} OK, {len(stale)} stale"
        + (f", {len(gaps)} gaps" if args.gaps else "")
    )

    # Log each stale feed at WARNING level
    for r in stale:
        logger.warning(f"STALE: {r}")
    for g in gaps:
        logger.warning(f"GAP: {g}")

    # ── Alert if anything is stale ───────────────────────────────────────
    if (stale or gaps) and not args.no_alert:
        message = monitor.build_alert_message(stale, gaps if args.gaps else None)
        monitor._send_telegram(message)
        logger.info(f"Alert sent: {len(stale)} stale feeds, {len(gaps)} gaps")

    # Exit code: 0 = all fresh, 1 = stale data detected
    sys.exit(0 if not stale else 1)


if __name__ == "__main__":
    main()
