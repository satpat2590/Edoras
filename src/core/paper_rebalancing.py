#!/usr/bin/env python3
"""
Weekly Paper Portfolio Rebalancing
Rebalances the $1000 paper portfolio to top 5 cryptos by advanced score.
"""

import sys
import os
import json
from datetime import datetime


from core.paper_trading import PaperTradingPortfolio
from scoring.enhanced_optimizer import EnhancedPortfolioOptimizer
from config import MAX_SECTOR_PCT, MAX_POSITION_PCT, get_sector, DB_PATH


def send_telegram_message(message: str, chat_id: str = None):
    """Send message via Telegram Bot API"""
    chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID", "1806720995")
    try:
        import urllib.request
        import urllib.parse

        token = os.getenv(
            "TELEGRAM_BOT_TOKEN", "8724014451:AAGpisAWj86i8qmkOtfb4mCBSpiPfZd0ROI"
        )
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": message}).encode()
        req = urllib.request.Request(url, data=data, method="POST")
        urllib.request.urlopen(req, timeout=30)
        return True
    except Exception as e:
        print(f"Failed to send Telegram message: {e}")
        return False


def main():
    print("🔄 Weekly Paper Portfolio Rebalancing")
    print("=" * 60)

    try:
        # Load portfolio — set trader_id for audit trail
        portfolio = PaperTradingPortfolio(DB_PATH, initial_capital=1000.0)
        portfolio._current_trader_id = 6  # Weekly Rebalancer

        # Get current portfolio value before rebalancing
        pre_value = portfolio.get_portfolio_value()
        print(f"Portfolio value before rebalancing: ${pre_value:.2f}")

        # Get updated scores
        print("Fetching updated scores...")
        optimizer = EnhancedPortfolioOptimizer(DB_PATH)
        scores_df = optimizer.score_all_symbols()

        if scores_df.empty:
            print("❌ No scores available - skipping rebalancing")
            return

        # Select top 5 symbols
        top_5 = scores_df.head(5)["symbol"].tolist()
        print(f"Top 5 symbols by advanced score: {top_5}")

        # Get previous top 5 from state file
        previous_top_5 = []
        state_file = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "state/paper_portfolio_state.json",
        )
        if os.path.exists(state_file):
            with open(state_file, "r") as f:
                state = json.load(f)
                previous_top_5 = state.get("symbols", [])

        # Calculate changes
        added = [s for s in top_5 if s not in previous_top_5]
        removed = [s for s in previous_top_5 if s not in top_5]

        # Equal weight (20% each) — but enforce sector concentration limit
        raw_allocation = {symbol: 0.20 for symbol in top_5}

        # Sector concentration check: cap any sector at MAX_SECTOR_PCT
        sector_totals = {}
        for sym, weight in raw_allocation.items():
            sec = get_sector(sym)
            sector_totals.setdefault(sec, []).append(sym)

        target_allocation = dict(raw_allocation)
        sector_warnings = []
        for sec, syms in sector_totals.items():
            total_pct = sum(raw_allocation[s] for s in syms)
            if total_pct > MAX_SECTOR_PCT:
                # Scale down symbols in this sector proportionally
                scale = MAX_SECTOR_PCT / total_pct
                for s in syms:
                    target_allocation[s] = raw_allocation[s] * scale
                sector_warnings.append(
                    f"Sector '{sec}' capped: {total_pct:.0%} → {MAX_SECTOR_PCT:.0%} "
                    f"({', '.join(syms)})"
                )
                print(f"  ⚠️  {sector_warnings[-1]}")

        # Build decision context for all rebalancing trades
        context = json.dumps(
            {
                "signal_type": "weekly_rebalance",
                "trader": "weekly_rebalancer",
                "top_5_symbols": top_5,
                "scores": {
                    sym: float(
                        scores_df[scores_df["symbol"] == sym]["total_score"].values[0]
                    )
                    for sym in top_5
                    if len(scores_df[scores_df["symbol"] == sym]["total_score"].values)
                    > 0
                },
                "target_allocation": {
                    k: round(v, 4) for k, v in target_allocation.items()
                },
                "sector_adjustments": sector_warnings,
                "added": added,
                "removed": removed,
                "pre_value": round(pre_value, 2),
            }
        )
        portfolio._sticky_decision_context = context

        # Rebalance
        print("Executing rebalancing trades...")
        portfolio.rebalance_to_target(target_allocation)

        # Clear sticky context after batch
        portfolio._sticky_decision_context = None

        # Get post-rebalancing value
        post_value = portfolio.get_portfolio_value()
        rebalancing_cost = pre_value - post_value  # Negative if value increased

        # Update state file
        state = {
            "last_rebalanced": datetime.now().isoformat(),
            "symbols": top_5,
            "target_allocation": target_allocation,
            "portfolio_value": post_value,
            "changes": {"added": added, "removed": removed},
        }

        with open(state_file, "w") as f:
            json.dump(state, f, indent=2)

        # Generate report
        report_lines = []
        report_lines.append("🔄 **Weekly Paper Portfolio Rebalancing Complete**")
        report_lines.append(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        report_lines.append("")

        report_lines.append("**📊 Rebalancing Summary**")
        report_lines.append(f"• Pre‑rebalancing value: ${pre_value:.2f}")
        report_lines.append(f"• Post‑rebalancing value: ${post_value:.2f}")

        if rebalancing_cost > 0:
            report_lines.append(
                f"• Rebalancing cost: ${rebalancing_cost:.2f} (transaction fees)"
            )
        else:
            report_lines.append(f"• Value change: +${-rebalancing_cost:.2f}")

        report_lines.append("")

        if added or removed:
            report_lines.append("**🔄 Portfolio Changes**")
            if added:
                report_lines.append(f"• Added: {', '.join(added)}")
            if removed:
                report_lines.append(f"• Removed: {', '.join(removed)}")
            report_lines.append("")

        report_lines.append("**🎯 New Allocation (Top 5 by Advanced Score)**")
        for i, symbol in enumerate(top_5, 1):
            # Get score for this symbol
            symbol_score = scores_df[scores_df["symbol"] == symbol][
                "total_score"
            ].values
            score = symbol_score[0] if len(symbol_score) > 0 else "N/A"

            position_value = portfolio.get_position_value(symbol)
            weight = (position_value / post_value * 100) if post_value > 0 else 0

            report_lines.append(f"{i}. **{symbol}** (score: {score})")
            report_lines.append(f"   Value: ${position_value:.2f} ({weight:.1f}%)")

        report_lines.append("")

        # Add performance section from portfolio report
        portfolio_report = portfolio.generate_performance_report()
        # Extract just the portfolio summary section
        portfolio_lines = portfolio_report.split("\n")
        summary_section = []
        in_summary = False
        for line in portfolio_lines:
            if "📊 Portfolio Summary" in line:
                in_summary = True
            if in_summary and line.strip() and not line.startswith("**📈"):
                summary_section.append(line)
            if line.startswith("**📈"):
                break

        if summary_section:
            report_lines.append("**💰 Current Portfolio Status**")
            report_lines.extend(summary_section[1:])  # Skip the header line

        report_lines.append("")
        report_lines.append("**💡 Next Rebalancing**")
        report_lines.append("• Scheduled: Next Monday 9:00 AM EDT")
        report_lines.append("• Trigger: Manual override available if needed")
        report_lines.append("")
        report_lines.append("---")
        report_lines.append("_Weekly systematic rebalancing • Not financial advice_")

        report = "\n".join(report_lines)

        # Save report to file
        timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        filename = f"paper_rebalancing_report_{timestamp}.txt"
        with open(filename, "w") as f:
            f.write(report)

        print("✅ Rebalancing complete")
        print(f"📄 Report saved to {filename}")

        # Send to Telegram
        print("Sending report to Telegram...")
        telegram_success = send_telegram_message(report)

        if telegram_success:
            print("✅ Report sent to Telegram")
        else:
            print("⚠️ Could not send to Telegram")

        return True

    except Exception as e:
        print(f"❌ Error during rebalancing: {e}")
        import traceback

        traceback.print_exc()

        # Send error alert
        error_msg = f"❌ Paper portfolio rebalancing failed: {str(e)[:100]}"
        send_telegram_message(error_msg)

        return False


if __name__ == "__main__":
    main()
