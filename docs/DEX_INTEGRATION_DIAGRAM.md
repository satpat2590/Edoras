# DEX Integration Architecture Diagram

## System Context Diagram

```mermaid
C4Context
    title System Context: Edoras Trading System with DEX Integration
    
    Person(operator, "Operator", "Trader, provides ETH capital and strategic direction")
    Person(aleph, "Aleph", "AI Trading Agent, operates the system")
    
    System(edoras, "Edoras Trading System", "Multi-asset trading platform")
    System(bankr, "Bankr API", "Crypto trading API with natural language interface")
    System(dex, "Base DEX", "Decentralized exchanges on Base chain")
    System(cex, "Coinbase CEX", "Centralized exchange API")
    
    Rel(operator, aleph, "Provides ETH capital and strategy")
    Rel(aleph, edoras, "Operates and monitors")
    Rel(edoras, bankr, "Executes DEX trades via API")
    Rel(edoras, cex, "Executes CEX trades via API")
    Rel(bankr, dex, "Routes trades to DEX")
    
    UpdateRelStyle(operator, aleph, $offsetY="-40", $offsetX="60")
    UpdateRelStyle(aleph, edoras, $offsetY="10", $offsetX="40")
```

## Container Diagram

```mermaid
C4Container
    title Container View: Edoras with DEX Extension
    
    Person(operator, "Operator", "Trader")
    Person(aleph, "Aleph", "AI Trading Agent")
    
    System_Boundary(edoras, "Edoras Trading System") {
        Container(db, "SQLite Database", "Database", "Stores all trading data")
        Container(api, "Bankr API Client", "Python", "DEX execution interface")
        Container(engine, "Trading Engine", "Python", "Signal generation & risk management")
        Container(monitor, "Monitoring System", "Python", "Health checks & alerts")
        Container(scheduler, "Systemd Timers", "Linux", "Task scheduling")
    }
    
    System_Ext(bankr, "Bankr API", "External API")
    System_Ext(dex, "Base DEX", "Decentralized Exchange")
    System_Ext(cex, "Coinbase API", "Centralized Exchange")
    System_Ext(telegram, "Telegram", "Messaging platform")
    
    Rel(aleph, monitor, "Reviews reports & alerts")
    Rel(operator, telegram, "Receives trade alerts")
    
    Rel(engine, db, "Reads/writes trading data")
    Rel(api, bankr, "Executes DEX trades")
    Rel(engine, cex, "Executes CEX trades")
    Rel(bankr, dex, "Routes to DEX liquidity")
    Rel(monitor, telegram, "Sends alerts & reports")
    Rel(scheduler, engine, "Triggers trading cycles")
    Rel(scheduler, monitor, "Triggers health checks")
    
    UpdateRelStyle(aleph, monitor, $offsetY="-30", $offsetX="60")
    UpdateRelStyle(engine, db, $offsetY="20", $offsetX="-40")
```

## Component Diagram: DEX Integration

```mermaid
C4Component
    title Component View: DEX Integration Modules
    
    Container(edoras, "Edoras Trading System", "Python") {
        Component(data, "Data Collection", "Collects market data")
        Component(signals, "Signal Engine", "Generates trading signals")
        Component(risk, "Risk Manager", "Manages position risk")
        Component(exec, "Execution Engine", "Executes trades")
        Component(report, "Reporting", "Generates reports")
        
        Component(dex_data, "DEX Data Collector", "Collects DEX-specific data")
        Component(dex_signals, "DEX Signal Engine", "DEX-specific signals")
        Component(dex_exec, "DEX Executor", "Executes via Bankr API")
        Component(dex_monitor, "DEX Monitor", "Monitors DEX health")
    }
    
    ContainerDb(db, "SQLite DB", "Database")
    System_Ext(bankr, "Bankr API", "External API")
    System_Ext(dex_sources, "DEX Data Sources", "DeFi Llama, CoinGecko")
    
    Rel(data, dex_sources, "Fetches DEX market data", "HTTPS")
    Rel(dex_data, db, "Stores DEX metadata", "SQL")
    Rel(signals, dex_signals, "Extends with DEX logic", "Python call")
    Rel(dex_signals, risk, "Sends DEX signals for validation", "Python call")
    Rel(risk, dex_exec, "Approves DEX trades", "Python call")
    Rel(dex_exec, bankr, "Executes trades", "HTTPS/JSON")
    Rel(dex_monitor, report, "Sends DEX alerts", "Python call")
    Rel(dex_exec, db, "Logs DEX transactions", "SQL")
    
    UpdateRelStyle(data, dex_sources, $offsetY="10", $offsetX="80")
    UpdateRelStyle(dex_exec, bankr, $offsetY="-10", $offsetX="60")
```

