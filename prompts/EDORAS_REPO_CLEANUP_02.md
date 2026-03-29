# Claude Code Prompt — Edoras: Spring Cleaning & Documentation Overhaul

## Context

Edoras is a crypto trading system at `~/.openclaw/workspace/projects/edoras/`. It's been through rapid iteration since March 2026 — strategies, database schema, DEX integration, multi-portfolio support, warehouse redesign, real-time WebSocket feeds, and multiple agent integrations have all been added in quick succession.

The result: the codebase has accumulated dead files, outdated documentation, abandoned scripts, duplicate logic, and orphaned references. Old architectural decisions were never cleaned up when new ones replaced them. The `docs/` directory has 15 files (~69K tokens) with overlapping and contradictory information.

This matters for three reasons:
1. **Claude Code context** — every file in the workspace is context. Dead files waste tokens and confuse the model.
2. **LoRA training data** — we'll soon extract training pairs from this codebase. Stale code produces stale training data.
3. **Maintainability** — the system runs live trades. Dead code paths are landmines.

## Ground Rules

**DO NOT:**
- Delete any file that is imported by another active file
- Modify any logic in the signal pipeline, risk management, or execution path
- Change database schema (tables, columns, indexes)
- Modify any systemd timer or service configuration
- Delete the database itself or any data
- Change config.py parameters (thresholds, symbols, risk limits)
- Rename files that are referenced by systemd timers or shell scripts

**DO:**
- Move clearly dead files to `archive/deprecated/`
- Delete empty or trivially useless files
- Consolidate duplicate documentation into single authoritative files
- Add clear deprecation notices to files that will be removed later
- Update documentation to reflect current reality
- Create a clean production database reference document
- Create a clear project README that replaces the scattered docs

## Phase 1: Identify Dead Code

### Step 1a: Build the dependency graph

Before touching anything, map what imports what. Create a temporary script or use your analysis to answer:

```python
# For every .py file in the project (excluding archive/, tests/, __pycache__):
# 1. What does it import from the project? (local imports)
# 2. Is it imported by any other project file?
# 3. Is it referenced by any shell script (.sh file)?
# 4. Is it referenced by any systemd timer/service?

# Classify each file as:
# ACTIVE    — imported by other files OR referenced by systemd/shell
# ENTRY     — not imported but is a CLI entry point (has if __name__ == "__main__")
# ORPHAN    — not imported by anything, not an entry point, not in systemd
# ARCHIVE   — already in archive/ directory
```

Print the full classification. Do NOT delete or move anything yet — just report.

### Step 1b: Identify orphaned shell scripts

Check every `.sh` file in the project:
- Does the Python script it calls still exist?
- Is there a systemd timer that references this shell script?
- If neither: it's orphaned

### Step 1c: Check for duplicate functionality

Look for files that do the same thing:
- `backtester.py` (root) vs `backtest/engine.py` — are both needed?
- `paper_trading.py` vs `paper_rebalancing.py` — is rebalancing still separate?
- `crypto_data_collector.py` vs `daily_data_collection.py` — overlap?
- `automated_portfolio_report.py` vs `daily_portfolio_report.py` — same thing?
- `signal_alerts.py` vs `signal_trading.py` — does signal_alerts still serve a purpose?
- `portfolio_optimizer.py` vs `enhanced_optimizer.py` — which is current?
- `crypto_risk_analysis.py` — is this used or replaced by risk_manager.py?
- `run_optimization.py` — still used?
- `send_optimization_report.py` — still used?
- `send_paper_report.py` — still used?
- `send_rebalancing_report.py` — still used?
- `quick_market_scan.py` — still used?
- `schedule_random_reports.py` — still used?

Report findings. Don't delete yet.

## Phase 2: Archive Dead Files

Based on the Phase 1 analysis, move orphaned files to `archive/deprecated/` with a dated README:

```bash
mkdir -p archive/deprecated/2026-03-cleanup/
```

