#!/usr/bin/env python3
"""Render publication-quality dashboard PNGs from the reporting marts (Step 9).

This script is the static-chart half of the Python dashboard. It reads the
already-built marts from the DuckDB warehouse (read-only: it never writes to the
warehouse) and renders matplotlib PNGs into ``outputs/charts/``. The charts
mirror the three-page design in ``docs/dashboard_guide.md``:

* **Executive KPIs** -- monthly orders, monthly gross revenue, a KPI summary
  panel (total orders, people, repeat rate, gross revenue, AOV).
* **Marketing funnel** -- leads -> deals -> sellers-with-sales by channel,
  conversion rate by channel, gross sales by channel.
* **Data quality & reconciliation** -- reconciliation status counts (log scale),
  average review score late vs on-time, and a panel of key DQ issue counts.

Design follows the project's data-viz conventions: a validated, colorblind-safe
palette, titled charts, axes labeled with units (money as R$), recessive
gridlines, legible fonts, ``tight_layout`` and ~150 dpi output.

The warehouse path is resolved exactly as ``scripts/load_raw.py`` and
``scripts/export_dashboard.py`` resolve it: env ``DUCKDB_PATH`` first (the
Makefile exports an absolute path shared with dbt), then
``config.paths.duckdb_path``. No hard-coded paths or credentials.

The data-loading layer (``load_dashboard_data`` + ``DashboardData``) and the
shared palette are imported by ``scripts/build_dashboard_html.py`` so the two
renderers draw from a single source of truth for the SQL and the colours.
"""

from __future__ import annotations

import argparse
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import yaml

logger = logging.getLogger("build_charts")

# Project root = parent of the directory that holds this script (scripts/).
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"

# Default output directory for the chart PNGs (relative to root).
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "outputs" / "charts"

# DuckDB schema the marts materialize into: dbt's default schema ``main`` with
# the ``marts`` custom schema appended. Overridable via --marts-schema.
DEFAULT_MARTS_SCHEMA = "main_marts"

# Default raster resolution for the PNGs.
DEFAULT_DPI = 150

# Boundary months flagged partial in mart_monthly_kpis (truncated order window).
PARTIAL_MONTHS = ("2016-09", "2018-09", "2018-10")

# Monthly trend lines start here. The 2016 ramp-up (2016-09 partial, 2016-10,
# the missing 2016-11, and the lone 2016-12 order) is sparse and makes the line
# dive to ~0; those months are shown de-emphasized but excluded from the trend,
# exactly like the partial boundary months. The marketplace reaches steady volume
# from 2017-01 onward.
TREND_START = "2017-01-01"


# --------------------------------------------------------------------------- #
# Palette (validated against the data-viz skill's six checks; see palette.md).
# Categorical slots are the documented, colorblind-safe default order. Text/ink
# and chrome tokens keep labels off the series colours.
# --------------------------------------------------------------------------- #
PALETTE: dict[str, str] = {
    # Categorical slots (fixed order, never cycled).
    "series_1": "#2a78d6",  # blue
    "series_2": "#eb6834",  # orange
    "series_3": "#1baf7a",  # aqua
    "series_4": "#eda100",  # yellow
    "series_5": "#e87ba4",  # magenta
    "series_6": "#008300",  # green
    "series_7": "#6250d6",  # violet
    "series_8": "#e34948",  # red
    # Ordinal blue ramp for the funnel (leads -> deals -> sellers): light->dark,
    # light end >= 2:1 on the light surface (step 250).
    "funnel_leads": "#86b6ef",
    "funnel_deals": "#2a78d6",
    "funnel_sellers": "#184f95",
    # Status tokens (reserved meaning; never reused for "series N").
    "good": "#0ca30c",
    "warning": "#fab219",
    "serious": "#ec835a",
    "critical": "#d03b3b",
    # Chrome / ink.
    "surface": "#ffffff",  # white page so GitHub renders the PNGs cleanly
    "ink_primary": "#0b0b0b",
    "ink_secondary": "#52514e",
    "ink_muted": "#898781",
    "grid": "#e1e0d9",
    "axis": "#c3c2b7",
}

# One consistent colour per reconciliation status, reused across both renderers.
RECON_COLORS: dict[str, str] = {
    "match": PALETTE["series_1"],
    "no_items": PALETTE["series_2"],
    "overpaid": PALETTE["series_4"],
    "underpaid": PALETTE["series_5"],
    "no_payment": PALETTE["series_8"],
}


