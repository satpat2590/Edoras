# DEX Integration Requirements Document

## Project: Edoras DEX Extension
**Version:** 1.0  
**Date:** 2026-03-14  
**Status:** Proposed Architecture

## Executive Summary

Extend the existing Edoras multi-asset trading system to support Base chain DEX trading via Bankr API. This document outlines functional requirements, technical specifications, and implementation plan.

## 1. Business Requirements

### 1.1 Objectives
1. **Diversify trading venues** beyond CEX-only (Coinbase) to include DEX markets
2. **Access memecoin and early-stage token opportunities** on Base chain
3. **Leverage Bankr's natural language trading** for simplified DEX operations
4. **Maintain existing risk management framework** while adding DEX-specific safeguards
5. **Enable 24/7 automated trading** across both CEX and DEX venues

### 1.2 Success Criteria
- DEX trading achieves >55% win rate in paper trading phase
- Average slippage <2% on DEX executions
- No trades executed on tokens with <$100k liquidity
- Integration completes within 6 weeks
- System handles 50+ DEX tokens alongside existing 30+ CEX symbols

## 2. Functional Requirements

### 2.1 Data Collection (FR-DATA)

| ID | Requirement | Priority | Description |
|----|-------------|----------|-------------|
| FR-DATA-01 | DEX Price Feeds | High | Collect real-time prices for Base DEX tokens via Bankr API |
| FR-DATA-02 | Liquidity Data | High | Track token liquidity across DEX pools |
| FR-DATA-03 | Volume Metrics | High | Monitor 24h trading volume for DEX tokens |
| FR-DATA-04 | Holder Statistics | Medium | Track unique holder counts and growth |
| FR-DATA-05 | Contract Metadata | Medium | Store token contract addresses and verification status |
| FR-DATA-06 | Multi-Source Validation | Medium | Cross-reference Bankr data with DeFi Llama/CoinGecko |

### 2.2 Signal Generation (FR-SIGNAL)

| ID | Requirement | Priority | Description |
|----|-------------|----------|-------------|
| FR-SIGNAL-01 | DEX-Specific Indicators | High | Calculate liquidity change, holder growth, volume ratios |
| FR-SIGNAL-02 | Liquidity Filters | High | Filter out tokens below minimum liquidity thresholds |
| FR-SIGNAL-03 | Volume Filters | High | Filter out tokens below minimum volume thresholds |
| FR-SIGNAL-04 | Age Filters | Medium | Filter out tokens younger than 7 days |
| FR-SIGNAL-05 | Signal Strength Adjustment | Medium | Adjust signal strength based on DEX metrics |
| FR-SIGNAL-06 | Multi-Timeframe Alignment | Low | Require alignment across 5m, 1h, 4h timeframes |

### 2.3 Risk Management (FR-RISK)

| ID | Requirement | Priority | Description |
|----|-------------|----------|-------------|
| FR-RISK-01 | DEX Position Limits | High | Max 10% of portfolio per DEX token |
| FR-RISK-02 | Slippage Protection | High | Cancel orders if slippage >5% |
| FR-RISK-03 | Gas Optimization | Medium | Execute during low gas periods (<30 gwei) |
| FR-RISK-04 | Contract Risk Assessment | Medium | Check for verified contracts, audit status |
| FR-RISK-05 | Circuit Breaker Extension | High | Extend existing 15% drawdown circuit breaker to DEX portfolio |
| FR-RISK-06 | Emergency Halt | High | Manual override to pause all DEX trading |

### 2.4 Execution (FR-EXEC)

| ID | Requirement | Priority | Description |
|----|-------------|----------|-------------|
| FR-EXEC-01 | Bankr API Integration | High | Execute swaps via Bankr natural language commands |
| FR-EXEC-02 | Order Types | Medium | Support market, limit, and stop-loss orders |
| FR-EXEC-03 | Multi-Token Swaps | Medium | Execute ETH→Token and Token→Token swaps |
| FR-EXEC-04 | Transaction Monitoring | High | Track transaction status and confirmations |
| FR-EXEC-05 | Failed Order Handling | High | Retry logic for failed transactions |
| FR-EXEC-06 | Portfolio Synchronization | High | Keep paper portfolio in sync with DEX positions |

### 2.5 Monitoring & Reporting (FR-MONITOR)