## Data Flow Diagram

```mermaid
flowchart TD
    Start([Trading Cycle Start]) --> DataCollection
    
    subgraph "Data Collection Layer"
        DataCollection --> CEXData[CEX Data<br/>Coinbase WS/REST]
        DataCollection --> DEXData[DEX Data<br/>Bankr API + DeFi Llama]
    end
    
    CEXData --> IndicatorCalc[Indicator Calculator]
    DEXData --> IndicatorCalc
    
    IndicatorCalc --> SignalGen[Signal Generation]
    
    subgraph "Signal Processing"
        SignalGen --> CEXSignals[CEX Signals]
        SignalGen --> DEXSignals[DEX Signals]
        
        DEXSignals --> LiquidityFilter{Liquidity > $100k?}
        DEXSignals --> VolumeFilter{Volume > $50k?}
        DEXSignals --> AgeFilter{Age > 7 days?}
        
        LiquidityFilter -->|Pass| RiskCheck
        VolumeFilter -->|Pass| RiskCheck  
        AgeFilter -->|Pass| RiskCheck
    end
    
    CEXSignals --> RiskCheck[Risk Management]
    
    subgraph "Risk Management"
        RiskCheck --> CircuitBreaker{Circuit Breaker Active?}
        CircuitBreaker -->|No| PositionLimits{Within Position Limits?}
        CircuitBreaker -->|Yes| Reject[Reject All Buys]
        PositionLimits -->|Yes| SlippageCheck{Acceptable Slippage?}
        PositionLimits -->|No| Reject
        SlippageCheck -->|Yes| Approve[Approve Trade]
        SlippageCheck -->|No| Reject
    end
    
    Approve --> Execution
    
    subgraph "Execution Layer"
        Execution --> CEXExec[CEX Execution<br/>Coinbase API]
        Execution --> DEXExec[DEX Execution<br/>Bankr API]
    end
    
    CEXExec --> PortfolioUpdate[Portfolio Update]
    DEXExec --> PortfolioUpdate
    
    PortfolioUpdate --> Reporting[Reporting & Alerts]
    Reporting --> Telegram[Telegram Messages]
    
    Reject --> Logging[Log Rejected Trade]
    Logging --> Reporting
```

## Database Schema Extension

```mermaid
erDiagram
    SECURITIES {
        int id PK
        string symbol
        string name
        string type
        string class
        string sector
        int exchange_id FK
        string indicator_profile
        string chain
        string contract_address
        boolean is_dex
        datetime created_at
    }
    
    EXCHANGES {
        int id PK
        string name
        string type
        string api_endpoint
    }
    
    DEX_TOKENS {
        int id PK
        int security_id FK
        string chain
        string contract_address UK
        string dex_platform
        float liquidity
        int holder_count
        datetime created_at
        datetime last_updated
    }
    
    DEX_TRANSACTIONS {
        int id PK
        int portfolio_id FK
        int security_id FK
        string tx_hash UK
        string action
        float amount
        float price
        float slippage
        float gas_used
        string status
        datetime created_at
        datetime confirmed_at
    }
    
    PORTFOLIOS {
        int id PK
        string name
        string mode
        float capital
        boolean active
    }
    
    SECURITIES ||--o{ DEX_TOKENS : "has DEX instances"
    SECURITIES }o--|| EXCHANGES : "traded on"
    DEX_TRANSACTIONS }o--|| PORTFOLIOS : "executed in"
    DEX_TRANSACTIONS }o--|| SECURITIES : "traded"
    
    SECURITIES {
        "Examples:"
        "BTC-USD (CEX)"
        "ETH-USD (CEX)" 
        "VVV-BASE (DEX)"
        "FAI-BASE (DEX)"
    }
    
    EXCHANGES {
        "coinbase (CEX)"
        "yfinance (CEX)"
        "polymarket (CEX)"
        "base_dex (DEX)"
        "ethereum_dex (DEX)"
    }
```

