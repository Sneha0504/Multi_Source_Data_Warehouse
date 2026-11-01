#!/usr/bin/env python3
"""Land the raw Olist source CSVs into versioned ``raw`` tables (Milestone 3).

This script implements Step 5 of the project ("Ingestion and incremental
loads"). It reads each source CSV *unchanged* (as strings, so ids are not
coerced to floats and quoted embedded newlines parse correctly) and lands it
into a table in the ``raw`` schema, adding exactly four metadata columns:

* ``_source_system`` -- ``'ecommerce'`` for the 8 e-commerce files,
  ``'crm'`` for the 2 marketing-funnel files.
* ``_source_file``   -- the originating CSV filename.
* ``_loaded_at``     -- the batch's ``loaded_at`` value (from config).
* ``_row_hash``      -- md5 of ALL source columns (never the metadata columns),
  computed deterministically in pandas: every value is cast to ``str`` (NaN /
  None -> ``''``), joined in source-column order with ``'||'``, and md5-hashed.
  The hash is stable across runs and batches, so identical source content always
  produces the same hash.

Raw table naming (schema ``raw``) -- ``olist_`` prefix and ``_dataset`` suffix
are stripped. Downstream staging models (next milestone) depend on this mapping:

    olist_customers_dataset.csv                   -> raw.customers
    olist_orders_dataset.csv                      -> raw.orders
    olist_order_items_dataset.csv                 -> raw.order_items
    olist_order_payments_dataset.csv              -> raw.order_payments
    olist_order_reviews_dataset.csv               -> raw.order_reviews
    olist_products_dataset.csv                    -> raw.products
    olist_sellers_dataset.csv                     -> raw.sellers
    product_category_name_translation.csv         -> raw.product_category_name_translation
    olist_marketing_qualified_leads_dataset.csv   -> raw.marketing_qualified_leads
    olist_closed_deals_dataset.csv                -> raw.closed_deals

Loads are idempotent and append-only / versioned. Every version of every row is
kept. On each load the existing ``_row_hash`` set for the table is queried and
only rows whose hash is not already present are appended, so re-running a batch
adds nothing. Because batch 1 writes an AS-OF (rolled-back / masked) version of
in-flight orders and reviews, the true rows loaded in batch 2 hash differently
and land as new versions -- a CDC feed that falls out of the hash dedup with no
special-casing of "changes".

Two warehouse targets are supported behind a small interface:

* DuckDB (local, used here): the file at env ``DUCKDB_PATH`` (env is read FIRST,
  since the Makefile exports an absolute path shared with dbt), falling back to
  ``config.paths.duckdb_path``.
* Snowflake (built correctly but NOT run here -- no credentials): uses
  ``snowflake-connector-python`` + ``write_pandas``, schema ``RAW``, with
  credentials read from ``.env`` (``SNOWFLAKE_*``, see ``.env.example``).

Batch semantics (cutoff / loaded_at read from ``config.batches``):

* Batch 1 (cutoff 2018-01-01) -- AS-OF snapshot. Only orders purchased before
  the cutoff are kept. For each kept order, the three event timestamps that can
  occur after purchase (approved_at, delivered_carrier_date,
  delivered_customer_date) are blanked when they fall on/after the cutoff.
  ``order_estimated_delivery_date`` is deliberately NOT masked: it is known at
  purchase time (a promise, not an event), so in a true as-of snapshot it was
  already known at the cutoff (see AI_USE.md for this deliberate deviation from
  the spec's literal "blank any order timestamp" wording). ``order_status`` is
  re-derived ONLY for orders that actually had an event timestamp blanked; orders
  with nothing masked keep their true source status. Orders whose source status
  is terminal ('canceled' / 'unavailable') keep that status even when masked,
  because the cancellation time is unknown and inventing a rollback would be
  wrong. Items / payments are restricted to the kept orders; reviews to those
  created before the cutoff on kept orders (answer timestamp blanked if on/after
  the cutoff); leads to ``first_contact_date`` before the cutoff; closed deals to
  ``won_date`` before the cutoff. The four reference tables (customers, products,
  sellers, category translation) are loaded in FULL in both batches.
* Batch 2 (no cutoff) -- everything, with true values.

Code example note: an order purchased before the cutoff but handed to the
carrier before it and delivered after it becomes ``'shipped'`` as of the cutoff;
one only approved before the cutoff becomes ``'processing'``; a 'canceled' order
stays 'canceled'; an order with no masked timestamp keeps its source status.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import yaml

logger = logging.getLogger("load_raw")

# Project root = parent of the directory that holds this script (scripts/).
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"

RAW_SCHEMA = "raw"

# Metadata columns appended to every raw table, in landing order. The source
# columns always precede these four; _row_hash is never part of its own input.
META_SOURCE_SYSTEM = "_source_system"
META_SOURCE_FILE = "_source_file"
META_LOADED_AT = "_loaded_at"
META_ROW_HASH = "_row_hash"
METADATA_COLUMNS = [
    META_SOURCE_SYSTEM,
    META_SOURCE_FILE,
    META_LOADED_AT,
    META_ROW_HASH,
]

# Order EVENT timestamp columns that are blanked when they fall on/after the
# cutoff. order_estimated_delivery_date is intentionally excluded: it is known at
# purchase time (a promise, not an event), so a true as-of snapshot keeps it.
MASKABLE_ORDER_COLUMNS = [
    "order_approved_at",
    "order_delivered_carrier_date",
    "order_delivered_customer_date",
]

# Source statuses treated as "early stage" and preserved when, as of the cutoff,
# no timestamp has advanced the order past creation.
EARLY_STAGE_STATUSES = ["created", "invoiced"]

# Terminal statuses kept as-is even when a timestamp is masked: the cancellation
# time is unknown, so we never invent a rollback for these.
TERMINAL_STATUSES = ["canceled", "unavailable"]


@dataclass(frozen=True)
class SourceSpec:
    """Describes one source CSV and where it lands."""

    file: str
    table: str
    system: str


# Registry of every source file, its target raw table, and its source system.
# Order matters only for readable logging; orders precede their dependents.
SOURCE_SPECS: list[SourceSpec] = [
    SourceSpec("olist_customers_dataset.csv", "customers", "ecommerce"),
    SourceSpec("olist_orders_dataset.csv", "orders", "ecommerce"),
    SourceSpec("olist_order_items_dataset.csv", "order_items", "ecommerce"),
    SourceSpec("olist_order_payments_dataset.csv", "order_payments", "ecommerce"),
    SourceSpec("olist_order_reviews_dataset.csv", "order_reviews", "ecommerce"),
    SourceSpec("olist_products_dataset.csv", "products", "ecommerce"),
    SourceSpec("olist_sellers_dataset.csv", "sellers", "ecommerce"),
    SourceSpec(
        "product_category_name_translation.csv",
        "product_category_name_translation",
        "ecommerce",
    ),
    SourceSpec(
        "olist_marketing_qualified_leads_dataset.csv",
        "marketing_qualified_leads",
        "crm",
    ),
    SourceSpec("olist_closed_deals_dataset.csv", "closed_deals", "crm"),
]


# --------------------------------------------------------------------------- #
# Configuration / environment helpers
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


def resolve_raw_dir(config: dict) -> Path:
    """Return the absolute data/raw directory from config, relative to root."""
    raw_dir_value = config.get("paths", {}).get("data_raw_dir", "data/raw")
    raw_dir = Path(raw_dir_value)
    if not raw_dir.is_absolute():
        raw_dir = PROJECT_ROOT / raw_dir
    return raw_dir


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


def resolve_target(cli_target: str | None, config: dict) -> str:
    """Resolve the warehouse target: CLI flag > env WAREHOUSE_TARGET > config."""
    target = cli_target or os.environ.get("WAREHOUSE_TARGET") or config.get("target")
    if target is None:
        raise ValueError("No warehouse target set (CLI, WAREHOUSE_TARGET, or config).")
    target = target.lower()
    if target not in ("duckdb", "snowflake"):
        raise ValueError(f"Unsupported target: {target!r} (expected duckdb|snowflake).")
    return target


def batch_settings(config: dict, batch: int) -> tuple[pd.Timestamp | None, str]:
    """Return ``(cutoff, loaded_at)`` for the given batch number from config.

    ``cutoff`` is ``None`` when the batch has no cutoff (full load). ``loaded_at``
    is the raw string value, landed verbatim into ``_loaded_at``.
    """
    key = f"batch{batch}"
    batches = config.get("batches", {})
    if key not in batches:
        raise ValueError(f"Batch {batch} not configured under 'batches' in config.")
    settings = batches[key]
    loaded_at = settings.get("loaded_at")
    if not loaded_at:
        raise ValueError(f"Batch {batch} has no 'loaded_at' in config.")
    cutoff_value = settings.get("cutoff")
    cutoff = pd.Timestamp(cutoff_value) if cutoff_value else None
    return cutoff, str(loaded_at)


# --------------------------------------------------------------------------- #
# Reading / hashing / AS-OF transforms
# --------------------------------------------------------------------------- #
def read_source_csv(path: Path) -> pd.DataFrame:
    """Read a source CSV as strings, keeping empty cells empty and values intact.

    ``dtype=str`` + ``keep_default_na=False`` + ``na_filter=False`` ensures ids
    are not coerced to floats, nothing becomes NaN, and empty stays ``''``. The
    pandas C parser honours quoting, so fields with embedded newlines (the Olist
    reviews) parse as one row each.
    """
    if not path.exists():
        raise FileNotFoundError(f"Source CSV not found: {path}")
    return pd.read_csv(path, dtype=str, keep_default_na=False, na_filter=False)


def compute_row_hash(frame: pd.DataFrame, source_columns: list[str]) -> pd.Series:
    """Return a deterministic md5 hash per row over ``source_columns`` only.

    Each value is cast to ``str`` (NaN/None -> ``''``) and joined in
    source-column order with ``'||'`` before hashing, so identical content always
    hashes identically and the metadata columns never influence the hash.
    """
    joined = frame[source_columns].fillna("").astype(str).agg("||".join, axis=1)
    return joined.map(lambda text: hashlib.md5(text.encode("utf-8")).hexdigest())


def derive_order_status(orders: pd.DataFrame) -> pd.Series:
    """Derive ``order_status`` as of the cutoff from the surviving timestamps.

    Called after later-than-cutoff timestamps have been blanked, so a non-empty
    timestamp means that event happened before the cutoff. Precedence (later
    wins): delivered-to-customer -> 'delivered'; delivered-to-carrier ->
    'shipped'; approved -> 'processing'; otherwise the order has only been placed
    as of the cutoff, so the source status is kept if it is already an early
    stage ('created'/'invoiced') and defaults to 'created' otherwise.
    """

    def non_empty(column: str) -> pd.Series:
        return orders[column].astype(str).str.len() > 0

    status = pd.Series("created", index=orders.index)
    early = orders["order_status"].isin(EARLY_STAGE_STATUSES)
    status = status.mask(early, orders["order_status"])
    status = status.mask(non_empty("order_approved_at"), "processing")
    status = status.mask(non_empty("order_delivered_carrier_date"), "shipped")
    status = status.mask(non_empty("order_delivered_customer_date"), "delivered")
    return status


def apply_asof_orders(orders: pd.DataFrame, cutoff: pd.Timestamp) -> pd.DataFrame:
    """Return the AS-OF-cutoff view of orders.

    Keeps only orders purchased before the cutoff, blanks the event timestamps
    (``MASKABLE_ORDER_COLUMNS``) that fall on/after it, and re-derives
    ``order_status`` ONLY for orders that actually had a timestamp blanked. Orders
    with nothing masked keep their true source status (so we never inject a bogus
    status change into the versioned history). Terminal statuses ('canceled' /
    'unavailable') are preserved even when a timestamp is masked, because the
    cancellation time is unknown. ``order_estimated_delivery_date`` is not masked
    (known at purchase time).
    """
    purchased = pd.to_datetime(orders["order_purchase_timestamp"], errors="coerce")
    kept = orders[purchased < cutoff].copy()
    masked = pd.Series(False, index=kept.index)
    for column in MASKABLE_ORDER_COLUMNS:
        parsed = pd.to_datetime(kept[column], errors="coerce")
        # NaT >= cutoff is False, so blank/unparseable timestamps are untouched.
        after_cutoff = parsed >= cutoff
        kept.loc[after_cutoff, column] = ""
        masked = masked | after_cutoff
    # Re-derive status only where an event was rolled back, and never for terminal
    # statuses (cancellation time is unknown).
    rederive = masked & ~kept["order_status"].isin(TERMINAL_STATUSES)
    if rederive.any():
        derived = derive_order_status(kept)
        kept.loc[rederive, "order_status"] = derived[rederive]
    return kept


def apply_batch1_transforms(
    frames: dict[str, pd.DataFrame], cutoff: pd.Timestamp
) -> dict[str, pd.DataFrame]:
    """Apply the batch-1 AS-OF snapshot rules to the raw frames.

    Reference tables (customers, products, sellers, category translation) are
    deliberately loaded in full in both batches; only the transactional / CRM
    event tables are filtered to the cutoff.
    """
    result = dict(frames)

    orders = apply_asof_orders(frames["orders"], cutoff)
    result["orders"] = orders
    kept_order_ids = set(orders["order_id"])

    result["order_items"] = frames["order_items"][
        frames["order_items"]["order_id"].isin(kept_order_ids)
    ].copy()
    result["order_payments"] = frames["order_payments"][
        frames["order_payments"]["order_id"].isin(kept_order_ids)
    ].copy()

    reviews = frames["order_reviews"]
    creation = pd.to_datetime(reviews["review_creation_date"], errors="coerce")
    reviews = reviews[
        (creation < cutoff) & reviews["order_id"].isin(kept_order_ids)
    ].copy()
    answered = pd.to_datetime(reviews["review_answer_timestamp"], errors="coerce")
    reviews.loc[answered >= cutoff, "review_answer_timestamp"] = ""
    result["order_reviews"] = reviews

    leads = frames["marketing_qualified_leads"]
    first_contact = pd.to_datetime(leads["first_contact_date"], errors="coerce")
    result["marketing_qualified_leads"] = leads[first_contact < cutoff].copy()

    deals = frames["closed_deals"]
    won = pd.to_datetime(deals["won_date"], errors="coerce")
    result["closed_deals"] = deals[won < cutoff].copy()

    return result


def prepare_batch_frames(
    raw_dir: Path, cutoff: pd.Timestamp | None
) -> dict[str, pd.DataFrame]:
    """Read every source CSV and apply the batch rules (cutoff ``None`` = full)."""
    frames: dict[str, pd.DataFrame] = {}
    for spec in SOURCE_SPECS:
        frames[spec.table] = read_source_csv(raw_dir / spec.file)
    if cutoff is None:
        return frames
    return apply_batch1_transforms(frames, cutoff)


# --------------------------------------------------------------------------- #
# Warehouse target abstraction
# --------------------------------------------------------------------------- #
class WarehouseTarget(ABC):
    """Minimal interface the loader needs from a warehouse backend."""

    @abstractmethod
    def ensure_schema(self) -> None:
        """Create the raw schema if it does not already exist."""

    @abstractmethod
    def existing_hashes(self, table: str) -> set[str]:
        """Return the set of ``_row_hash`` values already present in the table."""

    @abstractmethod
    def append(self, table: str, frame: pd.DataFrame) -> None:
        """Append ``frame`` to the table, creating it on first write."""

    @abstractmethod
    def close(self) -> None:
        """Release any underlying connection."""


class DuckDBTarget(WarehouseTarget):
    """DuckDB implementation backed by a single local database file."""

    def __init__(self, db_path: Path) -> None:
        import duckdb

        db_path.parent.mkdir(parents=True, exist_ok=True)
        logger.info("Connecting to DuckDB at %s", db_path)
        self._con = duckdb.connect(str(db_path))

    def ensure_schema(self) -> None:
        self._con.execute(f"CREATE SCHEMA IF NOT EXISTS {RAW_SCHEMA}")

    def _table_exists(self, table: str) -> bool:
        query = (
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema = ? AND table_name = ?"
        )
        # fetchall() fully consumes the result; DuckDB refuses further work on a
        # connection that still has an unfinished result pending.
        return self._con.execute(query, [RAW_SCHEMA, table]).fetchall()[0][0] > 0

    def existing_hashes(self, table: str) -> set[str]:
        if not self._table_exists(table):
            return set()
        rows = self._con.execute(
            f'SELECT {META_ROW_HASH} FROM {RAW_SCHEMA}."{table}"'
        ).fetchall()
        return {row[0] for row in rows}

    def append(self, table: str, frame: pd.DataFrame) -> None:
        # Register the frame and let DuckDB read it directly (zero-copy insert).
        self._con.register("_incoming_frame", frame)
        try:
            if self._table_exists(table):
                # BY NAME matches columns by name, so column order can't misalign.
                self._con.execute(
                    f'INSERT INTO {RAW_SCHEMA}."{table}" BY NAME '
                    "SELECT * FROM _incoming_frame"
                )
            else:
                self._con.execute(
                    f'CREATE TABLE {RAW_SCHEMA}."{table}" AS '
                    "SELECT * FROM _incoming_frame"
                )
        finally:
            self._con.unregister("_incoming_frame")

    def close(self) -> None:
        self._con.close()


class SnowflakeTarget(WarehouseTarget):
    """Snowflake implementation (built for Step 10; NOT exercised here).

    Credentials are read from the environment (populated from ``.env``); see the
    ``SNOWFLAKE_*`` names in ``.env.example``. Rows are written with
    ``write_pandas``. This class is intentionally kept clean and separate; it is
    not run in this milestone because no credentials are available.
    """

    def __init__(self) -> None:
        import snowflake.connector

        required = ["SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER", "SNOWFLAKE_PASSWORD"]
        missing = [name for name in required if not os.environ.get(name)]
        if missing:
            raise ValueError(
                "Missing Snowflake credentials in environment: " + ", ".join(missing)
            )
        self._schema = os.environ.get("SNOWFLAKE_RAW_SCHEMA", "RAW")
        self._database = os.environ.get("SNOWFLAKE_DATABASE", "OLIST")
        logger.info("Connecting to Snowflake database %s schema %s", self._database, self._schema)
        self._con = snowflake.connector.connect(
            account=os.environ["SNOWFLAKE_ACCOUNT"],
            user=os.environ["SNOWFLAKE_USER"],
            password=os.environ["SNOWFLAKE_PASSWORD"],
            role=os.environ.get("SNOWFLAKE_ROLE"),
            warehouse=os.environ.get("SNOWFLAKE_WAREHOUSE"),
            database=self._database,
        )

    def ensure_schema(self) -> None:
        self._con.cursor().execute(f"CREATE SCHEMA IF NOT EXISTS {self._schema}")

    def existing_hashes(self, table: str) -> set[str]:
        from snowflake.connector.errors import ProgrammingError

        cursor = self._con.cursor()
        try:
            cursor.execute(f"SELECT {META_ROW_HASH} FROM {self._schema}.{table.upper()}")
            return {row[0] for row in cursor.fetchall()}
        except ProgrammingError:
            # Table does not exist yet.
            return set()

    def append(self, table: str, frame: pd.DataFrame) -> None:
        from snowflake.connector.pandas_tools import write_pandas

        write_pandas(
            self._con,
            frame,
            table_name=table.upper(),
            schema=self._schema,
            auto_create_table=True,
            quote_identifiers=False,
        )

    def close(self) -> None:
        self._con.close()


def make_target(target: str, config: dict) -> WarehouseTarget:
    """Instantiate the requested warehouse target."""
    if target == "duckdb":
        return DuckDBTarget(resolve_duckdb_path(config))
    if target == "snowflake":
        return SnowflakeTarget()
    raise ValueError(f"Unsupported target: {target!r}")


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #
def add_metadata_columns(
    frame: pd.DataFrame,
    source_columns: list[str],
    source_system: str,
    source_file: str,
    loaded_at: str,
) -> pd.DataFrame:
    """Return ``frame`` with the four metadata columns appended in landing order.

    The row hash is computed over the source columns only, then the frame is
    re-ordered so source columns precede the metadata columns.
    """
    out = frame.copy()
    row_hash = compute_row_hash(out, source_columns)
    out[META_SOURCE_SYSTEM] = source_system
    out[META_SOURCE_FILE] = source_file
    out[META_LOADED_AT] = loaded_at
    out[META_ROW_HASH] = row_hash
    return out[source_columns + METADATA_COLUMNS]


def load_frame(
    target: WarehouseTarget,
    spec: SourceSpec,
    frame: pd.DataFrame,
    loaded_at: str,
) -> int:
    """Hash, dedup against existing versions, and append one table. Returns inserts.

    Only rows whose ``_row_hash`` is not already present in the table are
    appended (append-only, versioned). Re-running a batch therefore inserts
    nothing. No within-batch dedup is done, so each CSV lands unchanged.
    """
    source_columns = list(frame.columns)
    enriched = add_metadata_columns(
        frame, source_columns, spec.system, spec.file, loaded_at
    )
    existing = target.existing_hashes(spec.table)
    if existing:
        new_rows = enriched[~enriched[META_ROW_HASH].isin(existing)]
    else:
        new_rows = enriched
    inserted = len(new_rows)
    if inserted:
        target.append(spec.table, new_rows)
    logger.info(
        "raw.%s: %d incoming, %d new version(s) inserted, %d skipped (already present)",
        spec.table,
        len(enriched),
        inserted,
        len(enriched) - inserted,
    )
    return inserted


def run_load(
    target: WarehouseTarget,
    raw_dir: Path,
    cutoff: pd.Timestamp | None,
    loaded_at: str,
) -> dict[str, int]:
    """Prepare and load every source table for one batch. Returns inserts/table."""
    target.ensure_schema()
    frames = prepare_batch_frames(raw_dir, cutoff)
    inserted: dict[str, int] = {}
    for spec in SOURCE_SPECS:
        inserted[spec.table] = load_frame(target, spec, frames[spec.table], loaded_at)
    return inserted


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Land the raw Olist CSVs into versioned raw tables."
    )
    parser.add_argument(
        "--batch",
        type=int,
        required=True,
        choices=[1, 2],
        help="Which simulated load to run (1 = AS-OF snapshot, 2 = full).",
    )
    parser.add_argument(
        "--target",
        default=None,
        choices=["duckdb", "snowflake"],
        help="Warehouse target (default: WAREHOUSE_TARGET env, else config.target).",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=f"Path to the YAML config (default: {DEFAULT_CONFIG_PATH}).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: INFO).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run one batch load. Returns a process exit code."""
    args = parse_args(argv)
    configure_logging(args.log_level)

    config = load_config(args.config)
    raw_dir = resolve_raw_dir(config)
    target_name = resolve_target(args.target, config)
    cutoff, loaded_at = batch_settings(config, args.batch)

    logger.info(
        "Batch %d | target=%s | cutoff=%s | loaded_at=%s | raw_dir=%s",
        args.batch,
        target_name,
        cutoff.date() if cutoff is not None else "none (full load)",
        loaded_at,
        raw_dir,
    )

    target = make_target(target_name, config)
    try:
        inserted = run_load(target, raw_dir, cutoff, loaded_at)
    finally:
        target.close()

    total = sum(inserted.values())
    logger.info("Batch %d complete: %d new row version(s) inserted total.", args.batch, total)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