| ID | Requirement | Priority | Description |
|----|-------------|----------|-------------|
| FR-MONITOR-01 | DEX Health Dashboard | High | Monitor Bankr API status, gas prices, liquidity |
| FR-MONITOR-02 | Performance Reporting | High | Daily DEX performance reports via Telegram |
| FR-MONITOR-03 | Alert System | High | Alerts for: high slippage, low liquidity, failed trades |
| FR-MONITOR-04 | Audit Trail | Medium | Log all DEX transactions with timestamps and metadata |
| FR-MONITOR-05 | Reconciliation | Medium | Daily reconciliation of expected vs actual positions |

## 3. Technical Requirements

### 3.1 Database Schema Updates

**Table: `securities` (extensions)**
```sql
-- New columns
ALTER TABLE securities ADD COLUMN chain TEXT;
ALTER TABLE securities ADD COLUMN contract_address TEXT;
ALTER TABLE securities ADD COLUMN is_dex BOOLEAN DEFAULT FALSE;
ALTER TABLE securities ADD COLUMN dex_platform TEXT;
```

**New Table: `dex_tokens`**
```sql
CREATE TABLE dex_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    security_id INTEGER NOT NULL,
    chain TEXT NOT NULL,
    contract_address TEXT NOT NULL UNIQUE,
    dex_platform TEXT,
    liquidity REAL,
    holder_count INTEGER,
    created_at TIMESTAMP,
    last_updated TIMESTAMP,
    FOREIGN KEY (security_id) REFERENCES securities(id)
);
```

**New Table: `dex_transactions`**
```sql
CREATE TABLE dex_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    portfolio_id INTEGER NOT NULL,
    security_id INTEGER NOT NULL,
    tx_hash TEXT NOT NULL UNIQUE,
    action TEXT NOT NULL,  -- 'buy', 'sell', 'swap'
    amount REAL NOT NULL,
    price REAL NOT NULL,
    slippage REAL,
    gas_used REAL,
    status TEXT NOT NULL,  -- 'pending', 'confirmed', 'failed'
    created_at TIMESTAMP,
    confirmed_at TIMESTAMP,
    FOREIGN KEY (portfolio_id) REFERENCES portfolios(id),
    FOREIGN KEY (security_id) REFERENCES securities(id)
);
```

### 3.2 Configuration Requirements

**File: `config.py` additions**
```python
# DEX Configuration
DEX_CONFIG = {
    'enabled': True,
    'default_chain': 'base',
    
    # Risk thresholds
    'min_liquidity_usd': 100000,
    'min_volume_24h_usd': 50000,
    'max_slippage_percent': 5.0,
    'max_position_size_percent': 10.0,
    'min_token_age_days': 7,
    'min_holder_count': 100,
    
    # Bankr API
    'bankr_api_key': os.getenv('BANKR_API_KEY'),
    'bankr_api_url': 'https://api.bankr.bot',
    
    # Supported chains
    'supported_chains': ['base', 'ethereum', 'polygon', 'solana'],
    
    # Gas optimization
    'max_gas_price_gwei': 30,
    'gas_check_interval_minutes': 5,
}

# Initial DEX token universe
DEX_SYMBOLS = [
    {
        'symbol': 'VVV-BASE',
        'name': 'Venice Token',
        'chain': 'base',
        'contract': '0x...',
        'dex_platform': 'uniswap_v3'
    },
    {
        'symbol': 'FAI-BASE', 
        'name': 'FAI',
        'chain': 'base',
        'contract': '0x...',
        'dex_platform': 'aerodrome'
    },
    {
        'symbol': 'BNKR-BASE',
        'name': 'BankrCoin',
        'chain': 'base',
        'contract': '0x...',
        'dex_platform': 'uniswap_v3'
    },
    # Blue-chip DEX tokens
    {
        'symbol': 'ETH-BASE',
        'name': 'Ethereum',
        'chain': 'base', 
        'contract': '0x...',
        'dex_platform': 'native'
    },
    {
        'symbol': 'USDC-BASE',
        'name': 'USD Coin',
        'chain': 'base',
        'contract': '0x...',
        'dex_platform': 'native'
    },
]
```

### 3.3 Module Requirements

**New Module: `bankr_client.py`**
```python
"""
Bankr API client for DEX operations.
Requirements:
- Async HTTP client with retry logic
- Rate limiting (Bankr: 100 requests/day standard)
- Error handling for API failures
- Transaction status polling
- Portfolio balance fetching
"""
```