Create `archive/deprecated/2026-03-cleanup/README.md`:
```markdown
# Deprecated Files — March 2026 Cleanup

Files moved here during spring cleaning. They were identified as orphaned
(not imported by any active file, not referenced by systemd timers).

If something breaks after this cleanup, check here first.

## Files Moved
| File | Reason | Safe to Delete After |
|------|--------|---------------------|
| ... | ... | 2026-04-30 |
```

For each file moved, add an entry explaining WHY it's deprecated. Keep the files for 30 days before permanent deletion.

**Special handling:**
- Files in `archive/one-off/` and `archive/optimization/` and `archive/grid-search/` — these are already archived. Leave them. But note their total size.
- Files in `tests/` — leave all tests, even if they reference dead code. Tests are cheap insurance.

## Phase 3: Documentation Consolidation

The docs/ directory has 15 files totaling ~69K tokens with massive overlap. Consolidate into a clean, non-redundant set.

### Current docs inventory (from the audit):

| File | Tokens | Status |
|------|--------|--------|
| docs/ROADMAP/RealTime_Wealth_Management_System.md | ~16,502 | Outdated — describes a TimescaleDB migration that never happened |
| docs/ROADMAP/DATABASE_SCHEMA.md | ~9,703 | Partially current — has the real schema |
| docs/ROADMAP/QUICKSTART.md | ~varies | Outdated — references old setup steps |
| docs/ARCHITECTURE.md | ~5,251 | Partially current |
| docs/DOCUMENTATION.md | ~5,190 | Partially current, overlaps ARCHITECTURE.md |
| docs/TRADING_PHILOSOPHY.md | ~varies | Current — the trading rules reference |
| docs/CROSS_ASSET_PORTFOLIO_CHANGES.md | ~varies | Completed — change plan, now implemented |
| docs/DEX_INTEGRATION_ARCHITECTURE.md | ~varies | Partially current |
| docs/DEX_INTEGRATION_DIAGRAM.md | ~varies | Diagrams only, overlaps above |
| docs/DEX_INTEGRATION_REQUIREMENTS.md | ~varies | Completed — requirements doc |
| docs/POLYMARKET_SIGNAL_INTEGRATION.md | ~varies | Current |
| docs/PORTFOLIO_MANAGEMENT_STRATEGY.md | ~varies | Partially current, overlaps TRADING_PHILOSOPHY |
| docs/ROADMAP.md | ~varies | Current — the roadmap |
| docs/SCHEDULING_PLAN.md | ~varies | Outdated — superseded by systemd timers |
| docs/architecture-diagram.md | ~varies | Current — Mermaid diagrams |
| SYSTEM_REFERENCE.md (root) | ~7,738 | Current — master reference |

### Target documentation structure:

After consolidation, the docs should be:

```
docs/
├── ARCHITECTURE.md              # Single authoritative architecture doc
│                                 # Merges: ARCHITECTURE.md + architecture-diagram.md
│                                 # Remove: DOCUMENTATION.md (redundant)
│
├── DATABASE.md                   # NEW: Clean production database reference
│                                 # Replaces: ROADMAP/DATABASE_SCHEMA.md
│                                 # Focus: current schema, key queries, table purposes
│
├── TRADING_RULES.md              # Rename/clean TRADING_PHILOSOPHY.md
│                                 # Merge in relevant parts of PORTFOLIO_MANAGEMENT_STRATEGY.md
│
├── STRATEGIES.md                 # NEW: Strategy catalog with backtest results
│                                 # Extract from SYSTEM_REFERENCE.md + backtest data
│
├── POLYMARKET.md                 # Keep POLYMARKET_SIGNAL_INTEGRATION.md (already clean)
│
├── DEX.md                        # Consolidate DEX_INTEGRATION_*.md into one file
│                                 # Remove the three separate files
│
├── ROADMAP.md                    # Keep, update status markers
│
├── OPERATIONS.md                 # NEW: Scheduling, systemd timers, daily operations
│                                 # Replaces: SCHEDULING_PLAN.md
│                                 # Extract operational parts from SYSTEM_REFERENCE.md
│
└── archive/                      # Move deprecated docs here
    ├── RealTime_Wealth_Management_System.md  # TimescaleDB plan — never implemented
    ├── QUICKSTART.md                          # Outdated setup guide
    ├── CROSS_ASSET_PORTFOLIO_CHANGES.md       # Completed change plan
    ├── DEX_INTEGRATION_REQUIREMENTS.md        # Completed requirements
    └── PORTFOLIO_MANAGEMENT_STRATEGY.md       # Merged into TRADING_RULES.md
```

