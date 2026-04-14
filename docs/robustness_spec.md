# Edoras Daily Data Collection Robustness Improvements

## Current System
- `crypto-daily-analysis.service` (systemd) → `run_daily_analysis.sh` → `crypto_data_collector.py --daily`
- Collects daily/hourly candles for crypto symbols, calculates indicators, generates report.

## Known Weaknesses
1. **Environment dependency**: fails if edoras package not importable (Python path, pip install).
2. **No retry logic for transient failures (API limits, network timeouts)
3. Basic logging to `daily_analysis.log` (no rotation, limited structure)
4. Minimal health check after collection
5. Report detection flaky (fixed but could be better)
6. No monitoring of data quality or collection metrics

## Improvements Needed

### 1. Pre‑flight Verification Script
- Check Python environment (edoras package importable)
- Verify Coinbase API credentials exist and are valid
- Ensure database is accessible
- Log setup status

### 2. Retry with Exponential Backoff
- For API calls in `crypto_data_collector.py`, add retry decorator
- Handle Coinbase rate limits (429, 503)
- Max attempts 3, backoff factor 2

### 3. Improved Logging
- Rotate logs daily (`daily_analysis_YYYYMMDD.log`)
- Structured JSON logs for easier parsing
- Capture timing metrics per symbol
- Separate error log for failures

### 4. Post‑collection Health Check
- Verify candles inserted (count > 0)
- Check freshness (latest candle < threshold)
- Log data quality metrics (missing symbols, gaps)
- Alert on critical failures

### 5. Robust Report Detection
- Check multiple possible paths (`reports/`, `./`)
- Archive old reports with timestamp
- Validate report content before sending

### 6. Simple Monitoring
- Track success/failure rate in SQLite table
- Count data points collected per run
- Measure execution time
- Generate weekly summary

### 7. Optional Fallback Source
- If Coinbase fails, try yfinance for top symbols
- Mark data as fallback source in DB

## Constraints
- Do not break existing systemd service or scripts
- Maintain backward compatibility
- Keep changes incremental and testable
- Document all modifications

## Deliverables
1. Updated `run_daily_analysis.sh` with verification and better logging
2. Enhanced `crypto_data_collector.py` with retry logic
3. New helper modules as needed (retry, logging, health check)
4. Updated documentation in `README.md` or `AGENTS.md`
5. Test script to verify improvements work