**New Module: `dex_data_collector.py`**
```python
"""
DEX data collection from multiple sources.
Requirements:
- Bankr API integration for prices
- DeFi Llama API for liquidity data
- CoinGecko API for market data
- Data normalization across sources
- Scheduled collection (every 5 minutes)
"""
```

**New Module: `dex_signal_engine.py`**
```python
"""
DEX-specific signal generation.
Requirements:
- DEX indicator calculation
- Liquidity/volume filtering
- Signal strength adjustment
- Integration with existing signal_trading.py
- Backtesting compatibility
"""
```

**New Module: `dex_executor.py`**
```python
"""
DEX trade execution via Bankr.
Requirements:
- Bankr natural language command generation
- Slippage protection
- Gas optimization
- Transaction monitoring
- Failed transaction handling
- Portfolio synchronization
"""
```

**New Module: `dex_monitor.py`**
```python
"""
DEX system monitoring and alerts.
Requirements:
- Bankr API health checks
- Gas price monitoring
- Liquidity threshold alerts
- Performance reporting
- Telegram alert integration
"""
```

### 3.4 Integration Requirements

**Integration Point 1: `indicator_calculator.py`**
- Add DEX-specific indicator methods
- Maintain backward compatibility with existing indicators
- Support both CEX and DEX data sources

**Integration Point 2: `signal_trading.py`**
- Extend signal generation to include DEX tokens
- Apply DEX-specific filters before signal generation
- Route DEX signals to `dex_executor.py`

**Integration Point 3: `risk_manager.py`**
- Extend risk checks to include DEX positions
- Add DEX-specific risk rules (liquidity, slippage, contract risk)
- Maintain unified circuit breaker across CEX and DEX

**Integration Point 4: `paper_trading.py`**
- Sync DEX positions with paper portfolio
- Track DEX transaction costs (gas, slippage)
- Calculate accurate P&L including fees

## 4. Non-Functional Requirements

### 4.1 Performance Requirements
- Data collection: <5 seconds per token
- Signal generation: <1 second per token
- Trade execution: <30 seconds end-to-end
- System should handle 100+ DEX tokens concurrently
- API rate limits: Respect Bankr's 100 requests/day (standard tier)

### 4.2 Reliability Requirements
- 99.9% uptime for monitoring components
- Automatic retry for failed API calls (3 attempts)
- Graceful degradation if Bankr API is unavailable
- Data persistence across system restarts

### 4.3 Security Requirements
- Bankr API key stored in environment variables, not code
- No private keys stored in database
- All DEX transactions logged for audit
- Emergency stop mechanism accessible via Telegram
- Regular security review of integration code

### 4.4 Maintainability Requirements
- Clear separation between CEX and DEX code
- Comprehensive unit test coverage (>80%)
- Documentation for all new modules
- Configuration-driven behavior (no hardcoded values)
- Logging at appropriate levels (DEBUG, INFO, WARN, ERROR)

## 5. Data Requirements

### 5.1 Data Sources
1. **Primary**: Bankr API (prices, execution)
2. **Secondary**: DeFi Llama API (liquidity, volume)
3. **Tertiary**: CoinGecko API (market data, metadata)
4. **On-chain**: Ethereum/BASE RPC (transaction status)

### 5.2 Data Quality Requirements
- Price data: <1 minute freshness
- Liquidity data: <15 minute freshness
- Volume data: <1 hour freshness
- Data validation: Cross-check across multiple sources
- Missing data handling: Skip token if data incomplete

### 5.3 Storage Requirements
- Historical DEX data: 90 days retention
- Transaction logs: Permanent retention
- Performance metrics: 1 year retention
- Estimated storage growth: 100MB/month

## 6. Testing Requirements

### 6.1 Unit Testing
- Test all new modules in isolation
- Mock Bankr API responses
- Test edge cases (low liquidity, high slippage)
- Achieve >80% code coverage

### 6.2 Integration Testing
- Test integration with existing Edoras modules
- Test Bankr API integration with sandbox (if available)
- Test database schema migrations
- Test error handling and recovery

