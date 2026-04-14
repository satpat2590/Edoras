"""
PDF report generation for backtest results.
Uses EdorasPDF (dark-themed) with matplotlib charts.
"""

import io
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from .metrics import BacktestResult, BacktestMetrics

# ── Colors (matching EdorasPDF theme) ──────────────────────────────────────

C_BG = (15, 18, 25)
C_CARD = (22, 27, 38)
C_HEADER = (99, 102, 241)
C_TEXT = (220, 220, 230)
C_MUTED = (140, 145, 160)
C_GREEN = (52, 211, 153)
C_RED = (248, 113, 113)
C_YELLOW = (251, 191, 36)
C_WHITE = (255, 255, 255)
C_ROW_EVEN = (22, 27, 38)
C_ROW_ODD = (28, 33, 46)
C_TABLE_HEADER = (35, 40, 58)

FONT_DIR = "/usr/share/fonts/truetype/dejavu"

# Matplotlib colors (0-1 range)
_c = lambda rgb: tuple(v / 255 for v in rgb)
MPL_BG = _c(C_BG)
MPL_CARD = _c(C_CARD)
MPL_GREEN = _c(C_GREEN)
MPL_RED = _c(C_RED)
MPL_HEADER = _c(C_HEADER)
MPL_TEXT = _c(C_TEXT)
MPL_MUTED = _c(C_MUTED)

REPORTS_DIR = Path(__file__).parent.parent.parent.parent / "reports" / "backtest"


