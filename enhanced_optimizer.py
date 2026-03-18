#!/usr/bin/env python3
"""
Enhanced Portfolio Optimizer with advanced scoring, risk analysis, and expanded crypto universe.
"""

import os
import sys
import json
import sqlite3
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
import logging

# Import advanced scoring
from advanced_scorer import AdvancedScoringModel

logger = logging.getLogger(__name__)

class EnhancedPortfolioOptimizer:
    """Enhanced optimizer with advanced scoring and risk analysis"""
    
    def __init__(self, db_path: str = "crypto_data.db", api_key: str = None, api_secret: str = None):
        self.db_path = db_path
        self.scorer = AdvancedScoringModel(db_path)
        
        # Load available symbols
        self.available_symbols = self.load_available_symbols()
        
        # Categorize symbols
        self.categorized_symbols = self.categorize_symbols(self.available_symbols)
        
        # Expanded universe (selected diverse set)
        self.expanded_universe = self.select_diverse_universe()
        
        logger.info(f"Enhanced optimizer initialized with {len(self.expanded_universe)} symbols")
    
    def load_available_symbols(self) -> List[str]:
        """Load available USD trading pairs"""
        # Try to load from saved JSON
        json_file = "coinbase_usd_pairs.json"
        if os.path.exists(json_file):
            with open(json_file, 'r') as f:
                symbols = json.load(f)
            logger.info(f"Loaded {len(symbols)} symbols from {json_file}")
            return symbols
        
        # Fallback: get from database
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT symbol FROM candlesticks WHERE symbol LIKE '%-USD'")
        symbols = [row[0] for row in cursor.fetchall()]
        conn.close()
        
        logger.info(f"Loaded {len(symbols)} symbols from database")
        return symbols
    
    def categorize_symbols(self, symbols: List[str]) -> Dict[str, List[str]]:
        """Categorize symbols by type/market cap"""
        categories = {
            'large_cap': [],
            'mid_cap': [],
            'small_cap': [],
            'defi': [],
            'meme': [],
            'layer1': [],
            'gaming': [],
            'ai': [],
        }
        
        # Known categorizations (simplified)
        large_cap_set = {
            "BTC-USD", "ETH-USD", "BNB-USD", "SOL-USD", "XRP-USD",
            "ADA-USD", "AVAX-USD", "DOGE-USD", "DOT-USD", "TRX-USD",
            "LINK-USD", "MATIC-USD", "SHIB-USD", "LTC-USD", "UNI-USD",
            "ATOM-USD", "ETC-USD", "XLM-USD", "ALGO-USD", "NEAR-USD"
        }
        
        defi_set = {
            "UNI-USD", "AAVE-USD", "COMP-USD", "MKR-USD", "SNX-USD",
            "YFI-USD", "CRV-USD", "BAL-USD", "SUSHI-USD", "1INCH-USD"
        }
        
        meme_set = {
            "DOGE-USD", "SHIB-USD", "BONK-USD", "TROLL-USD", "PEPE-USD",
            "FLOKI-USD", "WIF-USD"
        }
        
        layer1_set = {
            "BTC-USD", "ETH-USD", "SOL-USD", "ADA-USD", "AVAX-USD",
            "DOT-USD", "ATOM-USD", "ALGO-USD", "NEAR-USD", "ICP-USD"
        }
        
        gaming_set = {
            "SAND-USD", "MANA-USD", "GALA-USD", "ENJ-USD", "AXS-USD",
            "IMX-USD"
        }
        
        ai_set = {
            "FET-USD", "AGIX-USD", "OCEAN-USD", "NMR-USD"
        }
        
        for symbol in symbols:
            # Large cap
            if symbol in large_cap_set:
                categories['large_cap'].append(symbol)
            
            # DeFi
            if symbol in defi_set:
                categories['defi'].append(symbol)
            
            # Meme
            if symbol in meme_set:
                categories['meme'].append(symbol)
            
            # Layer 1
            if symbol in layer1_set:
                categories['layer1'].append(symbol)
            
            # Gaming
            if symbol in gaming_set:
                categories['gaming'].append(symbol)
            
            # AI
            if symbol in ai_set:
                categories['ai'].append(symbol)
            
            # Mid/small cap (simplified)
            if symbol not in large_cap_set:
                if len(categories['mid_cap']) < 30:
                    categories['mid_cap'].append(symbol)
                else:
                    categories['small_cap'].append(symbol)
        
        # Log counts
        for cat, syms in categories.items():
            if syms:
                logger.debug(f"Category {cat}: {len(syms)} symbols")
        
        return categories
    
    def select_diverse_universe(self, max_symbols: int = 35) -> List[str]:
        """Select diverse set of symbols for analysis"""
        selected = set()
        
        # Ensure portfolio symbols are included
        portfolio_symbols = self.get_portfolio_symbols()
        selected.update(portfolio_symbols)
        
        # Select from each category
        categories = self.categorized_symbols
        
        # Large cap: top 10
        large_cap = categories.get('large_cap', [])
        selected.update(large_cap[:10])
        
        # Mid cap: 5 random
        mid_cap = categories.get('mid_cap', [])
        if len(mid_cap) > 5:
            import random
            selected.update(random.sample(mid_cap, min(5, len(mid_cap))))
        else:
            selected.update(mid_cap)
        
        # DeFi: 3 tokens
        defi = categories.get('defi', [])
        selected.update(defi[:3])
        
        # Gaming: 2 tokens
        gaming = categories.get('gaming', [])
        selected.update(gaming[:2])
        
        # AI: 2 tokens
        ai = categories.get('ai', [])
        selected.update(ai[:2])
        
        # Meme: 2 tokens
        meme = categories.get('meme', [])
        selected.update(meme[:2])
        
        # Layer 1: 3 additional
        layer1 = categories.get('layer1', [])
        # Exclude already selected large cap layer1
        layer1_filtered = [s for s in layer1 if s not in selected]
        selected.update(layer1_filtered[:3])
        
        # Convert to list and sort
        selected_list = sorted(list(selected))
        
        # Limit to max_symbols
        if len(selected_list) > max_symbols:
            selected_list = selected_list[:max_symbols]
        
        logger.info(f"Selected {len(selected_list)} diverse symbols for analysis")
        return selected_list
    
    def get_portfolio_symbols(self) -> List[str]:
        """Get current portfolio symbols (simplified - from database)"""
        # For now, return symbols we know are in portfolio
        # In production, fetch from Coinbase API
        portfolio_symbols = [
            "ETH-USD", "BTC-USD", "XRP-USD", "TROLL-USD", 
            "BONK-USD", "FET-USD", "AMP-USD", "GRT-USD"
        ]
        
        # Filter to only those available
        available_set = set(self.available_symbols)
        return [s for s in portfolio_symbols if s in available_set]
    
    def score_all_symbols(self) -> pd.DataFrame:
        """Score all symbols in expanded universe"""
        logger.info(f"Scoring {len(self.expanded_universe)} symbols...")
        
        scores = []
        for symbol in self.expanded_universe:
            try:
                score_data = self.scorer.calculate_total_score(symbol)
                score_data['symbol'] = symbol
                
                # Add category info
                for cat, syms in self.categorized_symbols.items():
                    if symbol in syms:
                        score_data['category'] = cat
                        break
                else:
                    score_data['category'] = 'other'
                
                scores.append(score_data)
                
            except Exception as e:
                logger.warning(f"Error scoring {symbol}: {e}")
        
        if scores:
            df = pd.DataFrame(scores)
            # Reorder columns
            cols = ['symbol', 'category', 'total_score', 'momentum', 'trend', 
                   'volatility', 'volume', 'risk_adjusted']
            df = df[cols]
            df.sort_values('total_score', ascending=False, inplace=True)
            return df
        else:
            return pd.DataFrame()
    
    def calculate_portfolio_risk_metrics(self, portfolio_symbols: List[str]) -> Dict:
        """Calculate risk metrics for portfolio"""
        if not portfolio_symbols:
            return {}
        
        conn = sqlite3.connect(self.db_path)
        
        risk_metrics = {
            'symbols': portfolio_symbols,
            'metrics': {}
        }
        
        for symbol in portfolio_symbols:
            try:
                # Get daily returns
                query = '''
                SELECT timestamp, close 
                FROM candlesticks 
                WHERE symbol = ? AND timeframe = '1d'
                ORDER BY timestamp
                '''
                
                df = pd.read_sql_query(query, conn, params=(symbol,))
                if len(df) < 30:  # Need sufficient data for meaningful risk metrics
                    continue
                
                df['returns'] = df['close'].pct_change()
                returns = df['returns'].dropna()
                
                if len(returns) < 3:
                    continue
                
                # Calculate metrics
                mean_return = returns.mean()
                std_return = returns.std()
                
                if std_return > 0:
                    sharpe = mean_return / std_return * np.sqrt(365)  # Annualized
                else:
                    sharpe = 0
                
                # Sortino ratio (downside deviation)
                downside_returns = returns[returns < 0]
                downside_std = downside_returns.std() if len(downside_returns) > 0 else 0
                sortino = mean_return / downside_std * np.sqrt(365) if downside_std > 0 else 0
                
                # Maximum drawdown
                cumulative = (1 + returns).cumprod()
                running_max = cumulative.expanding().max()
                drawdown = (cumulative - running_max) / running_max
                max_dd = drawdown.min() if not drawdown.empty else 0
                
                # Value at Risk (95%, 1-day)
                var_95 = np.percentile(returns, 5)
                
                risk_metrics['metrics'][symbol] = {
                    'sharpe_ratio': round(sharpe, 3),
                    'sortino_ratio': round(sortino, 3),
                    'max_drawdown': round(max_dd, 4),
                    'var_95': round(var_95, 4),
                    'volatility': round(std_return, 4),
                    'avg_return': round(mean_return, 4)
                }
                
            except Exception as e:
                logger.warning(f"Error calculating risk metrics for {symbol}: {e}")
        
        conn.close()
        return risk_metrics
    
    def generate_enhanced_report(self) -> str:
        """Generate enhanced optimization report"""
        report_lines = []
        report_lines.append("🚀 **Enhanced Portfolio Optimization Report**")
        report_lines.append(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        report_lines.append("")
        
        # 1. Universe Overview
        report_lines.append("**📊 Analysis Universe**")
        report_lines.append(f"• Total symbols analyzed: {len(self.expanded_universe)}")
        
        category_counts = {}
        for symbol in self.expanded_universe:
            for cat, syms in self.categorized_symbols.items():
                if symbol in syms:
                    category_counts[cat] = category_counts.get(cat, 0) + 1
                    break
        
        for cat, count in sorted(category_counts.items()):
            report_lines.append(f"• {cat}: {count} symbols")
        
        report_lines.append("")
        
        # 2. Score all symbols
        scores_df = self.score_all_symbols()
        if not scores_df.empty:
            report_lines.append("**🏆 Top 10 Cryptos by Advanced Score**")
            report_lines.append("")
            
            top_10 = scores_df.head(10)
            for _, row in top_10.iterrows():
                symbol = row['symbol']
                total = row['total_score']
                category = row['category']
                momentum = row['momentum']
                trend = row['trend']
                
                report_lines.append(f"• **{symbol}** ({category}): {total}/100")
                report_lines.append(f"  Momentum: {momentum} | Trend: {trend}")
            
            report_lines.append("")
            
            # Bottom 5
            bottom_5 = scores_df.tail(5)
            report_lines.append("**📉 Bottom 5 Cryptos by Score**")
            for _, row in bottom_5.iterrows():
                symbol = row['symbol']
                total = row['total_score']
                report_lines.append(f"• {symbol}: {total}/100")
            
            report_lines.append("")
        
        # 3. Portfolio Risk Analysis
        portfolio_symbols = self.get_portfolio_symbols()
        if portfolio_symbols:
            report_lines.append("**⚠️ Portfolio Risk Analysis**")
            
            risk_metrics = self.calculate_portfolio_risk_metrics(portfolio_symbols)
            
            if risk_metrics['metrics']:
                from telegram_fmt import fmt_table
                risk_rows = []
                for symbol, metrics in risk_metrics['metrics'].items():
                    risk_rows.append({
                        "symbol": symbol,
                        "sharpe": f"{metrics['sharpe_ratio']:.2f}",
                        "sortino": f"{metrics['sortino_ratio']:.2f}",
                        "max_dd": f"{metrics['max_drawdown']:.2%}",
                        "volatility": f"{metrics['volatility']:.2%}",
                    })
                report_lines.append(fmt_table(
                    risk_rows,
                    ["symbol", "sharpe", "sortino", "max_dd", "volatility"],
                    {"max_dd": "Max DD"},
                ))
            else:
                report_lines.append("• Insufficient data for detailed risk metrics (need more historical data)")
            
            report_lines.append("")
        
        # 4. Optimization Recommendations
        if not scores_df.empty and portfolio_symbols:
            report_lines.append("**🎯 Enhanced Optimization Recommendations**")
            report_lines.append("")
            
            # Get portfolio scores
            portfolio_scores = scores_df[scores_df['symbol'].isin(portfolio_symbols)].copy()
            if not portfolio_scores.empty:
                avg_portfolio_score = portfolio_scores['total_score'].mean()
                
                # Identify underperformers (bottom 25% of portfolio)
                portfolio_scores.sort_values('total_score', inplace=True)
                bottom_count = max(1, len(portfolio_scores) // 4)  # 25%
                underperformers = portfolio_scores.head(bottom_count)
                
                if not underperformers.empty:
                    report_lines.append("**Consider Reducing Exposure:**")
                    for _, row in underperformers.iterrows():
                        symbol = row['symbol']
                        score = row['total_score']
                        report_lines.append(f"• {symbol} (score: {score} vs portfolio avg: {avg_portfolio_score:.1f})")
                    report_lines.append("")
                
                # Identify top non-portfolio opportunities
                non_portfolio = scores_df[~scores_df['symbol'].isin(portfolio_symbols)].copy()
                if not non_portfolio.empty:
                    top_opportunities = non_portfolio.head(5)
                    
                    report_lines.append("**Top Opportunities Not in Portfolio:**")
                    for _, row in top_opportunities.iterrows():
                        symbol = row['symbol']
                        score = row['total_score']
                        category = row['category']
                        report_lines.append(f"• {symbol} ({category}): score {score}")
                    report_lines.append("")
            
            report_lines.append("**💡 Advanced Scoring Components:**")
            report_lines.append("• **Momentum (40%)**: RSI, MACD, price vs moving averages")
            report_lines.append("• **Trend (25%)**: ADX strength, MA slopes, golden/death cross")
            report_lines.append("• **Volatility (15%)**: ATR, Bollinger Band width")
            report_lines.append("• **Volume (10%)**: Volume trends, volume-price confirmation")
            report_lines.append("• **Risk-Adjusted (10%)**: Sharpe ratio, max drawdown, VaR")
            report_lines.append("")
        
        # 5. Paper Trading Portfolio Setup
        report_lines.append("**💰 Paper Trading Portfolio ($1000)**")
        report_lines.append("• Starting capital: $1,000.00")
        report_lines.append("• Transaction cost: 0.1% per trade")
        report_lines.append("• Rebalancing: Weekly based on top 5 scores")
        report_lines.append("• Initial allocation: Equal weight top 5 cryptos")
        report_lines.append("")
        
        if not scores_df.empty:
            top_5 = scores_df.head(5)
            report_lines.append("**Initial Paper Portfolio Allocation:**")
            for i, (_, row) in enumerate(top_5.iterrows(), 1):
                symbol = row['symbol']
                score = row['total_score']
                report_lines.append(f"{i}. {symbol} (score: {score}) - $200.00 (20%)")
        
        report_lines.append("")
        report_lines.append("---")
        report_lines.append("_Enhanced Portfolio Optimization Engine • Not financial advice_")
        report_lines.append("_Risk metrics based on limited historical data (7 days)_")
        
        return "\n".join(report_lines)


def main():
    """Main execution function"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Enhanced Portfolio Optimizer")
    parser.add_argument('--report', action='store_true', help='Generate enhanced report')
    
    args = parser.parse_args()
    
    print("Enhanced Portfolio Optimizer")
    print("=" * 60)
    
    try:
        optimizer = EnhancedPortfolioOptimizer("crypto_data.db")
        
        if args.report:
            print("Generating enhanced optimization report...")
            report = optimizer.generate_enhanced_report()
            print("\n" + report)
            
            # Save to file
            timestamp = datetime.now().strftime('%Y%m%d_%H%M')
            filename = f"enhanced_optimization_report_{timestamp}.txt"
            with open(filename, 'w') as f:
                f.write(report)
            print(f"\n✅ Report saved to {filename}")
        else:
            print("Usage: python3 enhanced_optimizer.py --report")
            
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()