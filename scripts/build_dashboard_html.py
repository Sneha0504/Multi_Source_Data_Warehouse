#!/usr/bin/env python3
"""Build a single self-contained interactive Plotly dashboard (Step 9).

This is the interactive half of the Python dashboard. It reuses the data-loading
layer and palette from ``scripts/build_charts.py`` (a single source of truth for
the SQL and the colours) and renders an **offline, one-file** HTML dashboard at
``outputs/dashboard/dashboard.html``. plotly.js is inlined so the file works with
no network access.

The dashboard mirrors the three pages of ``docs/dashboard_guide.md``:

* **Executive KPIs** -- KPI cards, monthly orders, monthly gross revenue.
* **Marketing funnel** -- leads -> deals -> sellers by channel, conversion rate,
  gross sales.
* **Data quality & reconciliation** -- reconciliation status counts, review score
  late vs on-time, key DQ issue counts.

Every chart keeps Plotly's native hover and zoom. The warehouse is opened
read-only exactly as the sibling scripts do (env ``DUCKDB_PATH`` first, then
``config.paths.duckdb_path``).
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Ensure the sibling module is importable regardless of the caller's cwd.
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import build_charts as bc

logger = logging.getLogger("build_dashboard_html")

PROJECT_ROOT = bc.PROJECT_ROOT
DEFAULT_CONFIG_PATH = bc.DEFAULT_CONFIG_PATH
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "outputs" / "dashboard"
DEFAULT_OUTPUT_FILE = DEFAULT_OUTPUT_DIR / "dashboard.html"

PALETTE = bc.PALETTE
RECON_COLORS = bc.RECON_COLORS


# --------------------------------------------------------------------------- #
# Shared Plotly layout.
# --------------------------------------------------------------------------- #
def _base_layout(title: str) -> dict:
    """Return a consistent layout dict for one figure."""
    return {
        "title": {"text": title, "font": {"size": 18, "color": PALETTE["ink_primary"]}},
        "paper_bgcolor": PALETTE["surface"],
        "plot_bgcolor": PALETTE["surface"],
        "font": {
            "family": "system-ui, -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif",
            "size": 13,
            "color": PALETTE["ink_secondary"],
        },
        "margin": {"l": 70, "r": 30, "t": 60, "b": 80},
        "xaxis": {
            "gridcolor": PALETTE["grid"],
            "linecolor": PALETTE["axis"],
            "zeroline": False,
        },
        "yaxis": {
            "gridcolor": PALETTE["grid"],
            "linecolor": PALETTE["axis"],
            "zeroline": False,
        },
        "legend": {"orientation": "h", "yanchor": "bottom", "y": 1.02, "x": 0},
        "hoverlabel": {"font": {"size": 12}},
    }


# --------------------------------------------------------------------------- #
# Figures (one per chart).
# --------------------------------------------------------------------------- #
def kpi_cards_html(data: bc.DashboardData) -> str:
    """Render the KPI summary as a row of static HTML cards (no plot needed)."""
    t = data.totals
    cards = [
        ("Total orders", f"{t['orders']:,}", PALETTE["series_1"]),
        ("Customers (people)", f"{t['people']:,}", PALETTE["series_3"]),
        ("Repeat-order rate", f"{t['repeat_rate'] * 100:.2f}%", PALETTE["series_7"]),
        (
            "Gross order value (GMV — items + freight, all orders)",
            bc.fmt_brl(t["gross"]),
            PALETTE["series_6"],
        ),
        (
            "Avg order value (incl. 775 zero-item orders)",
            f"R${t['aov']:,.2f}",
            PALETTE["series_2"],
        ),
    ]
    tiles = "".join(
        f'<div class="kpi" style="border-top:4px solid {accent}">'
        f'<div class="kpi-value">{value}</div>'
        f'<div class="kpi-label">{label}</div></div>'
        for label, value, accent in cards
    )
    return f'<div class="kpi-row">{tiles}</div>'


def fig_monthly_line(
    data: bc.DashboardData, value_col: str, title: str, ylabel: str, color: str, money: bool
) -> object:
    """Monthly line with partial boundary months shown as distinct markers."""
    import plotly.graph_objects as go

    trend, excluded = bc.trend_split(data.monthly)
    money_hover = "%{x|%b %Y}<br>R$%{y:,.0f}<extra></extra>"
    plain_hover = "%{x|%b %Y}<br>%{y:,.0f}<extra></extra>"
    hover = money_hover if money else plain_hover
    excluded_hover = (
        "%{x|%b %Y} (excluded)<br>R$%{y:,.0f}<extra></extra>"
        if money
        else "%{x|%b %Y} (excluded)<br>%{y:,.0f}<extra></extra>"
    )

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=trend["month_start"],
            y=trend[value_col],
            mode="lines+markers",
            name="Monthly trend (2017-01 onward)",
            line={"color": color, "width": 2.5},
            marker={"size": 7},
            hovertemplate=hover,
        )
    )
    fig.add_trace(
        go.Scatter(
            x=excluded["month_start"],
            y=excluded[value_col],
            mode="markers",
            name="Partial / 2016 ramp-up months (excluded from trend)",
            marker={
                "size": 11,
                "color": PALETTE["surface"],
                "line": {"color": PALETTE["ink_muted"], "width": 2},
                "symbol": "circle",
            },
            hovertemplate=excluded_hover,
        )
    )
    # Annotate the peak trend month, kept clear of the line (upper-left area).
    peak = trend.loc[trend[value_col].idxmax()]
    peak_label = bc.fmt_brl(float(peak[value_col])) if money else f"{int(peak[value_col]):,}"
    fig.add_annotation(
        x=peak["month_start"],
        y=peak[value_col],
        text=f"Peak {peak['month_start'].strftime('%b %Y')}: {peak_label}",
        showarrow=True,
        arrowhead=2,
        ax=-55,
        ay=-45,
        font={"size": 11, "color": PALETTE["ink_secondary"]},
        arrowcolor=PALETTE["ink_muted"],
    )

    layout = _base_layout(title)
    layout["xaxis"]["title"] = "Purchase month"
    layout["yaxis"]["title"] = ylabel
    layout["yaxis"]["rangemode"] = "tozero"
    if money:
        layout["yaxis"]["tickprefix"] = "R$"
        layout["yaxis"]["tickformat"] = "~s"
    fig.update_layout(layout)
    return fig


def fig_funnel(data: bc.DashboardData) -> object:
    """Grouped bar: leads vs closed deals vs sellers-with-sales by origin."""
    import plotly.graph_objects as go

    df = data.seller_acq.sort_values("leads", ascending=False)
    origins = df["origin"].tolist()
    series = [
        ("Leads", df["leads"], PALETTE["funnel_leads"]),
        ("Closed deals", df["closed_deals"], PALETTE["funnel_deals"]),
        ("Sellers with sales", df["sellers_with_sales"], PALETTE["funnel_sellers"]),
    ]
    fig = go.Figure()
    for name, values, color in series:
        fig.add_trace(
            go.Bar(
                x=origins,
                y=values,
                name=name,
                marker_color=color,
                hovertemplate="%{x}<br>" + name + ": %{y:,}<extra></extra>",
            )
        )
    layout = _base_layout(
        "Acquisition funnel by channel: leads → deals → sellers with sales"
    )
    layout["barmode"] = "group"
    layout["xaxis"]["title"] = "Lead origin (marketing channel)"
    layout["yaxis"]["title"] = "Count (log scale)"
    layout["yaxis"]["type"] = "log"
    fig.update_layout(layout)
    return fig


def fig_channel_bar(
    data: bc.DashboardData,
    value_col: str,
    sort_col: str,
    title: str,
    ylabel: str,
    *,
    money: bool,
    pct: bool,
) -> object:
    """Single-series channel bar (conversion rate / gross sales)."""
    import plotly.graph_objects as go

    df = data.seller_acq.sort_values(sort_col, ascending=False)
    origins = df["origin"].tolist()
    values = df[value_col].tolist()
    colors = [
        PALETTE["ink_muted"] if o == "unknown" else PALETTE["series_1"] for o in origins
    ]
    if pct:
        hover = "%{x}<br>" + ylabel + ": %{y:.1%}<extra></extra>"
        text = [f"{v * 100:.1f}%" for v in values]
    elif money:
        hover = "%{x}<br>" + ylabel + ": R$%{y:,.2f}<extra></extra>"
        text = [bc.fmt_brl(float(v)) for v in values]
    else:
        hover = "%{x}<br>%{y:,}<extra></extra>"
        text = [f"{v:,}" for v in values]

    fig = go.Figure(
        go.Bar(
            x=origins,
            y=values,
            marker_color=colors,
            text=text,
            textposition="outside",
            hovertemplate=hover,
        )
    )
    if value_col == "conversion_rate":
        named = df[df["origin"] != "unknown"]
        best = named.loc[named["conversion_rate"].idxmax()]
        fig.add_annotation(
            x=best["origin"],
            y=best["conversion_rate"],
            text=f"Best named channel: {best['origin']} ({best['conversion_rate'] * 100:.1f}%)",
            showarrow=True,
            arrowhead=2,
            ay=-45,
            font={"size": 11, "color": PALETTE["ink_secondary"]},
            arrowcolor=PALETTE["ink_muted"],
        )

    layout = _base_layout(title)
    layout["xaxis"]["title"] = "Lead origin (marketing channel)"
    layout["yaxis"]["title"] = ylabel
    layout["showlegend"] = False
    if money:
        layout["yaxis"]["tickprefix"] = "R$"
        layout["yaxis"]["tickformat"] = "~s"
    if pct:
        layout["yaxis"]["tickformat"] = ".0%"
    fig.update_layout(layout)
    return fig


def fig_reconciliation(data: bc.DashboardData) -> object:
    """Reconciliation status counts (log scale + value labels)."""
    import plotly.graph_objects as go

    df = data.recon.sort_values("order_count", ascending=False)
    statuses = df["reconciliation_status"].tolist()
    counts = df["order_count"].tolist()
    colors = [RECON_COLORS.get(st, PALETTE["series_1"]) for st in statuses]

    fig = go.Figure(
        go.Bar(
            x=statuses,
            y=counts,
            marker_color=colors,
            text=[f"{int(c):,}" for c in counts],
            textposition="outside",
            hovertemplate="%{x}<br>%{y:,} orders<extra></extra>",
        )
    )
    layout = _base_layout("Orders by payment reconciliation status")
    layout["xaxis"]["title"] = "Reconciliation status"
    layout["yaxis"]["title"] = "Orders (log scale)"
    layout["yaxis"]["type"] = "log"
    layout["showlegend"] = False
    fig.update_layout(layout)
    return fig


def fig_review_late(data: bc.DashboardData) -> object:
    """Average review score, late vs on-time (status colours)."""
    import plotly.graph_objects as go

    on_time = data.review_late["on_time"]
    late = data.review_late["late"]
    labels = ["On-time delivery", "Late delivery"]
    scores = [on_time["avg_score"], late["avg_score"]]
    counts = [on_time["n"], late["n"]]

    fig = go.Figure(
        go.Bar(
            x=labels,
            y=scores,
            marker_color=[PALETTE["good"], PALETTE["critical"]],
            text=[f"{s:.2f}" for s in scores],
            textposition="outside",
            customdata=counts,
            hovertemplate="%{x}<br>Avg score: %{y:.2f}<br>n=%{customdata:,}<extra></extra>",
        )
    )
    fig.add_hline(
        y=4.0,
        line_dash="dash",
        line_color=PALETTE["ink_muted"],
        annotation_text="reference 4.0",
        annotation_position="top right",
    )
    layout = _base_layout("Average review score: late vs on-time delivery")
    layout["yaxis"]["title"] = "Average review score (1–5)"
    layout["yaxis"]["range"] = [0, 5]
    layout["showlegend"] = False
    fig.update_layout(layout)
    return fig


def fig_dq_issues(data: bc.DashboardData) -> object:
    """Key data-quality issue counts (horizontal bars)."""
    import plotly.graph_objects as go

    issues = data.dq_issues
    items = sorted(issues.items(), key=lambda kv: kv[1])
    labels = [k for k, _ in items]
    values = [v for _, v in items]

    fig = go.Figure(
        go.Bar(
            x=values,
            y=labels,
            orientation="h",
            marker_color=PALETTE["series_1"],
            text=[f"{v:,}" for v in values],
            textposition="outside",
            hovertemplate="%{y}<br>%{x:,}<extra></extra>",
        )
    )
    layout = _base_layout("Key data-quality issue counts")
    layout["xaxis"]["title"] = "Count"
    layout["margin"]["l"] = 220
    layout["showlegend"] = False
    fig.update_layout(layout)
    return fig


# --------------------------------------------------------------------------- #
# Assemble the single self-contained HTML file.
# --------------------------------------------------------------------------- #
PAGE_CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body {
  margin: 0;
  background: #f9f9f7;
  color: #0b0b0b;
  font-family: system-ui, -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif;
}
.wrap { max-width: 1180px; margin: 0 auto; padding: 32px 16px 64px; }
header.page-head { margin-bottom: 8px; }
header.page-head h1 { font-size: 28px; margin: 0 0 6px; }
header.page-head p { color: #52514e; margin: 0; font-size: 15px; }
section.page { margin-top: 40px; }
section.page > h2 {
  font-size: 21px; margin: 0 0 4px; padding-bottom: 8px;
  border-bottom: 2px solid #e1e0d9;
}
section.page > p.note { color: #52514e; font-size: 14px; margin: 6px 0 16px; }
.chart { background: #ffffff; border: 1px solid rgba(11,11,11,0.08);
  border-radius: 10px; padding: 8px 10px; margin: 18px 0; }
.kpi-row { display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px;
  margin: 18px 0; }
.kpi { background: #ffffff; border: 1px solid rgba(11,11,11,0.08);
  border-radius: 10px; padding: 16px 14px; }
.kpi-value { font-size: 26px; font-weight: 700; color: #0b0b0b; }
.kpi-label { font-size: 13px; color: #52514e; margin-top: 4px; }
@media (max-width: 760px) { .kpi-row { grid-template-columns: repeat(2, 1fr); } }
footer { margin-top: 48px; color: #898781; font-size: 13px; text-align: center; }
"""