## Module Dependency Graph

```mermaid
flowchart TD
    subgraph "Core Edoras System"
        config[config.py]
        db[Database Schema]
        indicators[indicator_calculator.py]
        signals[signal_trading.py]
        risk[risk_manager.py]
        paper[paper_trading.py]
    end
    
    subgraph "New DEX Modules"
        bankr_client[bankr_client.py]
        dex_data[dex_data_collector.py]
        dex_signals[dex_signal_engine.py]
        dex_exec[dex_executor.py]
        dex_monitor[dex_monitor.py]
    end
    
    subgraph "External Dependencies"
        bankr_api[Bankr API]
        defillama[DeFi Llama API]
        coingecko[CoinGecko API]
    end
    
    config --> indicators
    config --> signals
    config --> risk
    config --> dex_data
    config --> dex_exec
    
    db --> indicators
    db --> signals
    db --> paper
    db --> dex_data
    db --> dex_exec
    
    indicators --> signals
    signals --> risk
    risk --> paper
    
    dex_data --> bankr_client
    dex_data --> defillama
    dex_data --> coingecko
    
    bankr_client --> bankr_api
    
    dex_signals --> indicators
    dex_signals --> signals
    
    dex_exec --> bankr_client
    dex_exec --> risk
    dex_exec --> paper
    
    dex_monitor --> bankr_client
    dex_monitor --> dex_data
    dex_monitor --> dex_exec
```

## Deployment Timeline

```mermaid
gantt
    title DEX Integration Deployment Timeline
    dateFormat YYYY-MM-DD
    axisFormat %m/%d
    
    section Phase 1: Foundation
    Database Schema Extension :2026-03-15, 5d
    Bankr API Client :2026-03-18, 4d
    DEX Data Collection :2026-03-21, 5d
    
    section Phase 2: Signal Processing
    DEX Indicators :2026-03-25, 5d
    Signal Engine Extension :2026-03-29, 4d
    Risk Management Extension :2026-04-01, 4d
    
    section Phase 3: Execution
    DEX Executor :2026-04-04, 5d
    Portfolio Integration :2026-04-08, 3d
    Monitoring System :2026-04-10, 3d
    
    section Phase 4: Testing
    Unit & Integration Tests :2026-04-12, 5d
    Paper Trading (30 days) :2026-04-17, 30d
    Small Live Test (14 days) :2026-05-17, 14d
    
    section Phase 5: Deployment
    Production Readiness :2026-05-31, 3d
    Full Deployment :2026-06-03, 2d
```

## Risk Management Flow