### Consolidation rules:

- **Never lose information** — if a doc has something unique, it goes into the consolidated version
- **Resolve contradictions** — when two docs disagree, check the code to determine which is correct
- **Remove aspirational content** — docs should describe what IS, not what we planned to build. Move plans to ROADMAP.md
- **Include dates** — every doc gets a "Last updated" date and a "Verified against code" date
- **Cross-reference, don't duplicate** — if ARCHITECTURE.md and TRADING_RULES.md both describe the signal pipeline, one should reference the other

### The SYSTEM_REFERENCE.md at project root:

This 7,738-token file is the most complete reference but it's too large and duplicates content from the docs/ files. After consolidation:
- Move operational content to docs/OPERATIONS.md
- Move strategy details to docs/STRATEGIES.md
- Move database details to docs/DATABASE.md
- What remains in SYSTEM_REFERENCE.md should be a concise (~2000 token) overview that points to the specific docs for details

## Phase 4: Production Database Documentation

Create `docs/DATABASE.md` — the authoritative reference for the Edoras database. This document will also be used to design data extraction jobs for LoRA training on the Mac Mini.

### What to include:

**4a: Schema overview**

Query the actual database and document what exists:

```sql
-- Get all tables
SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;

-- For each table: columns, types, constraints
PRAGMA table_info({table_name});

-- Foreign keys
PRAGMA foreign_key_list({table_name});

-- Indexes
SELECT * FROM sqlite_master WHERE type='index' AND tbl_name='{table_name}';

-- Row counts
SELECT COUNT(*) FROM {table_name};
```

For each table, document:
- Purpose (one sentence)
- Column list with types
- Foreign keys and relationships
- Row count (as of documentation date)
- Whether it's actively written to (check for recent timestamps)
- Which Python module writes to it
- Which Python module reads from it

**4b: Table classification**

Classify every table as:

| Category | Tables | Description |
|----------|--------|-------------|
| Core Market Data | candlesticks, indicators | Price data and technical indicators |
| Trading | trades, positions, trade_outcomes | Execution and results |
| Strategy | strategy_registry, strategy_performance, strategy_signals_log, portfolio_strategies, strategy_catalogue | Strategy config and tracking |
| Risk | risk_events | Risk monitoring |
| Portfolio | portfolios, accounts, paper_snapshots, portfolio_valuations, portfolio_performance | Portfolio management |
| Dimension | exchanges, securities, traders, trader_wallets, trader_portfolio_access | Reference data |
| Intelligence | market_memory, sentiment_scores, news_sentiment_stream, correlations, market_regime, market_regime_detailed | Analysis and memory |
| DEX | dex_tokens, dex_transactions | On-chain trading |
| System | system_metrics, collection_log | Operational |
| Legacy | paper_trades_legacy | Deprecated, kept for history |

**4c: Key queries for data extraction**

Document the most important queries — these are what the LoRA training data scripts will use:

