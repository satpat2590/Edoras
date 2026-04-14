# Edoras Trading System -- Operations Runbook

Last updated: 2026-04-02

All times are **EDT (UTC-4)** unless noted. The system runs on local time via systemd user timers. Most timers include `RandomizedDelaySec` jitter to avoid API thundering herd.

---

## 1. Systemd Timers

### Continuous / High-Frequency

| Timer | Schedule | Description | Runs |
|-------|----------|-------------|------|
| `hermes-gateway-watchdog` | Every 60s | Health-checks the Hermes gateway; restarts if unresponsive or inactive | Inline bash (nc probe on port 18789) |
| `risk-guardian` | Every 30 min, 07:00--22:30 | Position stop-loss, trailing stop, take-profit checks | `run_risk_guardian.sh` -> `risk_guardian.py --check` |
| `crypto-price-alerts` | Every 30 min, 07:00--20:00 | Price movement alerts to Telegram | `price_alerts_cron.sh` -> `price_alerts.py --check` |
| `crypto-intraday-update` | Every 2h (odd hours: 01,03,...,23) | Fetches 1h candles, aggregates 4h, computes indicators | `intraday_update.py` (direct) |
| `dex-data-collection` | Every 2h (half hours: 00:30,02:30,...,22:30) | GeckoTerminal candle + metadata fetch for DEX tokens | `dex_data_collector.py --backfill 1` (direct) |

### 4-Hour Signal Cycle

| Timer | Schedule | Description | Runs |
|-------|----------|-------------|------|
| `crypto-signal-trading` | Every 4h at :05 (00:05,04:05,...,20:05) | Regime detection + signal generation + trade execution | `signal_trading.py --check` (direct) |
| `crypto-signal-alerts` | Every 4h (08:00,12:00,16:00,20:00) | Signal summary alerts to Telegram | `run_signal_alerts.sh` -> `signal_alerts.py --check --sentiment` |
| `polymarket-ingest` | Every 4h at :15 (00:15,04:15,...,20:15) | Polymarket REST gap-fill: new markets + price history | `providers/polymarket.py` (direct) |

### Daily Timers

| Timer | Schedule | Description | Runs |
|-------|----------|-------------|------|
| `equity-daily-update` | 05:00 | Equity & index price data update | `equity_data_collector.py --update` (direct) |
| `crypto-price-alerts` | 07:00--20:00 | (see above, high-frequency) | -- |
| `company-financials` | 07:30 | Company financial data collection | `company_financials.py --collect` (separate project) |
| `correlation-snapshot` | 08:00 | Cross-asset correlation matrix snapshot | `correlation_tracker.py --snapshot` (direct) |
| `news-digest` | 08:00, 16:00 | Morning & afternoon news digest | `utilities/news_digest_cron.sh` (workspace-level) |
| `crypto-daily-analysis` | 08:30 | Full daily data collection + technical analysis + Telegram report | `run_daily_analysis.sh` -> `crypto_data_collector.py --daily` |
| `trading-agent` | 08:45 | Daily trading review (runs after analysis/correlation/equity) | `run_trading_agent.sh` -> `trading_agent.py --run` |
| `crypto-portfolio-snapshot` | 09:00 | Portfolio snapshot + Telegram delivery | `daily_report_cron.sh` -> `daily_portfolio_report.py --auto-send` |
| `midday-trading-review` | 12:30, 16:30 | Tactical midday position review | `run_midday_review.sh` -> `trading_agent.py --midday` |
| `paper-portfolio-report` | 17:00 | Paper portfolio EOD performance report | `run_paper_report.sh` -> `paper_trading.py --report` |
| `edoras-daily-reports` | 17:30 | Generate all PDF reports + deliver via Telegram | `run_daily_reports.sh` -> `report_engine.py all` |
| `research-reader` | 21:30 | Evening arXiv paper reading (3 papers) | `run_research_reader.sh` -> `research_reader.py --read --papers 3` |

### Weekly Timers

| Timer | Schedule | Description | Runs |
|-------|----------|-------------|------|
| `gateway-weekly-restart` | Sunday 01:00 | Preemptive gateway restart for memory pressure relief | Inline bash (logs to outage-log.jsonl, restarts gateway) |
| `crypto-weekly-backfill` | Sunday 02:00 | 14-day historical gap-fill backfill | `historical_backfill.py --days 14` (direct) |
| `equity-full-collect` | Sunday 03:00 | Full equity historical collection | `equity_data_collector.py --collect` (direct) |
| `paper-portfolio-rebalancing` | Monday 09:00 | Weekly paper portfolio rebalancing | `run_paper_rebalancing.sh` -> `paper_rebalancing.py` |