### 6.3 Paper Trading Testing
- **Duration**: 30 days minimum
- **Portfolio**: Virtual $1,000
- **Tokens**: 10-20 DEX tokens
- **Metrics**: Compare vs CEX-only performance
- **Validation**: Manual review of all trades

### 6.4 Live Testing (Small Scale)
- **Duration**: 14 days after successful paper trading
- **Capital**: $100 real ETH
- **Tokens**: 3-5 high-liquidity tokens only
- **Monitoring**: Daily manual review
- **Exit Criteria**: No critical issues for 14 days

## 7. Deployment Requirements

### 7.1 Environment Setup
1. **Bankr API key** with read-write permissions
2. **DeFi Llama API key** (free tier sufficient)
3. **CoinGecko API key** (free tier sufficient)
4. **Database migration** to add DEX tables
5. **Configuration updates** to enable DEX features

### 7.2 Deployment Phases
**Phase 1: Data Collection Only**
- Enable DEX data collection
- No trading execution
- Monitor data quality for 7 days

**Phase 2: Paper Trading**
- Enable DEX signal generation
- Paper trading only
- Monitor for 30 days

**Phase 3: Small Live Test**
- Enable execution for $100 portfolio
- Limited token set
- Monitor for 14 days

**Phase 4: Full Deployment**
- Enable for full portfolio (max 20% allocation)
- All DEX tokens meeting criteria
- Continuous monitoring

### 7.3 Rollback Plan
1. **Immediate disable**: Set `DEX_CONFIG['enabled'] = False`
2. **Database rollback**: Script to remove DEX positions if needed
3. **Configuration revert**: Restore pre-DEX config backup
4. **Communication**: Notify via Telegram of system changes

## 8. Operational Requirements

### 8.1 Monitoring
- Daily health check report via Telegram
- Alert for: API failures, high slippage, low liquidity
- Weekly performance review
- Monthly system audit

### 8.2 Maintenance
- Weekly: Review and update DEX token list
- Monthly: Review risk parameters
- Quarterly: Security review of integration
- As needed: Update for Bankr API changes

### 8.3 Support
- Documentation for troubleshooting common issues
- Escalation path for critical failures
- Regular backup of configuration and database
- Version control for all configuration changes

## 9. Constraints and Assumptions

### 9.1 Constraints
1. **Bankr API rate limits**: 100 requests/day (standard), 1000/day (Bankr Club)
2. **Gas costs**: Transactions require ETH for gas on each chain
3. **Liquidity**: Some tokens may have insufficient liquidity for trading
4. **Regulatory**: DEX trading may have regulatory implications in some jurisdictions
5. **Technical**: Bankr API availability outside our control

### 9.2 Assumptions
1. Bankr API will maintain backward compatibility during implementation
2. Base chain will remain low-cost for transactions
3. Sufficient liquidity exists in target DEX tokens
4. Existing Edoras system is stable and reliable
5. the operator will provide ongoing feedback during implementation

## 10. Dependencies

### 10.1 External Dependencies
1. **Bankr API**: Primary execution and price data
2. **DeFi Llama API**: Liquidity and volume data
3. **CoinGecko API**: Market data and token metadata
4. **Ethereum/BASE RPC**: Transaction status monitoring
5. **Telegram API**: Alerts and reporting

### 10.2 Internal Dependencies
1. **Existing Edoras database schema**: Must extend, not replace
2. **Existing signal pipeline**: Must integrate with minimal disruption
3. **Existing risk management**: Must extend to cover DEX
4. **Existing reporting system**: Must include DEX metrics
5. **Existing scheduling system**: Must schedule DEX tasks

## 11. Acceptance Criteria

### 11.1 Technical Acceptance
- [ ] All unit tests pass
- [ ] Integration tests pass
- [ ] Database migrations execute successfully
- [ ] Configuration loads without errors
- [ ] System starts up with DEX enabled

### 11.2 Functional Acceptance
- [ ] DEX data collection works for all configured tokens
- [ ] DEX signals are generated with appropriate filters
- [ ] DEX trades execute via Bankr API
- [ ] Portfolio syncs DEX positions correctly
- [ ] Alerts are sent for DEX events

### 11.3 Performance Acceptance
- [ ] Data collection completes within 5 minutes
- [ ] Signal generation completes within 1 minute
- [ ] Trade execution completes within 30 seconds
- [ ] System handles 50+ DEX tokens without degradation
- [ ] Memory usage