```sql
-- Signal → outcome pairs (for training)
SELECT ssl.strategy_name, ssl.symbol, ssl.timeframe, ssl.action, ssl.strength,
       ssl.was_executed, ssl.adx, ssl.rsi, ssl.signal_time,
       to2.outcome_pct, to2.outcome_usd, to2.holding_hours, to2.exit_reason
FROM strategy_signals_log ssl
LEFT JOIN trade_outcomes to2 ON ssl.symbol = to2.symbol 
  AND to2.entry_date >= ssl.signal_time 
  AND to2.entry_date <= datetime(ssl.signal_time, '+1 hour')
WHERE ssl.was_executed = 1;

-- Trade reasoning examples (for training)
SELECT t.symbol, t.side, t.quantity, t.price, t.amount_usd,
       t.decision_context, t.created_at,
       tr.code as trader
FROM trades t
LEFT JOIN traders tr ON tr.id = t.trader_id
WHERE t.decision_context IS NOT NULL AND t.decision_context != '';

-- Strategy performance comparison
SELECT strategy_name, symbol, timeframe, sharpe_ratio, win_rate, 
       total_return, total_trades, max_drawdown
FROM strategy_performance
WHERE total_trades >= 3
ORDER BY sharpe_ratio DESC;

-- Current portfolio state
SELECT p.symbol, p.quantity, p.entry_price, p.current_price, 
       p.pnl, p.pnl_percent, p.status, p.entry_time,
       a.account_ref as venue
FROM positions p
LEFT JOIN accounts a ON p.account_id = a.id
WHERE p.status = 'open';

-- Daily NAV history
SELECT date, portfolio_value, cash, num_positions
FROM paper_snapshots
WHERE portfolio_id = 1
ORDER BY date DESC LIMIT 30;
```

**4d: Data freshness expectations**

| Table | Update Frequency | Source | Staleness Threshold |
|-------|-----------------|--------|-------------------|
| candlesticks (1h) | Every 2h via intraday_update | Coinbase REST | >4h = stale |
| candlesticks (5m) | Real-time via WebSocket | Coinbase WS | >15m = stale |
| indicators | After each candlestick insert | indicator_calculator | Same as candles |
| trades | On signal execution | signal_trading, trading_agent | N/A (event-driven) |
| positions | On every trade | paper_trading | N/A |
| strategy_signals_log | Every 4h (signal cycle) | signal_trading | >8h = stale |
| market_regime | Daily + on regime change | correlation_tracker, regime_monitor | >48h = stale |
| sentiment_scores | Daily | sentiment.py | >48h = stale |

**4e: Data extraction interface for Mac Mini**

Document how the Mac Mini training pipeline should pull data:

```bash
# Option A: SCP the entire database for offline analysis
scp user@laptop:~/.openclaw/workspace/projects/edoras/crypto_data.db /tmp/edoras_snapshot.db

# Option B: Export specific tables as CSV (lighter weight)
sqlite3 crypto_data.db ".headers on" ".mode csv" \
  "SELECT * FROM strategy_signals_log WHERE signal_time > date('now', '-30 days')" \
  > /tmp/signals_30d.csv

# Option C: Export as JSONL (ready for training)
sqlite3 crypto_data.db -json \
  "SELECT * FROM trades WHERE decision_context IS NOT NULL" \
  | python3 -c "import sys,json; [print(json.dumps(r)) for r in json.load(sys.stdin)]" \
  > /tmp/trade_reasoning.jsonl
```

Document which tables contain training-relevant data and estimated row counts.

## Phase 5: Clean Up the Root Directory

The project root has too many files at the top level. Identify files that should be moved into subdirectories:

```
# Current root (partial list from SYSTEM_REFERENCE.md):
config.py                     # KEEP — central config
indicator_calculator.py       # KEEP — core module
signal_trading.py             # KEEP — core module
paper_trading.py              # KEEP — core module
risk_manager.py               # KEEP — core module
risk_guardian.py               # KEEP — core module
trading_agent.py               # KEEP — core module
cli.py                         # KEEP — CLI entry point
...
crypto_risk_analysis.py       # PROBABLY DEAD — check
portfolio_optimizer.py        # PROBABLY DEAD — check against enhanced_optimizer
get_coinbase_symbols.py       # PROBABLY DEAD — one-time utility
run_optimization.py           # PROBABLY DEAD — check
send_optimization_report.py   # PROBABLY DEAD — check
send_paper_report.py          # PROBABLY DEAD — check
schedule_random_reports.py    # CHECK — still in systemd?
```