### Other

| Timer | Schedule | Description | Runs |
|-------|----------|-------------|------|
| `crypto-random-scheduler` | 03:00 daily | Schedules random-time portfolio reports via `at` | `schedule_daily.sh` -> `schedule_random_reports.py` |

### Always-On Services (No Timer)

| Service | Description |
|---------|-------------|
| `coinbase-websocket` | Real-time WebSocket market data feed (auto-restarts, 512M memory cap) |
| `hermes-gateway` | Hermes gateway on port 18789 (auto-restarts, 2G memory cap) |

---

## 2. Signal Pipeline (4h Cycle)

Each 4-hour cycle follows this sequence:

```
1. Data Collection        intraday_update.py (every 2h)
   Fetch 1h candles from Coinbase, aggregate to 4h

2. Indicator Computation  (inside intraday_update)
   RSI, MACD, Bollinger Bands, ATR, etc.

3. Regime Detection       regime_monitor.py (called by signal_trading.py)
   HMM 3-state GaussianHMM with heuristic fallback
   Determines: trending / mean-reverting / volatile

4. Signal Generation      signal_trading.py --check (every 4h at :05)
   13 backtested strategies evaluate, regime-aware routing
   Polymarket overlay boosts/creates signals
   Risk manager pre-filters: stop-loss, trailing, circuit breaker

5. Trade Execution        signal_trading.py (same run)
   Orders placed via Coinbase API (Galadriel paper, Arwen DEX live)

6. Reporting              signal_alerts.py (every 4h, offset from trading)
   Summarizes signals fired/skipped, sends to Telegram
```

The intraday update runs every 2h so data is always fresh before the 4h signal check at :05.

---

## 3. Daily Schedule (Chronological, EDT)

```
00:05   crypto-signal-trading     4h signal cycle
00:15   polymarket-ingest         Polymarket gap-fill
00:30   dex-data-collection       DEX candle fetch
01:00   crypto-intraday-update    2h data refresh
        (Sunday: gateway-weekly-restart)
02:00   (Sunday: crypto-weekly-backfill)
03:00   crypto-random-scheduler   Schedule random reports
        (Sunday: equity-full-collect)
04:05   crypto-signal-trading     4h signal cycle
04:15   polymarket-ingest
04:30   dex-data-collection
05:00   equity-daily-update       Equity & index prices
        crypto-intraday-update    2h data refresh
06:30   dex-data-collection
07:00   crypto-price-alerts       First price alert of the day
        crypto-intraday-update    2h data refresh
        risk-guardian             First risk check
07:30   crypto-price-alerts, risk-guardian (30-min cycles continue)
        company-financials        Financial data pull
08:00   correlation-snapshot      Cross-asset correlations
        news-digest               Morning news
        crypto-signal-alerts      Signal summary
08:05   crypto-signal-trading     4h signal cycle
08:15   polymarket-ingest
08:30   crypto-daily-analysis     Full daily tech analysis
        dex-data-collection
08:45   trading-agent             Daily trading review
09:00   crypto-portfolio-snapshot Portfolio snapshot
        crypto-intraday-update    2h data refresh
        (Monday: paper-portfolio-rebalancing)
10:30   dex-data-collection
11:00   crypto-intraday-update
12:05   crypto-signal-trading     4h signal cycle
12:15   polymarket-ingest
12:30   midday-trading-review     Midday tactical review
        dex-data-collection
13:00   crypto-intraday-update
14:30   dex-data-collection
15:00   crypto-intraday-update
16:00   news-digest               Afternoon news
        crypto-signal-alerts      Signal summary
16:05   crypto-signal-trading     4h signal cycle
16:15   polymarket-ingest
16:30   midday-trading-review     Afternoon tactical review
        dex-data-collection
17:00   paper-portfolio-report    Paper portfolio EOD
        crypto-intraday-update
17:30   edoras-daily-reports      Generate + deliver all PDF reports
18:30   dex-data-collection
19:00   crypto-intraday-update
20:00   crypto-signal-alerts      Signal summary
20:05   crypto-signal-trading     4h signal cycle
20:15   polymarket-ingest
20:30   dex-data-collection
21:00   crypto-intraday-update
21:30   research-reader           Evening arXiv reading
22:00   risk-guardian             Last risk check
22:30   dex-data-collection
23:00   crypto-intraday-update    Last intraday update
```

Risk guardian runs every 30 minutes from 07:00 to 22:30 (not shown individually after 07:30).
Price alerts run every 30 minutes from 07:00 to 20:00 (not shown individually).

