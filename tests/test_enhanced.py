#!/usr/bin/env python3
"""Test enhanced optimizer"""

import sys
sys.path.insert(0, '.')

from enhanced_optimizer import EnhancedPortfolioOptimizer

def main():
    print("🧪 Testing Enhanced Portfolio Optimizer")
    print("=" * 60)
    
    try:
        optimizer = EnhancedPortfolioOptimizer("crypto_data.db")
        
        print(f"Expanded universe: {len(optimizer.expanded_universe)} symbols")
        print(f"Sample symbols: {optimizer.expanded_universe[:10]}...")
        
        print("\n📊 Scoring all symbols...")
        scores_df = optimizer.score_all_symbols()
        
        if not scores_df.empty:
            print(f"\nTop 10 symbols by advanced score:")
            print(scores_df[['symbol', 'category', 'total_score']].head(10).to_string(index=False))
            
            print(f"\nBottom 5 symbols:")
            print(scores_df[['symbol', 'category', 'total_score']].tail(5).to_string(index=False))
        
        print("\n⚠️ Portfolio risk analysis...")
        portfolio_symbols = optimizer.get_portfolio_symbols()
        print(f"Portfolio symbols: {portfolio_symbols}")
        
        risk_metrics = optimizer.calculate_portfolio_risk_metrics(portfolio_symbols)
        if risk_metrics['metrics']:
            print("\nRisk metrics:")
            for symbol, metrics in risk_metrics['metrics'].items():
                print(f"{symbol}:")
                for key, value in metrics.items():
                    print(f"  {key}: {value}")
        
        print("\n📝 Generating enhanced report...")
        report = optimizer.generate_enhanced_report()
        
        # Print first 50 lines
        lines = report.split('\n')
        for line in lines[:50]:
            print(line)
        
        if len(lines) > 50:
            print("... (report truncated)")
        
        # Save full report
        with open("enhanced_optimization_report.txt", "w") as f:
            f.write(report)
        print("\n✅ Full report saved to enhanced_optimization_report.txt")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()