def build_html(data: bc.DashboardData) -> str:
    """Render every figure and assemble the one self-contained HTML document."""
    from plotly.io import to_html
    from plotly.offline import get_plotlyjs

    # Build the figures grouped by page. Each page: (heading, note, prefix_html, figs).
    pages = [
        (
            "1 · Executive KPIs",
            ("How is the marketplace trending? The monthly trend runs from "
            "2017-01; partial boundary months (2016-09, 2018-09, 2018-10) and the "
            "sparse 2016 ramp-up are shown but excluded from the trend line."),
            kpi_cards_html(data),
            [
                fig_monthly_line(
                    data, "orders", "Monthly orders over time", "Orders",
                    PALETTE["series_1"], money=False,
                ),
                fig_monthly_line(
                    data, "gross_revenue", "Monthly gross revenue over time",
                    "Gross revenue (BRL)", PALETTE["series_3"], money=True,
                ),
            ],
        ),
        (
            "2 · Marketing funnel by channel",
            ("Which channels bring leads that become selling sellers? "
            "'unknown' is a bucket, not a real channel (shown muted). "
            "The gross-sales figures below cover only the 380 marketing-acquired "
            "sellers that actually sold (≈ R$775.8K, about 5% of total GMV) — "
            "the marketing-attributed subset of marketplace revenue, not the whole."),
            "",
            [
                fig_funnel(data),
                fig_channel_bar(
                    data, "conversion_rate", "conversion_rate",
                    "Lead-to-deal conversion rate by channel",
                    "Conversion rate", money=False, pct=True,
                ),
                fig_channel_bar(
                    data, "gross_sales", "gross_sales",
                    "Gross sales of marketing-acquired sellers by channel "
                    "(380 sellers; R$775.8K ≈ 5% of GMV)",
                    "Gross sales (BRL)",
                    money=True, pct=False,
                ),
            ],
        ),
        (
            "3 · Data quality & reconciliation",
            ("How trustworthy is the data, and where does money not tie out? "
            "'match' dominates, so counts use a log scale with value labels."),
            "",
            [
                fig_reconciliation(data),
                fig_review_late(data),
                fig_dq_issues(data),
            ],
        ),
    ]

    # plotly.js is inlined once in <head>; every chart div omits it.
    body_parts: list[str] = []
    for heading, note, prefix_html, figs in pages:
        chart_divs = []
        for fig in figs:
            div = to_html(
                fig,
                full_html=False,
                include_plotlyjs=False,
                config={"responsive": True, "displaylogo": False},
                default_width="100%",
            )
            chart_divs.append(f'<div class="chart">{div}</div>')
        body_parts.append(
            f'<section class="page"><h2>{heading}</h2>'
            f'<p class="note">{note}</p>{prefix_html}{"".join(chart_divs)}</section>'
        )

    plotly_js = get_plotlyjs()
    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Olist Warehouse Dashboard</title>