---

## 4. Shell Scripts

All scripts live in the project root: `~/edoras/`

| Script | Wraps | Called By |
|--------|-------|-----------|
| `run_daily_analysis.sh` | `crypto_data_collector.py --daily` | `crypto-daily-analysis.service` |
| `run_daily_data_collection.sh` | `daily_data_collection.py` | Manual use |
| `run_daily_reports.sh` | `report_engine.py all` + Telegram delivery | `edoras-daily-reports.service` |
| `run_midday_review.sh` | `trading_agent.py --midday` | `midday-trading-review.service` |
| `run_paper_rebalancing.sh` | `paper_rebalancing.py` | `paper-portfolio-rebalancing.service` |
| `run_paper_report.sh` | `paper_trading.py --report` | `paper-portfolio-report.service` |
| `run_portfolio_report.sh` | `automated_portfolio_report.py` | `at` scheduler (random times) |
| `run_research_reader.sh` | `research_reader.py --read --papers 3` | `research-reader.service` |
| `run_risk_guardian.sh` | `risk_guardian.py --check` | `risk-guardian.service` |
| `run_signal_alerts.sh` | `signal_alerts.py --check --sentiment` | `crypto-signal-alerts.service` |
| `run_trading_agent.sh` | `trading_agent.py --run` | `trading-agent.service` |
| `daily_report_cron.sh` | `daily_portfolio_report.py --auto-send` | `crypto-portfolio-snapshot.service` |
| `price_alerts_cron.sh` | `price_alerts.py --check` | `crypto-price-alerts.service` |
| `schedule_daily.sh` | `schedule_random_reports.py` | `crypto-random-scheduler.service` |

---

## 5. Environment Setup

### Coinbase Credentials

Most services load credentials via `EnvironmentFile=/home/satyamini/.config/coinbase.env`. For manual runs:

```bash
set -a && source ~/.config/coinbase.env && set +a
```

The shell scripts also extract credentials from `~/.zshrc` as a fallback (grepping for `COINBASE_API_KEY`, `COINBASE_API_SECRET`, etc.).

### Node.js / Hermes CLI

All scripts hardcode the nvm path:

```bash
export PATH="$HOME/.nvm/versions/node/v22.22.1/bin:$PATH"
```

The `hermes` CLI is used for Telegram message delivery.

### Telegram

`TELEGRAM_CHAT_ID` is expected in the environment (from `coinbase.env`).
`TELEGRAM_BOT_TOKEN` is set in the `edoras-daily-reports.service` unit directly.

### Manual Script Invocation

```bash
cd ~/edoras
set -a && source ~/.config/coinbase.env && set +a
export PATH="$HOME/.nvm/versions/node/v22.22.1/bin:$PATH"

# Now run any script
python3 cli.py health
python3 signal_trading.py --test
bash run_daily_analysis.sh
```

---

## 6. Health Monitoring

### Quick Health Check

```bash
cd ~/edoras
python3 cli.py health
```

Shows: data freshness per timeframe (1h/4h/1d), open position count, cash balance, 24h trade count, and systemd timer status.

### Timer Status

```bash
systemctl --user list-timers --all --no-pager
```

Check for timers where NEXT shows `n/a` (disabled) or where PASSED is unusually old.

### Service Status (Individual)

```bash
systemctl --user status crypto-signal-trading.service
systemctl --user status coinbase-websocket.service
systemctl --user status hermes-gateway.service
```

### Gateway Health

```bash
# Is the gateway alive?
systemctl --user is-active hermes-gateway.service

# Port check
nc -zw2 127.0.0.1 18789 && echo "OK" || echo "DOWN"

# Outage history
cat ~/.hermes/workspace/monitoring/outage-log.jsonl | tail -10
```

### WebSocket Feed

```bash
systemctl --user status coinbase-websocket.service
journalctl --user -u coinbase-websocket -n 20 --no-pager
```

### Data Freshness (SQL)

```bash
cd ~/edoras
python3 -c "
import sqlite3, time
conn = sqlite3.connect('crypto_data.db')
for tf in ['1h','4h','1d']:
    row = conn.execute('SELECT MAX(timestamp) FROM candlesticks WHERE timeframe=?',(tf,)).fetchone()
    age = (time.time()-row[0])/3600 if row and row[0] else None
    print(f'  {tf}: {age:.1f}h ago' if age else f'  {tf}: NO DATA')
conn.close()
"
```

---

## 7. Log Locations

### Journald (Primary)

All systemd services log to the journal. Query by unit:

