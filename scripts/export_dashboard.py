#!/usr/bin/env python3
"""Export CSV extracts of the reporting marts for Tableau / Power BI (Step 9).

This script implements the dashboard-extract half of Step 9 of the project. It
reads the already-built marts from the DuckDB warehouse (read-only: it never
writes to the warehouse) and writes one CSV per extract into
``outputs/dashboard/``. Those CSVs are the data sources for the three-page
dashboard described in ``docs/dashboard_guide.md``.

Extracts written (see ``EXPORT_SPECS``):

    mart_monthly_kpis.csv       -- monthly KPI roll-up (Executive page).
    mart_seller_acquisition.csv -- funnel by marketing channel (Funnel page).
    fct_orders.csv              -- one row per order, 99,441 rows (all pages).
    reconciliation_summary.csv  -- payment reconciliation status + counts (DQ page).
    dim_seller.csv              -- seller dimension incl. lead origin (Funnel page).
    dim_product.csv             -- product dimension incl. category (Funnel page).

Each extract is either a whole mart table or a small aggregate query. The marts
live in the DuckDB schema ``main_marts`` (dbt's ``main`` default schema + the
``marts`` custom schema); override with ``--marts-schema`` if needed.

The warehouse path is resolved exactly as ``scripts/load_raw.py`` resolves it:
env ``DUCKDB_PATH`` first (the Makefile exports an absolute path shared with
dbt), then ``config.paths.duckdb_path``. No hard-coded paths or credentials.
"""

from __future__ import annotations

import argparse
import logging
import os
from dataclasses import dataclass
from pathlib import Path

import yaml

logger = logging.getLogger("export_dashboard")

# Project root = parent of the directory that holds this script (scripts/).
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"

# Default output directory for the dashboard CSV extracts (relative to root).
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "outputs" / "dashboard"

# DuckDB schema the marts materialize into: dbt's default schema ``main`` with
# the ``marts`` custom schema appended. Overridable via --marts-schema.
DEFAULT_MARTS_SCHEMA = "main_marts"


@dataclass(frozen=True)
class ExportSpec:
    """Describes one CSV extract and the query that produces it.

    Attributes:
        name: Output file name (written under the output directory).
        sql: A SELECT whose ``{schema}`` placeholder is filled with the marts
            schema. Whole-table extracts are simply ``select * from {schema}.x``.
        description: Human-readable note for the log line.
    """

    name: str
    sql: str
    description: str


# Registry of every dashboard extract. Config-driven in spirit: add a row here
# and the extract is produced on the next run. Order is cosmetic (logging only).
EXPORT_SPECS: list[ExportSpec] = [
    ExportSpec(
        name="mart_monthly_kpis.csv",
        sql="select * from {schema}.mart_monthly_kpis order by month_start",
        description="Monthly KPI roll-up (orders, revenue, AOV, late rate, reviews).",
    ),
    ExportSpec(
        name="mart_seller_acquisition.csv",
        sql=(
            "select * from {schema}.mart_seller_acquisition "
            "order by closed_deals desc"
        ),
        description="Seller acquisition funnel by marketing channel (lead origin).",
    ),
    ExportSpec(
        name="fct_orders.csv",
        sql="select * from {schema}.fct_orders order by order_purchase_timestamp",
        description="Order fact, one row per order (99,441 rows).",
    ),
    ExportSpec(
        name="reconciliation_summary.csv",
        sql=(
            "select "
            "  reconciliation_status, "
            "  count(*) as order_count, "
            "  round(100.0 * count(*) / sum(count(*)) over (), 4) as pct_of_orders "
            "from {schema}.fct_orders "
            "group by reconciliation_status "
            "order by order_count desc"
        ),
        description="Payment reconciliation status buckets with counts and shares.",
    ),
    ExportSpec(
        name="dim_seller.csv",
        sql="select * from {schema}.dim_seller order by seller_id",
        description="Seller dimension (lead origin, acquired_via_marketing, source).",
    ),
    ExportSpec(
        name="dim_product.csv",
        sql="select * from {schema}.dim_product order by product_id",
        description="Product dimension (category + translation status).",
    ),
]


# --------------------------------------------------------------------------- #
# Configuration / environment helpers (mirrors scripts/load_raw.py)
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
# Export
# --------------------------------------------------------------------------- #
def export_one(
    con: object,
    spec: ExportSpec,
    output_dir: Path,
    marts_schema: str,
) -> int:
    """Run one extract query and write it to a CSV. Returns the row count.

    Args:
        con: An open read-only DuckDB connection.
        spec: The extract to produce.
        output_dir: Directory the CSV is written into.
        marts_schema: Schema the marts live in (fills the ``{schema}`` token).

    Returns:
        The number of data rows written.
    """
    sql = spec.sql.format(schema=marts_schema)
    frame = con.execute(sql).df()
    out_path = output_dir / spec.name
    frame.to_csv(out_path, index=False)
    logger.info(
        "Wrote %s (%d rows, %d cols) -- %s",
        out_path,
        len(frame),
        frame.shape[1],
        spec.description,
    )
    return len(frame)


def run_export(
    db_path: Path,
    output_dir: Path,
    marts_schema: str,
) -> dict[str, int]:
    """Open the warehouse read-only and write every extract. Returns row counts.

    Raises:
        FileNotFoundError: If the DuckDB warehouse file does not exist.
    """
    import duckdb

    if not db_path.exists():
        raise FileNotFoundError(
            f"DuckDB warehouse not found: {db_path}. Build it first (make build)."
        )
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Opening DuckDB (read-only) at %s", db_path)
    con = duckdb.connect(str(db_path), read_only=True)
    counts: dict[str, int] = {}
    try:
        for spec in EXPORT_SPECS:
            counts[spec.name] = export_one(con, spec, output_dir, marts_schema)
    finally:
        con.close()
    return counts


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Export CSV extracts of the marts for Tableau / Power BI."
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
        help=f"Directory for the CSV extracts (default: {DEFAULT_OUTPUT_DIR}).",
    )
    parser.add_argument(
        "--marts-schema",
        default=DEFAULT_MARTS_SCHEMA,
        help=f"DuckDB schema the marts live in (default: {DEFAULT_MARTS_SCHEMA}).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: INFO).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Export every dashboard CSV. Returns a process exit code."""
    args = parse_args(argv)
    configure_logging(args.log_level)

    config = load_config(args.config)
    db_path = resolve_duckdb_path(config)

    logger.info(
        "Exporting %d extract(s) | schema=%s | output=%s",
        len(EXPORT_SPECS),
        args.marts_schema,
        args.output_dir,
    )
    counts = run_export(db_path, args.output_dir, args.marts_schema)

    total = sum(counts.values())
    logger.info(
        "Export complete: %d file(s), %d total rows written to %s",
        len(counts),
        total,
        args.output_dir,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