def _setup_mpl():
    """Configure matplotlib for dark-theme chart generation."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({
        "figure.facecolor": MPL_BG,
        "axes.facecolor": MPL_CARD,
        "axes.edgecolor": MPL_MUTED,
        "axes.labelcolor": MPL_TEXT,
        "xtick.color": MPL_MUTED,
        "ytick.color": MPL_MUTED,
        "text.color": MPL_TEXT,
        "grid.color": (*MPL_MUTED, 0.3),
        "legend.facecolor": MPL_CARD,
        "legend.edgecolor": MPL_MUTED,
        "font.size": 9,
    })
    return plt


def _chart_to_tempfile(fig) -> str:
    """Save matplotlib figure to a temp PNG and return the path."""
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    fig.savefig(tmp.name, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    import matplotlib.pyplot as plt
    plt.close(fig)
    return tmp.name


def _equity_drawdown_chart(result: BacktestResult) -> str:
    """Generate equity curve + drawdown chart, return temp file path."""
    plt = _setup_mpl()

    eq = result.equity_curve
    if eq.empty:
        return None

    daily = eq.resample("D").last().dropna()
    returns = daily.pct_change().dropna()
    cum = (1 + returns).cumprod()
    running_max = cum.expanding().max()
    dd = (cum - running_max) / running_max

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 5), height_ratios=[3, 1],
                                     sharex=True, gridspec_kw={"hspace": 0.08})

    # Equity curve
    ax1.plot(daily.index, daily.values, color=MPL_HEADER, linewidth=1.2, label="Portfolio")
    if not result.price_series.empty:
        # Normalize price to same starting value
        ps = result.price_series.resample("D").last().dropna()
        normalized = ps / ps.iloc[0] * result.initial_capital
        ax1.plot(normalized.index, normalized.values, color=MPL_MUTED, linewidth=0.8,
                 linestyle="--", alpha=0.7, label="Buy & Hold")
    ax1.set_ylabel("Portfolio Value ($)")
    ax1.legend(loc="upper left", fontsize=8)
    ax1.grid(True, alpha=0.3)
    ax1.set_title(f"{result.strategy_name} — {result.symbol} ({result.start_date} to {result.end_date})",
                  fontsize=11, color=MPL_TEXT)

    # Drawdown
    ax2.fill_between(dd.index, dd.values, 0, color=(*MPL_RED, 0.5))
    ax2.plot(dd.index, dd.values, color=MPL_RED, linewidth=0.8)
    ax2.set_ylabel("Drawdown")
    ax2.set_ylim(dd.min() * 1.1 if dd.min() < 0 else -0.01, 0.005)
    ax2.grid(True, alpha=0.3)

    return _chart_to_tempfile(fig)


def _monthly_heatmap(result: BacktestResult) -> Optional[str]:
    """Generate monthly returns heatmap, return temp file path."""
    plt = _setup_mpl()
    monthly = result.metrics.monthly_returns
    if not monthly:
        return None

    # Build year x month matrix
    data = {}
    for ym, ret in monthly.items():
        year, month = ym.split("-")
        data.setdefault(year, {})[int(month)] = ret

    years = sorted(data.keys())
    months = list(range(1, 13))
    matrix = []
    for y in years:
        row = [data[y].get(m, np.nan) for m in months]
        matrix.append(row)

    arr = np.array(matrix)
    fig, ax = plt.subplots(figsize=(10, max(1.5, len(years) * 0.7)))

    vmax = max(abs(np.nanmin(arr)) if not np.all(np.isnan(arr)) else 0.1,
               abs(np.nanmax(arr)) if not np.all(np.isnan(arr)) else 0.1)

    import matplotlib.colors as mcolors
    cmap = mcolors.LinearSegmentedColormap.from_list("pnl", [MPL_RED, MPL_CARD, MPL_GREEN])
    im = ax.imshow(arr, cmap=cmap, vmin=-vmax, vmax=vmax, aspect="auto")

    ax.set_xticks(range(12))
    ax.set_xticklabels(["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                         "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], fontsize=8)
    ax.set_yticks(range(len(years)))
    ax.set_yticklabels(years, fontsize=9)

    # Annotate cells
    for i in range(len(years)):
        for j in range(12):
            val = arr[i, j]
            if not np.isnan(val):
                color = "white" if abs(val) > vmax * 0.5 else MPL_TEXT
                ax.text(j, i, f"{val:.1%}", ha="center", va="center", fontsize=7, color=color)

    ax.set_title("Monthly Returns", fontsize=10, color=MPL_TEXT)
    fig.colorbar(im, ax=ax, shrink=0.8, format="%.0%%")

    return _chart_to_tempfile(fig)


def _comparison_bar_chart(results: List[BacktestResult]) -> Optional[str]:
    """Bar chart comparing key metrics across strategies."""
    plt = _setup_mpl()
    if len(results) < 2:
        return None

    names = [f"{r.strategy_name}\n{r.symbol}" for r in results]
    sharpes = [r.metrics.sharpe_ratio for r in results]
    returns = [r.metrics.total_return * 100 for r in results]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 4))
    x = range(len(names))

    colors = [MPL_GREEN if v > 0 else MPL_RED for v in sharpes]
    ax1.bar(x, sharpes, color=colors, alpha=0.85)
    ax1.set_xticks(x)
    ax1.set_xticklabels(names, fontsize=7, rotation=30, ha="right")
    ax1.set_title("Sharpe Ratio", fontsize=10)
    ax1.axhline(y=0, color=MPL_MUTED, linewidth=0.5)
    ax1.grid(True, alpha=0.2, axis="y")

    colors = [MPL_GREEN if v > 0 else MPL_RED for v in returns]
    ax2.bar(x, returns, color=colors, alpha=0.85)
    ax2.set_xticks(x)
    ax2.set_xticklabels(names, fontsize=7, rotation=30, ha="right")
    ax2.set_title("Total Return (%)", fontsize=10)
    ax2.axhline(y=0, color=MPL_MUTED, linewidth=0.5)
    ax2.grid(True, alpha=0.2, axis="y")

    fig.suptitle("Strategy Comparison", fontsize=12, color=MPL_TEXT)
    fig.tight_layout()
    return _chart_to_tempfile(fig)


# ── PDF Generation ──────────────────────────────────────────────────────

def _get_pdf_class():
    """Import EdorasPDF-like class (avoid circular imports)."""
    from fpdf import FPDF

    class BacktestPDF(FPDF):
        def __init__(self, title="Backtest Report"):
            super().__init__()
            self.report_title = title
            self.set_auto_page_break(auto=True, margin=15)
            self.add_font("DejaVu", "", f"{FONT_DIR}/DejaVuSans.ttf")
            self.add_font("DejaVu", "B", f"{FONT_DIR}/DejaVuSans-Bold.ttf")
            self.add_font("DejaVu", "I", f"{FONT_DIR}/DejaVuSans-Oblique.ttf")

        def header(self):
            self.set_fill_color(*C_BG)
            self.rect(0, 0, 210, 297, "F")
            self.set_font("DejaVu", "B", 16)
            self.set_text_color(*C_HEADER)
            self.cell(0, 10, self.report_title, new_x="LMARGIN", new_y="NEXT")
            self.set_font("DejaVu", "", 8)
            self.set_text_color(*C_MUTED)
            self.cell(0, 5, f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                      new_x="LMARGIN", new_y="NEXT")
            self.ln(4)

        def footer(self):
            self.set_y(-12)
            self.set_font("DejaVu", "I", 7)
            self.set_text_color(*C_MUTED)
            self.cell(0, 10, f"Edoras Backtest Engine  |  Page {self.page_no()}", align="C")

        def section_title(self, title):
            self.ln(3)
            self.set_font("DejaVu", "B", 11)
            self.set_text_color(*C_HEADER)
            self.cell(0, 7, title, new_x="LMARGIN", new_y="NEXT")
            self.set_draw_color(*C_HEADER)
            self.line(10, self.get_y(), 200, self.get_y())
            self.ln(3)

        def kv_line(self, label, value, value_color=None):
            self.set_font("DejaVu", "B", 9)
            self.set_text_color(*C_MUTED)
            self.cell(55, 5, label, new_x="END")
            self.set_font("DejaVu", "", 9)
            self.set_text_color(*(value_color or C_TEXT))
            self.cell(0, 5, str(value), new_x="LMARGIN", new_y="NEXT")

        def pnl_color(self, val):
            if val > 0:
                return C_GREEN
            elif val < 0:
                return C_RED
            return C_TEXT

        def _fit_text(self, text, max_width):
            if self.get_string_width(text) <= max_width - 1:
                return text
            while len(text) > 1 and self.get_string_width(text + "...") > max_width - 1:
                text = text[:-1]
            return text + "..."

        def table(self, headers, rows, col_widths=None, col_aligns=None):
            if not col_widths:
                usable = 190
                col_widths = [usable / len(headers)] * len(headers)
            if not col_aligns:
                col_aligns = ["L"] * len(headers)
            self.set_font("DejaVu", "B", 7)
            self.set_fill_color(*C_TABLE_HEADER)
            self.set_text_color(*C_MUTED)
            for i, h in enumerate(headers):
                self.cell(col_widths[i], 6, self._fit_text(h, col_widths[i]),
                          border=0, fill=True, align=col_aligns[i], new_x="END")
            self.ln()
            self.set_font("DejaVu", "", 7)
            for row_idx, row in enumerate(rows):
                if self.get_y() > 270:
                    self.add_page()
                bg = C_ROW_EVEN if row_idx % 2 == 0 else C_ROW_ODD
                self.set_fill_color(*bg)
                for i, cell_data in enumerate(row):
                    text = str(cell_data["text"]) if isinstance(cell_data, dict) else str(cell_data)
                    color = cell_data.get("color", C_TEXT) if isinstance(cell_data, dict) else C_TEXT
                    self.set_text_color(*color)
                    self.cell(col_widths[i], 5.5, self._fit_text(text, col_widths[i]),
                              border=0, fill=True, align=col_aligns[i], new_x="END")
                self.ln()

        def stat_cards(self, cards):
            card_w = 190 / len(cards) if cards else 190
            y_start = self.get_y()
            for label, value, color in cards:
                self.set_fill_color(*C_CARD)
                x = self.get_x()
                self.rect(x, y_start, card_w - 2, 16, "F")
                self.set_xy(x + 2, y_start + 1)
                self.set_font("DejaVu", "", 7)
                self.set_text_color(*C_MUTED)
                self.cell(card_w - 6, 4, label)
                self.set_xy(x + 2, y_start + 6)
                self.set_font("DejaVu", "B", 11)
                self.set_text_color(*color)
                self.cell(card_w - 6, 8, str(value))
                self.set_xy(x + card_w - 2, y_start)
            self.set_y(y_start + 19)

    return BacktestPDF


def generate_report(
    result: BacktestResult,
    output_path: str = None,
) -> str:
    """Generate a PDF report for a single backtest result. Returns the file path."""
    BacktestPDF = _get_pdf_class()
    pdf = BacktestPDF(title=f"Backtest: {result.strategy_name} — {result.symbol}")
    pdf.add_page()

    m = result.metrics
    chart_files = []

    # ── Stat cards ────────────────────────────────────────────────
    pdf.stat_cards([
        ("Total Return", f"{m.total_return:.2%}", pdf.pnl_color(m.total_return)),
        ("Sharpe Ratio", f"{m.sharpe_ratio:.2f}", pdf.pnl_color(m.sharpe_ratio)),
        ("Max Drawdown", f"{m.max_drawdown:.2%}", C_RED if m.max_drawdown < -0.05 else C_TEXT),
        ("Win Rate", f"{m.win_rate:.1%}", C_GREEN if m.win_rate > 0.5 else C_YELLOW),
        ("Trades", str(m.total_trades), C_TEXT),
    ])

    # ── Equity curve + drawdown chart ─────────────────────────────
    chart_path = _equity_drawdown_chart(result)
    if chart_path:
        chart_files.append(chart_path)
        pdf.section_title("Equity Curve & Drawdown")
        pdf.image(chart_path, x=10, w=190)
        pdf.ln(3)

    # ── Metrics table ─────────────────────────────────────────────
    pdf.section_title("Performance Metrics")

    metrics_data = [
        ("Returns", [
            ("Total Return", f"{m.total_return:.2%}"),
            ("Annualized Return", f"{m.annualized_return:.2%}"),
            ("Buy & Hold Return", f"{m.buy_hold_return:.2%}"),
        ]),
        ("Risk-Adjusted", [
            ("Sharpe Ratio", f"{m.sharpe_ratio:.2f}"),
            ("Sortino Ratio", f"{m.sortino_ratio:.2f}"),
            ("Calmar Ratio", f"{m.calmar_ratio:.2f}"),
            ("Serenity Ratio", f"{m.serenity_ratio:.2f}"),
        ]),
        ("Drawdown", [
            ("Max Drawdown", f"{m.max_drawdown:.2%}"),
            ("Max DD Duration", f"{m.max_drawdown_duration_days:.0f} days"),
            ("Avg Drawdown", f"{m.avg_drawdown:.2%}"),
            ("Ulcer Index", f"{m.ulcer_index:.4f}"),
        ]),
        ("Trade Quality", [
            ("Total Trades", str(m.total_trades)),
            ("Win Rate", f"{m.win_rate:.1%}"),
            ("Profit Factor", f"{m.profit_factor:.2f}"),
            ("Expectancy", f"{m.expectancy:.4f}"),
            ("Payoff Ratio", f"{m.payoff_ratio:.2f}"),
            ("Avg Win", f"{m.avg_win:.2%}"),
            ("Avg Loss", f"{m.avg_loss:.2%}"),
            ("Avg Holding Days", f"{m.avg_holding_days:.1f}"),
            ("Max Consec. Wins", str(m.max_consecutive_wins)),
            ("Max Consec. Losses", str(m.max_consecutive_losses)),
        ]),
        ("Exposure", [
            ("Exposure %", f"{m.exposure_pct:.1%}"),
            ("Recovery Factor", f"{m.recovery_factor:.2f}"),
            ("Tail Ratio", f"{m.tail_ratio:.2f}"),
        ]),
    ]

    for section_name, items in metrics_data:
        pdf.set_font("DejaVu", "B", 8)
        pdf.set_text_color(*C_HEADER)
        pdf.cell(0, 5, section_name, new_x="LMARGIN", new_y="NEXT")
        for label, value in items:
            pdf.kv_line(f"  {label}", value)
        pdf.ln(1)

    # ── Monthly returns heatmap ───────────────────────────────────
    heatmap_path = _monthly_heatmap(result)
    if heatmap_path:
        chart_files.append(heatmap_path)
        pdf.add_page()
        pdf.section_title("Monthly Returns")
        pdf.image(heatmap_path, x=10, w=190)
        pdf.ln(3)

    # ── Trade log ─────────────────────────────────────────────────
    if result.trades:
        pdf.add_page()
        pdf.section_title(f"Trade Log ({len(result.trades)} entries)")
        headers = ["Date", "Side", "Price", "Qty", "Reason"]
        col_widths = [35, 15, 30, 30, 80]
        col_aligns = ["L", "C", "R", "R", "L"]

        rows = []
        for t in result.trades:
            side_color = C_GREEN if t.side == "BUY" else C_RED
            rows.append([
                t.timestamp.strftime("%Y-%m-%d %H:%M"),
                {"text": t.side, "color": side_color},
                f"${t.price:,.2f}",
                f"{t.quantity:.4f}",
                t.reason,
            ])
        pdf.table(headers, rows, col_widths, col_aligns)

    # ── Parameters ────────────────────────────────────────────────
    if result.parameters:
        pdf.ln(5)
        pdf.section_title("Strategy Parameters")
        for k, v in result.parameters.items():
            pdf.kv_line(k, str(v))

    # ── Save ──────────────────────────────────────────────────────
    if not output_path:
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = str(REPORTS_DIR / f"{result.strategy_name}_{result.symbol}_{ts}.pdf")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    pdf.output(output_path)

    # Cleanup temp chart files
    for f in chart_files:
        try:
            os.unlink(f)
        except OSError:
            pass

    return output_path


def generate_comparison_report(
    results: List[BacktestResult],
    title: str = "Strategy Comparison",
    output_path: str = None,
) -> str:
    """Generate a PDF comparing multiple backtest results. Returns the file path."""
    BacktestPDF = _get_pdf_class()
    pdf = BacktestPDF(title=title)
    pdf.add_page()

    chart_files = []

    # ── Comparison bar chart ──────────────────────────────────────
    bar_path = _comparison_bar_chart(results)
    if bar_path:
        chart_files.append(bar_path)
        pdf.image(bar_path, x=10, w=190)
        pdf.ln(5)

    # ── Summary table ─────────────────────────────────────────────
    pdf.section_title("Results Summary")
    headers = ["Strategy", "Symbol", "Return", "Sharpe", "Sortino", "Max DD", "Win Rate", "PF", "Trades"]
    col_widths = [30, 22, 20, 18, 18, 20, 20, 18, 15]
    col_aligns = ["L", "L", "R", "R", "R", "R", "R", "R", "R"]

    rows = []
    for r in results:
        m = r.metrics
        rows.append([
            r.strategy_name,
            r.symbol,
            {"text": f"{m.total_return:.2%}", "color": pdf.pnl_color(m.total_return)},
            {"text": f"{m.sharpe_ratio:.2f}", "color": pdf.pnl_color(m.sharpe_ratio)},
            {"text": f"{m.sortino_ratio:.2f}", "color": pdf.pnl_color(m.sortino_ratio)},
            {"text": f"{m.max_drawdown:.2%}", "color": C_RED if m.max_drawdown < -0.05 else C_TEXT},
            {"text": f"{m.win_rate:.1%}", "color": C_GREEN if m.win_rate > 0.5 else C_YELLOW},
            f"{m.profit_factor:.2f}",
            str(m.total_trades),
        ])
    pdf.table(headers, rows, col_widths, col_aligns)

    # ── Individual equity curves ──────────────────────────────────
    for r in results:
        chart_path = _equity_drawdown_chart(r)
        if chart_path:
            chart_files.append(chart_path)
            pdf.add_page()
            pdf.section_title(f"{r.strategy_name} — {r.symbol}")

            m = r.metrics
            pdf.stat_cards([
                ("Return", f"{m.total_return:.2%}", pdf.pnl_color(m.total_return)),
                ("Sharpe", f"{m.sharpe_ratio:.2f}", pdf.pnl_color(m.sharpe_ratio)),
                ("Max DD", f"{m.max_drawdown:.2%}", C_RED if m.max_drawdown < -0.05 else C_TEXT),
                ("Win Rate", f"{m.win_rate:.1%}", C_GREEN if m.win_rate > 0.5 else C_YELLOW),
            ])
            pdf.image(chart_path, x=10, w=190)

    # ── Save ──────────────────────────────────────────────────────
    if not output_path:
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = str(REPORTS_DIR / f"comparison_{ts}.pdf")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    pdf.output(output_path)

    for f in chart_files:
        try:
            os.unlink(f)
        except OSError:
            pass

    return output_path