```bash
journalctl --user -u crypto-signal-trading -n 50 --no-pager
journalctl --user -u crypto-daily-analysis --since "today" --no-pager
journalctl --user -u risk-guardian --since "1 hour ago" --no-pager
journalctl --user -u coinbase-websocket -f          # Live tail
journalctl --user -u hermes-gateway -n 100
journalctl --user -u dex-data-collection --since "today"
journalctl --user -u polymarket-ingest --since "today"
```

### File Logs (In Project Root)

Some shell scripts also write to local log files:

| Log File | Written By |
|----------|-----------|
| `daily_analysis.log` | `run_daily_analysis.sh` |
| `data_collection_run.log` | `run_daily_data_collection.sh` |
| `signal_alerts.log` | `run_signal_alerts.sh` |
| `trading_agent.log` | `run_trading_agent.sh` |
| `trading_agent_midday.log` | `run_midday_review.sh` |
| `run_portfolio_report.log` | `run_portfolio_report.sh` |
| `scheduler.log` | `schedule_daily.sh` |
| `logs/daily_report_*.log` | `daily_report_cron.sh` |
| `logs/price_alerts_*.log` | `price_alerts_cron.sh` |

### Monitoring

```
~/.hermes/workspace/monitoring/outage-log.jsonl   # Gateway restart events
```

---

## 8. Troubleshooting

### Stale Data (cli.py health shows old timestamps)

1. Check if `crypto-intraday-update` ran recently:
   ```bash
   systemctl --user status crypto-intraday-update.service
   journalctl --user -u crypto-intraday-update -n 20 --no-pager
   ```
2. Check if the Coinbase WebSocket is connected:
   ```bash
   systemctl --user status coinbase-websocket.service
   ```
3. Manual refresh:
   ```bash
   cd ~/edoras
   set -a && source ~/.config/coinbase.env && set +a
   python3 intraday_update.py
   ```

### Timer Not Firing

1. Verify the timer is enabled and active:
   ```bash
   systemctl --user status crypto-signal-trading.timer
   ```
2. If inactive, re-enable:
   ```bash
   systemctl --user enable --now crypto-signal-trading.timer
   ```
3. Check if the service itself failed (blocks the next timer run for oneshot):
   ```bash
   systemctl --user status crypto-signal-trading.service
   systemctl --user reset-failed crypto-signal-trading.service
   ```

### Gateway Down

The watchdog checks every 60 seconds and auto-restarts. If it stays down:

```bash
journalctl --user -u hermes-gateway -n 50 --no-pager
systemctl --user restart hermes-gateway.service
```

Check for port conflicts or memory exhaustion (2G cap).

### Signal Trading Not Executing

1. Check regime state:
   ```bash
   python3 regime_monitor.py --detect-only
   ```
2. Check risk state for circuit breaker:
   ```bash
   cat risk_state.json
   ```
   The circuit breaker auto-resets after 24h cooldown (with no positions) or when cash ≥ 80% of portfolio.
   To force-reset manually:
   ```bash
   python3 -c "from risk_manager import RiskManager; rm = RiskManager(); rm.reset_circuit_breaker()"
   ```
3. Dry run:
   ```bash
   set -a && source ~/.config/coinbase.env && set +a
   python3 signal_trading.py --test
   ```
4. Check recent signals:
   ```bash
   python3 cli.py signals --hours 24
   ```

### Coinbase API Errors

- Rate limiting: check for 429 responses in journal logs
- Key expiry: verify credentials in `~/.config/coinbase.env` and `~/.zshrc`
- Test connectivity:
  ```bash
  set -a && source ~/.config/coinbase.env && set +a
  python3 -c "from coinbase_client import CoinbaseClient; c=CoinbaseClient(); print(c.get_accounts()[:1])"
  ```

### Telegram Delivery Failures

- Verify the hermes CLI is reachable:
  ```bash
  export PATH="$HOME/.nvm/versions/node/v22.22.1/bin:$PATH"
  hermes --version
  ```
- Check the gateway is running (Telegram messages route through it)
- Test send:
  ```bash
  hermes message send --channel telegram --target "$TELEGRAM_CHAT_ID" --message "test"
  ```

### DEX Data Collection Failures

```bash
journalctl --user -u dex-data-collection --since "today" --no-pager
```

GeckoTerminal API can rate-limit aggressively. The collector uses `--backfill 1` (1 day) to stay within limits.

### Reloading After Config Changes

After editing any `.timer` or `.service` file:

```bash
systemctl --user daemon-reload
systemctl --user restart <unit-name>.timer
```