For files that are clearly utilities or one-off scripts but NOT dead:
- Keep them in root if they're systemd entry points
- Move to `scripts/` if they're manual utilities

## Phase 6: Update SYSTEM_REFERENCE.md

After all the above, rewrite `SYSTEM_REFERENCE.md` to be a concise (~2000 token) entry point that:

1. Describes what Edoras is (2-3 sentences)
2. Lists the core modules with one-line descriptions
3. Points to docs/ for details: "See docs/DATABASE.md for schema", "See docs/TRADING_RULES.md for strategy details"
4. Lists current portfolio symbols and strategy routing
5. Lists systemd timers with schedule
6. Has a "Quick Commands" section with the most useful CLI commands

This becomes the file that Claude Code (and the LoRA adapter) primarily reads for system understanding.

## Phase 7: Create a Clean Project README

Replace or update the root `README.md` to be a proper project README:

1. What Edoras does (one paragraph)
2. Architecture diagram (simple ASCII, not Mermaid — works everywhere)
3. Quick start (3 commands to get running)
4. Key commands (cli.py, signal_trading.py, report_engine.py)
5. Project structure (directory tree with one-line descriptions)
6. Documentation index (links to each doc in docs/)
7. Current status (active portfolios, symbols, strategies)

Keep it under 200 lines. The README is the first thing anyone reads (including Claude Code).

## Deliverables

After all phases, provide a summary report:

```markdown
# Cleanup Summary

## Files Archived
| File | Reason |
|------|--------|
| ... | ... |

## Files Deleted (empty/trivial)
| File | Reason |
|------|--------|

## Documentation Changes
| Action | File |
|--------|------|
| Created | docs/DATABASE.md |
| Created | docs/STRATEGIES.md |
| Created | docs/OPERATIONS.md |
| Consolidated | docs/ARCHITECTURE.md (merged architecture-diagram.md) |
| Consolidated | docs/TRADING_RULES.md (merged PORTFOLIO_MANAGEMENT_STRATEGY) |
| Consolidated | docs/DEX.md (merged 3 DEX files) |
| Archived | docs/archive/RealTime_Wealth_Management_System.md |
| ... | ... |

## Root Directory Changes
| Action | File | Destination |
|--------|------|-------------|
| Moved | get_coinbase_symbols.py | archive/deprecated/ |
| ... | ... | ... |

## Token Impact
- Before: ~69,071 tokens in docs/ + ~X tokens in root scripts
- After: ~X tokens in docs/ + ~X tokens in root scripts
- Reduction: ~X tokens (~Y%)

## Database Documentation
- Tables documented: X/Y
- Key queries documented: X
- Data extraction interface: documented in docs/DATABASE.md
```

## Important Notes

- **Run the dependency analysis FIRST before moving anything.** A file that looks dead might be imported dynamically or referenced by a shell script.
- **Check shell scripts for Python file references.** Many `.sh` files call Python scripts by path — if you move the Python file, the shell script breaks, and the systemd timer fails silently.
- **Check systemd timers.** Run `systemctl --user list-timers --all` and cross-reference every script mentioned against your file inventory.
- **Don't consolidate docs that are still changing.** ROADMAP.md is a living document — leave it as-is with updated status markers. TRADING_PHILOSOPHY.md is referenced by agents — consolidate carefully.
- **The SYSTEM_REFERENCE.md is read by OpenClaw agents.** If you move it or drastically shrink it, the agents need their workspace updated too.
- **Database documentation must come from querying the actual database**, not from existing docs (which may be outdated). The schema might have columns or tables not documented anywhere.
- **Back up before starting.** Run `git status` first to see uncommitted changes, then commit everything before the cleanup so you can revert if needed.
- **The Edoras project path is:** `~/.openclaw/workspace/projects/edoras/`