<style>{PAGE_CSS}</style>
<script type="text/javascript">{plotly_js}</script>
</head>
<body>
<div class="wrap">
<header class="page-head">
<h1>Multi-Source Data Warehouse — Olist Dashboard</h1>
<p>Interactive view of the reporting marts (hover for values, drag to zoom, \
double-click to reset). Three pages mirror <code>docs/dashboard_guide.md</code>.</p>
</header>
{"".join(body_parts)}
<footer>Built from <code>olist_warehouse.duckdb</code> · \
self-contained (plotly.js inlined, works offline).</footer>
</div>
</body>
</html>
"""
    return html


def build_dashboard(db_path: Path, output_file: Path, marts_schema: str) -> Path:
    """Load data, render the HTML, and write it to ``output_file``."""
    data = bc.open_data(db_path, marts_schema)
    html = build_html(data)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(html, encoding="utf-8")
    logger.info("Wrote %s (%.1f KB)", output_file, output_file.stat().st_size / 1024)
    return output_file


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Build the self-contained interactive Plotly dashboard."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=f"Path to the YAML config (default: {DEFAULT_CONFIG_PATH}).",
    )
    parser.add_argument(
        "--output-file",
        type=Path,
        default=DEFAULT_OUTPUT_FILE,
        help=f"Output HTML path (default: {DEFAULT_OUTPUT_FILE}).",
    )
    parser.add_argument(
        "--marts-schema",
        default=bc.DEFAULT_MARTS_SCHEMA,
        help=f"DuckDB schema the marts live in (default: {bc.DEFAULT_MARTS_SCHEMA}).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: INFO).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Build the interactive dashboard. Returns a process exit code."""
    args = parse_args(argv)
    bc.configure_logging(args.log_level)

    config = bc.load_config(args.config)
    db_path = bc.resolve_duckdb_path(config)

    logger.info("Building interactive dashboard | schema=%s", args.marts_schema)
    out = build_dashboard(db_path, args.output_file, args.marts_schema)
    logger.info("Done: %s", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