# --------------------------------------------------------------------------- #
# Configuration / environment helpers (mirrors scripts/export_dashboard.py).
# --------------------------------------------------------------------------- #
def configure_logging(level: str = "INFO") -> None:
    """Configure root logging with a simple, consistent format."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def load_config(config_path: Path) -> dict:
    """Load and return the YAML config as a dict.

    Raises:
        FileNotFoundError: If the config file does not exist.
    """
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    with config_path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def resolve_duckdb_path(config: dict) -> Path:
    """Return the DuckDB file path, reading env ``DUCKDB_PATH`` first.

    The Makefile exports an absolute ``DUCKDB_PATH`` shared with dbt, so the
    environment variable wins over the config fallback.
    """
    env_value = os.environ.get("DUCKDB_PATH")
    if env_value:
        return Path(env_value)
    path_value = config.get("paths", {}).get("duckdb_path", "olist_warehouse.duckdb")
    path = Path(path_value)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


# --------------------------------------------------------------------------- #
# Data loading (shared with build_dashboard_html.py).
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class DashboardData:
    """Every frame / summary the dashboard renderers need, loaded once.

    Attributes:
        monthly: One row per purchase month (mart_monthly_kpis), sorted.
        seller_acq: One row per marketing channel (mart_seller_acquisition),
            sorted by leads descending.
        recon: Reconciliation status counts, one row per status.
        totals: Headline KPIs (orders, people, repeat rate, gross, AOV).
        review_late: Count and avg review score split by on-time vs late.
        dq_issues: Ordered mapping of key data-quality issue -> count.
    """

    monthly: object
    seller_acq: object
    recon: object
    totals: dict[str, float]
    review_late: dict[str, dict[str, float]]
    dq_issues: dict[str, int]


def load_dashboard_data(con: object, marts_schema: str = DEFAULT_MARTS_SCHEMA) -> DashboardData:
    """Run every dashboard query against an open read-only connection.

    Args:
        con: An open read-only DuckDB connection.
        marts_schema: Schema the marts live in (fills the ``{s}`` token).

    Returns:
        A populated :class:`DashboardData`.
    """
    s = marts_schema

    monthly = con.execute(
        f"""
        select month_start, orders, distinct_customers, gross_revenue,
               avg_order_value, late_delivery_rate, avg_review_score,
               is_partial_month
        from {s}.mart_monthly_kpis
        order by month_start
        """
    ).df()

    seller_acq = con.execute(
        f"""
        select origin, leads, closed_deals, conversion_rate, sellers_with_sales,
               gross_sales, sales_per_deal, deals_won_after_order_data_ends
        from {s}.mart_seller_acquisition
        order by leads desc
        """
    ).df()

    recon = con.execute(
        f"""
        select reconciliation_status, count(*) as order_count
        from {s}.fct_orders
        group by reconciliation_status
        order by order_count desc
        """
    ).df()

    totals_row = con.execute(
        f"""
        select
            (select count(*) from {s}.fct_orders)                       as orders,
            (select count(*) from {s}.dim_customer)                     as people,
            (select count(*) from {s}.dim_customer
                where is_repeat_customer)                               as repeat_people,
            (select sum(total_item_value) from {s}.fct_orders)         as gross,
            (select avg(total_item_value) from {s}.fct_orders)         as aov
        """
    ).fetchall()[0]
    orders, people, repeat_people, gross, aov = totals_row
    totals = {
        "orders": int(orders),
        "people": int(people),
        "repeat_people": int(repeat_people),
        "repeat_rate": float(repeat_people) / float(people) if people else 0.0,
        "gross": float(gross),
        "aov": float(aov),
    }

    review_rows = con.execute(
        f"""
        select is_late, count(*) as n, avg(latest_review_score) as avg_score
        from {s}.fct_orders
        where is_late is not null and latest_review_score is not null
        group by is_late
        """
    ).fetchall()
    review_late: dict[str, dict[str, float]] = {}
    for is_late, n, avg_score in review_rows:
        key = "late" if is_late else "on_time"
        review_late[key] = {"n": int(n), "avg_score": float(avg_score)}

    # Key data-quality issue counts (DQ panel). Each is a scalar lookup.
    no_items = int(
        con.execute(
            f"select count(*) from {s}.fct_orders "
            "where reconciliation_status = 'no_items'"
        ).fetchall()[0][0]
    )
    payment_mismatch = int(
        con.execute(
            f"select count(*) from {s}.fct_orders "
            "where reconciliation_status in ('overpaid', 'underpaid')"
        ).fetchall()[0][0]
    )
    no_category = int(
        con.execute(
            f"select count(*) from {s}.dim_product "
            "where category_translation_status = 'missing'"
        ).fetchall()[0][0]
    )
    multi_review = int(
        con.execute(
            f"""
            select count(*) from (
                select order_id from raw.order_reviews
                group by order_id having count(distinct review_id) > 1
            ) t
            where order_id in (select order_id from {s}.fct_orders)
            """
        ).fetchall()[0][0]
    )
    unknown_origin = int(
        con.execute(
            f"select leads from {s}.mart_seller_acquisition where origin = 'unknown'"
        ).fetchall()[0][0]
    )
    dq_issues = {
        "Orders with no line items": no_items,
        "Payment mismatches (over/under)": payment_mismatch,
        "Products with no category": no_category,
        "Orders with >1 review": multi_review,
        "Leads with unknown origin": unknown_origin,
    }

    return DashboardData(
        monthly=monthly,
        seller_acq=seller_acq,
        recon=recon,
        totals=totals,
        review_late=review_late,
        dq_issues=dq_issues,
    )


def open_data(db_path: Path, marts_schema: str) -> DashboardData:
    """Open the warehouse read-only, load every dataset, and close it.

    Raises:
        FileNotFoundError: If the DuckDB warehouse file does not exist.
    """
    import duckdb

    if not db_path.exists():
        raise FileNotFoundError(
            f"DuckDB warehouse not found: {db_path}. Build it first (make build)."
        )
    logger.info("Opening DuckDB (read-only) at %s", db_path)
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        return load_dashboard_data(con, marts_schema)
    finally:
        con.close()


# --------------------------------------------------------------------------- #
# Formatting helpers.
# --------------------------------------------------------------------------- #
def fmt_brl(value: float) -> str:
    """Format a BRL amount compactly (R$1.2M / R$234K / R$56)."""
    if abs(value) >= 1_000_000:
        return f"R${value / 1_000_000:.2f}M"
    if abs(value) >= 1_000:
        return f"R${value / 1_000:.0f}K"
    return f"R${value:,.0f}"


def _brl_axis_formatter() -> object:
    """Return a matplotlib FuncFormatter that labels a money axis in R$."""
    from matplotlib.ticker import FuncFormatter

    return FuncFormatter(lambda v, _pos: fmt_brl(v))


# --------------------------------------------------------------------------- #
# Chart styling.
# --------------------------------------------------------------------------- #
def apply_style() -> None:
    """Set shared matplotlib rcParams for a consistent, legible look."""
    import matplotlib

    matplotlib.rcParams.update(
        {
            "figure.facecolor": PALETTE["surface"],
            "savefig.facecolor": PALETTE["surface"],
            "axes.facecolor": PALETTE["surface"],
            "font.family": "sans-serif",
            "font.sans-serif": ["Helvetica", "Arial", "DejaVu Sans"],
            "font.size": 11,
            "axes.titlesize": 15,
            "axes.titleweight": "bold",
            "axes.titlepad": 12,
            "axes.labelsize": 12,
            "axes.labelcolor": PALETTE["ink_secondary"],
            "axes.edgecolor": PALETTE["axis"],
            "axes.linewidth": 1.0,
            "axes.grid": True,
            "axes.grid.axis": "y",
            "axes.axisbelow": True,
            "grid.color": PALETTE["grid"],
            "grid.linewidth": 0.8,
            "text.color": PALETTE["ink_primary"],
            "xtick.color": PALETTE["ink_muted"],
            "ytick.color": PALETTE["ink_muted"],
            "xtick.labelsize": 10,
            "ytick.labelsize": 10,
            "legend.fontsize": 10,
            "legend.frameon": False,
            "figure.autolayout": False,
        }
    )


def _despine(ax: object) -> None:
    """Hide the top and right spines for a cleaner frame."""
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)


def _save(fig: object, out_dir: Path, name: str, dpi: int) -> Path:
    """Tight-layout, save ``fig`` to ``out_dir/name`` and return the path."""
    fig.tight_layout()
    out_path = out_dir / name
    fig.savefig(out_path, dpi=dpi, bbox_inches="tight")
    import matplotlib.pyplot as plt

    plt.close(fig)
    logger.info("Wrote %s", out_path)
    return out_path


# --------------------------------------------------------------------------- #
# Page 1 -- Executive KPIs.
# --------------------------------------------------------------------------- #
def trend_split(df: object) -> tuple[object, object]:
    """Split monthly rows into (trend, excluded).

    Trend months are complete (not ``is_partial_month``) and at/after
    :data:`TREND_START`; everything else -- the partial boundary months and the
    sparse 2016 ramp-up -- is excluded from the line and shown de-emphasized.
    """
    import pandas as pd

    start = pd.Timestamp(TREND_START)
    trend_mask = (~df["is_partial_month"]) & (df["month_start"] >= start)
    return df[trend_mask], df[~trend_mask]


def _monthly_line(
    data: DashboardData,
    out_dir: Path,
    dpi: int,
    *,
    value_col: str,
    color: str,
    title: str,
    ylabel: str,
    money: bool,
    name: str,
) -> Path:
    """Shared monthly trend line with de-emphasized partial / ramp-up months."""
    import matplotlib.pyplot as plt

    trend, excluded = trend_split(data.monthly)

    fig, ax = plt.subplots(figsize=(10, 5.2))
    ax.plot(
        trend["month_start"],
        trend[value_col],
        color=color,
        linewidth=2.2,
        marker="o",
        markersize=5,
        label="Monthly trend (2017-01 onward)",
    )
    ax.scatter(
        excluded["month_start"],
        excluded[value_col],
        facecolors="white",
        edgecolors=PALETTE["ink_muted"],
        linewidths=1.5,
        s=55,
        zorder=5,
        label="Partial / 2016 ramp-up months (excluded from trend)",
    )

    # Annotate the peak trend month, placed in the open upper-left area so the
    # label never sits on top of the line.
    peak = trend.loc[trend[value_col].idxmax()]
    peak_value = (
        fmt_brl(float(peak[value_col])) if money else f"{int(peak[value_col]):,} orders"
    )
    ax.annotate(
        f"Peak: {peak['month_start'].strftime('%b %Y')}\n{peak_value}",
        xy=(peak["month_start"], peak[value_col]),
        xytext=(0.52, 0.30),
        textcoords="axes fraction",
        ha="center",
        va="center",
        fontsize=9,
        color=PALETTE["ink_secondary"],
        arrowprops={"arrowstyle": "->", "color": PALETTE["ink_muted"]},
    )

    ax.set_title(title)
    ax.set_xlabel("Purchase month")
    ax.set_ylabel(ylabel)
    ax.set_ylim(bottom=0)
    if money:
        ax.yaxis.set_major_formatter(_brl_axis_formatter())
    ax.legend(loc="upper left")
    _despine(ax)
    fig.autofmt_xdate()
    return _save(fig, out_dir, name, dpi)


def chart_monthly_orders(data: DashboardData, out_dir: Path, dpi: int) -> Path:
    """(a) Monthly orders over time; partial / ramp-up months de-emphasized."""
    return _monthly_line(
        data,
        out_dir,
        dpi,
        value_col="orders",
        color=PALETTE["series_1"],
        title="Monthly orders over time",
        ylabel="Orders",
        money=False,
        name="exec_monthly_orders.png",
    )


def chart_monthly_revenue(data: DashboardData, out_dir: Path, dpi: int) -> Path:
    """(b) Monthly gross revenue over time; partial / ramp-up months de-emphasized."""
    return _monthly_line(
        data,
        out_dir,
        dpi,
        value_col="gross_revenue",
        color=PALETTE["series_3"],
        title="Monthly gross revenue over time",
        ylabel="Gross revenue (BRL)",
        money=True,
        name="exec_monthly_revenue.png",
    )


def chart_kpi_summary(data: DashboardData, out_dir: Path, dpi: int) -> Path:
    """(c) KPI summary panel: orders, people, repeat rate, gross, AOV."""
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyBboxPatch

    t = data.totals
    cards = [
        ("Total orders", f"{t['orders']:,}", PALETTE["series_1"]),
        ("Customers (people)", f"{t['people']:,}", PALETTE["series_3"]),
        ("Repeat-order rate", f"{t['repeat_rate'] * 100:.2f}%", PALETTE["series_7"]),
        (
            "Gross order value (GMV)\nitems + freight, all orders",
            fmt_brl(t["gross"]),
            PALETTE["series_6"],
        ),
        (
            "Avg order value\nincl. 775 zero-item orders",
            f"R${t['aov']:,.2f}",
            PALETTE["series_2"],
        ),
    ]

    fig, ax = plt.subplots(figsize=(12, 3.4))
    ax.set_xlim(0, len(cards))
    ax.set_ylim(0, 1)
    ax.axis("off")
    ax.set_title("Executive KPI summary (full order history)", loc="center")

    for i, (label, value, accent) in enumerate(cards):
        box = FancyBboxPatch(
            (i + 0.06, 0.12),
            0.88,
            0.66,
            boxstyle="round,pad=0.02,rounding_size=0.04",
            linewidth=0,
            facecolor="#f4f4f1",
        )
        ax.add_patch(box)
        # Accent bar on the left edge of the card.
        ax.add_patch(
            plt.Rectangle((i + 0.06, 0.12), 0.03, 0.66, color=accent, linewidth=0)
        )
        ax.text(
            i + 0.5,
            0.60,
            value,
            ha="center",
            va="center",
            fontsize=21,
            fontweight="bold",
            color=PALETTE["ink_primary"],
        )
        ax.text(
            i + 0.5,
            0.38,
            label,
            ha="center",
            va="top",
            fontsize=9.5,
            color=PALETTE["ink_secondary"],
        )
    return _save(fig, out_dir, "exec_kpi_summary.png", dpi)


# --------------------------------------------------------------------------- #
# Page 2 -- Marketing funnel by channel.
# --------------------------------------------------------------------------- #
def chart_funnel(data: DashboardData, out_dir: Path, dpi: int) -> Path:
    """(d) Grouped bar: leads vs closed deals vs sellers-with-sales by origin."""
    import matplotlib.pyplot as plt
    import numpy as np

    df = data.seller_acq.sort_values("leads", ascending=False)
    origins = df["origin"].tolist()
    x = np.arange(len(origins))
    width = 0.27

    fig, ax = plt.subplots(figsize=(12, 5.6))
    ax.bar(
        x - width,
        df["leads"],
        width,
        color=PALETTE["funnel_leads"],
        label="Leads",
    )
    ax.bar(
        x,
        df["closed_deals"],
        width,
        color=PALETTE["funnel_deals"],
        label="Closed deals",
    )
    ax.bar(
        x + width,
        df["sellers_with_sales"],
        width,
        color=PALETTE["funnel_sellers"],
        label="Sellers with sales",
    )

    ax.set_title("Acquisition funnel by channel: leads -> deals -> sellers with sales")
    ax.set_xlabel("Lead origin (marketing channel)")
    ax.set_ylabel("Count (log scale)")
    ax.set_yscale("log")
    ax.set_xticks(x)
    ax.set_xticklabels(origins, rotation=35, ha="right")
    ax.legend(loc="upper right", ncol=3)
    ax.grid(axis="y", which="both")
    _despine(ax)
    return _save(fig, out_dir, "funnel_leads_deals_sellers.png", dpi)


def _channel_bar(
    data: DashboardData,
    out_dir: Path,
    dpi: int,
    *,
    value_col: str,
    sort_col: str,
    title: str,
    ylabel: str,
    name: str,
    is_money: bool,
    is_pct: bool,
    highlight_best_named: bool,
    dpi_label_fmt: Callable[[float], str],
) -> Path:
    """Shared single-series channel bar (conversion rate / gross sales)."""
    import matplotlib.pyplot as plt

    df = data.seller_acq.sort_values(sort_col, ascending=False)
    origins = df["origin"].tolist()
    values = df[value_col].tolist()

    # 'unknown' is a bucket, not a real channel -> muted so it never reads as a
    # top performer; every real channel keeps the slot-1 identity colour.
    colors = [
        PALETTE["ink_muted"] if origin == "unknown" else PALETTE["series_1"]
        for origin in origins
    ]

    fig, ax = plt.subplots(figsize=(11, 5.6))
    bars = ax.bar(origins, values, color=colors, width=0.68)

    for bar, value in zip(bars, values):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height(),
            dpi_label_fmt(value),
            ha="center",
            va="bottom",
            fontsize=9,
            color=PALETTE["ink_secondary"],
        )

    if highlight_best_named:
        named = df[df["origin"] != "unknown"]
        best = named.loc[named["conversion_rate"].idxmax()]
        ax.annotate(
            f"Best named channel:\n{best['origin']} ({best['conversion_rate'] * 100:.1f}%)",
            xy=(origins.index(best["origin"]), float(best["conversion_rate"])),
            xytext=(0, 42),
            textcoords="offset points",
            ha="center",
            fontsize=9,
            color=PALETTE["ink_secondary"],
            arrowprops={"arrowstyle": "->", "color": PALETTE["ink_muted"]},
        )

    ax.set_title(title)
    ax.set_xlabel("Lead origin (marketing channel)")
    ax.set_ylabel(ylabel)
    ax.set_xticks(range(len(origins)))
    ax.set_xticklabels(origins, rotation=35, ha="right")
    if is_money:
        ax.yaxis.set_major_formatter(_brl_axis_formatter())
    if is_pct:
        from matplotlib.ticker import FuncFormatter

        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _p: f"{v * 100:.0f}%"))
    ax.margins(y=0.16)
    _despine(ax)
    return _save(fig, out_dir, name, dpi)


def chart_conversion_rate(data: DashboardData, out_dir: Path, dpi: int) -> Path:
    """(e) Conversion rate by channel (unknown muted; best named flagged)."""
    return _channel_bar(
        data,
        out_dir,
        dpi,
        value_col="conversion_rate",
        sort_col="conversion_rate",
        title="Lead-to-deal conversion rate by channel",
        ylabel="Conversion rate (closed deals / leads)",
        name="funnel_conversion_rate.png",
        is_money=False,
        is_pct=True,
        highlight_best_named=True,
        dpi_label_fmt=lambda v: f"{v * 100:.1f}%",
    )


def chart_gross_sales(data: DashboardData, out_dir: Path, dpi: int) -> Path:
    """(f) Gross sales by channel (money axis; unknown bucket muted)."""
    return _channel_bar(
        data,
        out_dir,
        dpi,
        value_col="gross_sales",
        sort_col="gross_sales",
        title=(
            "Gross sales of marketing-acquired sellers by channel\n"
            "(380 sellers; R$775.8K ~ 5% of GMV)"
        ),
        ylabel="Gross sales (BRL)",
        name="funnel_gross_sales.png",
        is_money=True,
        is_pct=False,
        highlight_best_named=False,
        dpi_label_fmt=lambda v: fmt_brl(float(v)),
    )


# --------------------------------------------------------------------------- #
# Page 3 -- Data quality & reconciliation.
# --------------------------------------------------------------------------- #
def chart_reconciliation(data: DashboardData, out_dir: Path, dpi: int) -> Path:
    """(g) Reconciliation status counts (log scale + value labels)."""
    import matplotlib.pyplot as plt

    df = data.recon.sort_values("order_count", ascending=False)
    statuses = df["reconciliation_status"].tolist()
    counts = df["order_count"].tolist()
    colors = [RECON_COLORS.get(st, PALETTE["series_1"]) for st in statuses]

    fig, ax = plt.subplots(figsize=(10, 5.4))
    bars = ax.bar(statuses, counts, color=colors, width=0.62)
    ax.set_yscale("log")

    for bar, value in zip(bars, counts):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height(),
            f"{int(value):,}",
            ha="center",
            va="bottom",
            fontsize=10,
            fontweight="bold",
            color=PALETTE["ink_secondary"],
        )

    ax.set_title("Orders by payment reconciliation status")
    ax.set_xlabel("Reconciliation status")
    ax.set_ylabel("Orders (log scale)")
    ax.grid(axis="y", which="both")
    ax.margins(y=0.22)
    _despine(ax)
    return _save(fig, out_dir, "dq_reconciliation_status.png", dpi)


def chart_review_late(data: DashboardData, out_dir: Path, dpi: int) -> Path:
    """(h) Average review score, late vs on-time (status colours)."""
    import matplotlib.pyplot as plt

    on_time = data.review_late["on_time"]
    late = data.review_late["late"]
    labels = ["On-time delivery", "Late delivery"]
    scores = [on_time["avg_score"], late["avg_score"]]
    colors = [PALETTE["good"], PALETTE["critical"]]

    fig, ax = plt.subplots(figsize=(7.4, 5.4))
    bars = ax.bar(labels, scores, color=colors, width=0.56)
    for bar, value, meta in zip(bars, scores, [on_time, late]):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height(),
            f"{value:.2f}\n(n={meta['n']:,})",
            ha="center",
            va="bottom",
            fontsize=11,
            color=PALETTE["ink_secondary"],
        )

    ax.axhline(4.0, color=PALETTE["ink_muted"], linestyle="--", linewidth=1)
    ax.text(
        1.45,
        4.03,
        "reference 4.0",
        fontsize=8,
        color=PALETTE["ink_muted"],
        ha="right",
    )
    ax.set_title("Average review score: late vs on-time delivery")
    ax.set_ylabel("Average review score (1–5)")
    ax.set_ylim(0, 5)
    _despine(ax)
    return _save(fig, out_dir, "dq_review_late_vs_ontime.png", dpi)


def chart_dq_issues(data: DashboardData, out_dir: Path, dpi: int) -> Path:
    """(i) Panel of key data-quality issue counts (horizontal bars)."""
    import matplotlib.pyplot as plt

    issues = data.dq_issues
    labels = list(issues.keys())
    values = list(issues.values())
    # Largest at the top for easy reading.
    order = sorted(range(len(values)), key=lambda i: values[i])
    labels = [labels[i] for i in order]
    values = [values[i] for i in order]

    fig, ax = plt.subplots(figsize=(10, 5.0))
    bars = ax.barh(labels, values, color=PALETTE["series_1"], height=0.62)
    for bar, value in zip(bars, values):
        ax.text(
            bar.get_width(),
            bar.get_y() + bar.get_height() / 2,
            f"  {int(value):,}",
            va="center",
            ha="left",
            fontsize=10,
            fontweight="bold",
            color=PALETTE["ink_secondary"],
        )

    ax.set_title("Key data-quality issue counts")
    ax.set_xlabel("Count")
    ax.grid(axis="x")
    ax.margins(x=0.12)
    _despine(ax)
    return _save(fig, out_dir, "dq_issue_counts.png", dpi)


# Registry: (page, function). Order is cosmetic (logging only).
CHART_BUILDERS: list[tuple[str, Callable[[DashboardData, Path, int], Path]]] = [
    ("Executive KPIs", chart_monthly_orders),
    ("Executive KPIs", chart_monthly_revenue),
    ("Executive KPIs", chart_kpi_summary),
    ("Marketing funnel", chart_funnel),
    ("Marketing funnel", chart_conversion_rate),
    ("Marketing funnel", chart_gross_sales),
    ("Data quality", chart_reconciliation),
    ("Data quality", chart_review_late),
    ("Data quality", chart_dq_issues),
]


def build_all_charts(data: DashboardData, output_dir: Path, dpi: int) -> list[Path]:
    """Render every chart PNG into ``output_dir``. Returns the written paths."""
    apply_style()
    output_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for page, builder in CHART_BUILDERS:
        logger.info("[%s] %s", page, builder.__name__)
        paths.append(builder(data, output_dir, dpi))
    return paths


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Render matplotlib dashboard PNGs from the reporting marts."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=f"Path to the YAML config (default: {DEFAULT_CONFIG_PATH}).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Directory for the chart PNGs (default: {DEFAULT_OUTPUT_DIR}).",
    )
    parser.add_argument(
        "--marts-schema",
        default=DEFAULT_MARTS_SCHEMA,
        help=f"DuckDB schema the marts live in (default: {DEFAULT_MARTS_SCHEMA}).",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=DEFAULT_DPI,
        help=f"Raster resolution for the PNGs (default: {DEFAULT_DPI}).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: INFO).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Render every dashboard PNG. Returns a process exit code."""
    args = parse_args(argv)
    configure_logging(args.log_level)

    config = load_config(args.config)
    db_path = resolve_duckdb_path(config)

    logger.info(
        "Building %d chart(s) | schema=%s | dpi=%d | output=%s",
        len(CHART_BUILDERS),
        args.marts_schema,
        args.dpi,
        args.output_dir,
    )
    data = open_data(db_path, args.marts_schema)
    paths = build_all_charts(data, args.output_dir, args.dpi)
    logger.info("Done: %d PNG(s) written to %s", len(paths), args.output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