```mermaid
flowchart TD
    Start([DEX Trade Signal]) --> LiquidityCheck{Liquidity > $100k?}
    
    LiquidityCheck -->|No| Reject1[Reject: Low Liquidity]
    LiquidityCheck -->|Yes| VolumeCheck{24h Volume > $50k?}
    
    VolumeCheck -->|No| Reject2[Reject: Low Volume]
    VolumeCheck -->|Yes| AgeCheck{Token Age > 7 days?}
    
    AgeCheck -->|No| Reject3[Reject: Too New]
    AgeCheck -->|Yes| HolderCheck{Holders > 100?}
    
    HolderCheck -->|No| Reject4[Reject: Few Holders]
    HolderCheck -->|Yes| PositionCheck{Position < 10% of Portfolio?}
    
    PositionCheck -->|No| Reject5[Reject: Position Limit]
    PositionCheck -->|Yes| CircuitCheck{Circuit Breaker Active?}
    
    CircuitCheck -->|Yes| Reject6[Reject: Circuit Breaker]
    CircuitCheck -->|No| SlippageCheck{Est. Slippage < 5%?}
    
    SlippageCheck -->|No| Reject7[Reject: High Slippage]
    SlippageCheck -->|Yes| GasCheck{Gas Price < 30 gwei?}
    
    GasCheck -->|No| Wait[Wait for Lower Gas]
    GasCheck -->|Yes| Approve[Approve Trade]
    
    Wait --> GasMonitor[Monitor Gas Price]
    GasMonitor --> GasCheck
    
    Approve --> Execute[Execute via Bankr API]
    
    Execute --> Success{Trade Successful?}
    Success -->|Yes| LogSuccess[Log Success]
    Success -->|No| Retry{Retry Count < 3?}
    
    Retry -->|Yes| WaitRetry[Wait 30s & Retry]
    Retry -->|No| LogFailure[Log Failure]
    
    WaitRetry --> Execute
    
    LogSuccess --> UpdatePortfolio[Update Portfolio]
    LogFailure --> Alert[Send Alert]
    
    UpdatePortfolio --> End([Cycle Complete])
    Alert --> End
```

## Monitoring Dashboard View

```mermaid
quadrantChart
    title DEX Token Monitoring Dashboard
    x-axis "Low Liquidity" --> "High Liquidity"
    y-axis "Low Volume" --> "High Volume"
    
    "VVV-BASE": [0.8, 0.9]
    "FAI-BASE": [0.6, 0.7]
    "BNKR-BASE": [0.7, 0.6]
    "ETH-BASE": [1.0, 1.0]
    "USDC-BASE": [1.0, 0.8]
    
    quadrant-1 "Watch: High Risk"
    quadrant-2 "Trade: Good Opportunity"
    quadrant-3 "Avoid: Low Activity"
    quadrant-4 "Hold: Stable"
```

## Technology Stack

```mermaid
mindmap
  root((Edoras DEX Integration))
    
    Backend
      Python 3.11+
        FastAPI (REST endpoints)
        SQLAlchemy (ORM)
        AsyncIO (concurrent requests)
    
    Database
      SQLite (primary)
        SQLite-vec (embeddings)
        Migrations (Alembic)
    
    APIs
      Bankr API
        Trading execution
        Portfolio queries
      DeFi Llama API
        Liquidity data
        Volume metrics
      CoinGecko API
        Market data
        Token metadata
    
    Infrastructure
      Systemd Timers
        Scheduled tasks
        Persistent=true
      Docker (optional)
        Containerization
        Easy deployment
    
    Monitoring
      Telegram Bot
        Real-time alerts
        Daily reports
      Custom Dashboard
        DEX health
        Performance metrics
    
    Testing
      pytest
        Unit tests
        Integration tests
      Paper Trading
        30-day validation
        Performance comparison
```

## Summary

The DEX integration extends the existing Edoras trading system with:

1. **Data Layer**: New sources (Bankr, DeFi Llama, CoinGecko) for DEX token data
2. **Signal Layer**: DEX-specific indicators and filters (liquidity, volume, age)
3. **Execution Layer**: Bankr API integration for natural language trading
4. **Risk Layer**: Extended risk management with DEX-specific rules
5. **Monitoring Layer**: Comprehensive health checks and alerts

The architecture maintains backward compatibility with existing CEX functionality while adding DEX capabilities through modular extensions. The phased deployment approach minimizes risk and allows for thorough testing at each stage.

**Key Design Principles:**
- **Modularity**: DEX functionality as optional extensions
- **Safety**: Multiple risk filters before execution
- **Observability**: Comprehensive logging and monitoring
- **Maintainability**: Clear separation between CEX and DEX code
- **Scalability**: Support for multiple chains (Base, Ethereum, Polygon)