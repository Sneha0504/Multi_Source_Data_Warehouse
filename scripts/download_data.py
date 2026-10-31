#!/usr/bin/env python3
"""Acquire and catalog the raw Olist source data (Milestone 2: data acquisition).

This script is the single entry point for Step 3 of the project ("Getting the
data"). It pulls two public Kaggle datasets into ``data/raw/`` and records a
reproducible catalog (sha256 checksum, row count, date ranges) in
``docs/data_sources.md``.

Behaviour (decided deliberately so the Makefile chain stays usable):

1. If every expected raw CSV is already present in ``data/raw/``, downloading is
   skipped and the script proceeds straight to cataloging. Raw files are never
   modified after download.
2. Otherwise, if a Kaggle API token is available (``~/.kaggle/kaggle.json``) and
   authentication succeeds, both datasets are downloaded and unzipped in place.
3. Otherwise (no token, auth failure, or still-missing files) the script prints
   the EXACT manual download steps for both datasets and continues to catalog
   whatever files do exist.

Exit code: the script exits ``0`` even when data is missing, so that a
``make`` chain is not aborted mid-way purely because the operator has not yet
downloaded the data. Missing data is reported loudly via WARNING-level logs and
written as "not yet downloaded" in the catalog, so the state is never hidden.
Honesty rule: checksums / counts are only ever computed for files that truly
exist on disk; nothing is fabricated for absent files.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import logging
from pathlib import Path

import pandas as pd
import yaml

logger = logging.getLogger("download_data")

# Project root = parent of the directory that holds this script (scripts/).
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"

# Expected raw CSV file names per dataset. geolocation is intentionally omitted
# (optional per the spec). These are the files downstream steps depend on.
EXPECTED_FILES: dict[str, list[str]] = {
    "ecommerce": [
        "olist_customers_dataset.csv",
        "olist_orders_dataset.csv",
        "olist_order_items_dataset.csv",
        "olist_order_payments_dataset.csv",
        "olist_order_reviews_dataset.csv",
        "olist_products_dataset.csv",
        "olist_sellers_dataset.csv",
        "product_category_name_translation.csv",
    ],
    "marketing_funnel": [
        "olist_marketing_qualified_leads_dataset.csv",
        "olist_closed_deals_dataset.csv",
    ],
}

# Files whose date range is worth recording, with the relevant date column.
DATE_COLUMNS: dict[str, str] = {
    "olist_orders_dataset.csv": "order_purchase_timestamp",
    "olist_marketing_qualified_leads_dataset.csv": "first_contact_date",
    "olist_closed_deals_dataset.csv": "won_date",
}

# Static dataset metadata used to document both acquisition routes.
DATASET_METADATA: dict[str, dict[str, str]] = {
    "ecommerce": {
        "title": "Brazilian E-Commerce Public Dataset by Olist",
        "url": "https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce",
    },
    "marketing_funnel": {
        "title": "Marketing Funnel by Olist",
        "url": "https://www.kaggle.com/datasets/olistbr/marketing-funnel-olist",
    },
}
PUBLISHER = "Olist"
LICENSE = "CC BY-NC-SA 4.0"

# Path to the Kaggle API token on disk, per the Kaggle client convention.
KAGGLE_TOKEN_PATH = Path.home() / ".kaggle" / "kaggle.json"


def configure_logging(level: str = "INFO") -> None:
    """Configure root logging with a simple, consistent format."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def load_config(config_path: Path) -> dict:
    """Load and return the YAML config as a dict.

    Args:
        config_path: Path to ``config/config.yaml``.

    Returns:
        Parsed configuration mapping.

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


def dataset_slugs(config: dict) -> dict[str, str]:
    """Return the configured Kaggle dataset slugs keyed by dataset name."""
    return dict(config.get("kaggle_datasets", {}))


def all_expected_file_names() -> list[str]:
    """Return a flat list of all required raw CSV file names."""
    return [name for names in EXPECTED_FILES.values() for name in names]


def find_missing_files(raw_dir: Path) -> list[str]:
    """Return the list of expected file names not present in ``raw_dir``."""
    return [name for name in all_expected_file_names() if not (raw_dir / name).exists()]


def kaggle_token_available() -> bool:
    """Return True if a Kaggle API token file is present on disk."""
    return KAGGLE_TOKEN_PATH.exists()


def download_datasets(slugs: dict[str, str], raw_dir: Path) -> bool:
    """Authenticate with Kaggle and download + unzip both datasets.

    The ``kaggle`` package is imported lazily because importing it eagerly emits
    noisy auth output and may call ``sys.exit`` when no token is configured.

    Args:
        slugs: Mapping of dataset key -> Kaggle dataset slug (owner/name).
        raw_dir: Directory to download and unzip the CSVs into.

    Returns:
        True if both datasets downloaded without error, False otherwise.
    """
    try:
        from kaggle.api.kaggle_api_extended import KaggleApi
    except Exception as exc:  # pragma: no cover - depends on environment
        logger.warning("Could not import the kaggle package: %s", exc)
        return False

    api = KaggleApi()
    try:
        # authenticate() raises SystemExit (not a normal exception) when the
        # token is missing/invalid, so both must be caught here.
        api.authenticate()
    except SystemExit:
        logger.warning("Kaggle authentication failed: no valid token found.")
        return False
    except Exception as exc:
        logger.warning("Kaggle authentication failed: %s", exc)
        return False

    raw_dir.mkdir(parents=True, exist_ok=True)
    for name, slug in slugs.items():
        logger.info("Downloading dataset '%s' (%s) into %s", name, slug, raw_dir)
        try:
            api.dataset_download_files(slug, path=str(raw_dir), unzip=True, quiet=False)
        except Exception as exc:
            logger.warning("Failed to download '%s' (%s): %s", name, slug, exc)
            return False
    logger.info("Kaggle download complete.")
    return True


def print_manual_instructions(slugs: dict[str, str], raw_dir: Path) -> None:
    """Log the exact manual download steps for both datasets."""
    logger.warning("Raw data is MISSING and could not be downloaded automatically.")
    logger.warning("Follow these manual steps to obtain the data:")
    logger.warning("")
    for key in EXPECTED_FILES:
        meta = DATASET_METADATA.get(key, {})
        slug = slugs.get(key, "")
        title = meta.get("title", key)
        url = meta.get("url", f"https://www.kaggle.com/datasets/{slug}")
        logger.warning("  Dataset: %s (%s)", title, slug)
        logger.warning("    1. Open %s", url)
        logger.warning("    2. Click 'Download' (top-right of the page).")
        logger.warning("    3. Unzip the CSVs into: %s", raw_dir)
        logger.warning("")
    logger.warning(
        "After placing the CSVs, re-run: python scripts/download_data.py "
        "(it will skip the download and build the catalog)."
    )


def sha256_of_file(path: Path, chunk_size: int = 1 << 20) -> str:
    """Return the hex sha256 checksum of a file, read in streaming chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def count_data_rows(path: Path) -> int:
    """Return the number of data rows (excluding the header) in a CSV.

    Uses pandas rather than a naive line count so that quoted fields containing
    embedded newlines (present in the Olist reviews data) are counted correctly.
    """
    frame = pd.read_csv(path, dtype=str, low_memory=False)
    return len(frame)


def compute_date_range(path: Path, column: str) -> tuple[str, str] | None:
    """Return (min, max) of a date/datetime column as ISO strings, if present.

    Returns None if the column is absent or holds no parseable dates.
    """
    header = pd.read_csv(path, nrows=0)
    if column not in header.columns:
        return None
    series = pd.read_csv(path, usecols=[column])[column]
    parsed = pd.to_datetime(series, errors="coerce")
    parsed = parsed.dropna()
    if parsed.empty:
        return None
    return (str(parsed.min()), str(parsed.max()))


def catalog_file(path: Path) -> dict[str, object]:
    """Compute checksum, row count, and optional date range for one raw CSV."""
    entry: dict[str, object] = {
        "present": True,
        "sha256": sha256_of_file(path),
        "row_count": count_data_rows(path),
        "date_range": None,
    }
    date_column = DATE_COLUMNS.get(path.name)
    if date_column:
        date_range = compute_date_range(path, date_column)
        if date_range is not None:
            entry["date_range"] = f"{date_range[0]} -> {date_range[1]}"
    return entry


def build_catalog(raw_dir: Path) -> dict[str, dict[str, dict[str, object]]]:
    """Catalog every expected file per dataset, marking absent ones honestly.

    Returns:
        Mapping dataset_key -> {file_name -> entry}. Entries for present files
        carry real checksum/row-count/date-range values; absent files carry
        ``{"present": False}`` and are rendered as "not yet downloaded".
    """
    catalog: dict[str, dict[str, dict[str, object]]] = {}
    for key, names in EXPECTED_FILES.items():
        catalog[key] = {}
        for name in names:
            path = raw_dir / name
            if path.exists():
                logger.info("Cataloging %s", name)
                catalog[key][name] = catalog_file(path)
            else:
                catalog[key][name] = {"present": False}
    return catalog


def _render_table_rows(files: dict[str, dict[str, object]]) -> list[str]:
    """Render markdown table rows for one dataset's files."""
    rows: list[str] = []
    for name, entry in files.items():
        if entry.get("present"):
            row_count = entry.get("row_count")
            sha = entry.get("sha256")
            date_range = entry.get("date_range") or "n/a"
            rows.append(f"| `{name}` | {row_count:,} | `{sha}` | {date_range} |")
        else:
            rows.append(f"| `{name}` | not yet downloaded | not yet downloaded | not yet downloaded |")
    return rows


def write_data_sources_doc(
    doc_path: Path,
    slugs: dict[str, str],
    catalog: dict[str, dict[str, dict[str, object]]],
) -> None:
    """Regenerate ``docs/data_sources.md`` with metadata and the live catalog.

    The script owns this file in full: both datasets are documented (title, URL,
    publisher, license, date accessed, file list) and the per-file table is
    filled from the catalog computed this run.
    """
    accessed = dt.date.today().isoformat()
    lines: list[str] = []
    lines.append("# Data Sources")
    lines.append("")
    lines.append(
        "This file is generated and refreshed by `scripts/download_data.py`. "
        "Row counts, checksums, and date ranges below are computed from the "
        "actual files present in `data/raw/` at the last run; files that have "
        "not been downloaded yet are marked accordingly."
    )
    lines.append("")
    lines.append(f"- **Publisher:** {PUBLISHER}")
    lines.append(f"- **License:** {LICENSE}")
    lines.append(f"- **Date accessed / last cataloged:** {accessed}")
    lines.append("")

    lines.append("## Acquisition routes")
    lines.append("")
    lines.append("**Route A — Kaggle API (automated):**")
    lines.append("")
    lines.append("1. Install the Kaggle client (included in `requirements.txt`).")
    lines.append(
        "2. Create an API token at <https://www.kaggle.com/settings/api> "
        "(\"Create New Token\") and save it to `~/.kaggle/kaggle.json` "
        "(`chmod 600 ~/.kaggle/kaggle.json`)."
    )
    lines.append("3. Run `python scripts/download_data.py` (or `make download`).")
    lines.append("")
    lines.append("**Route B — Manual download:**")
    lines.append("")
    for key in EXPECTED_FILES:
        meta = DATASET_METADATA.get(key, {})
        slug = slugs.get(key, "")
        lines.append(f"- {meta.get('title', key)} ({slug}):")
        lines.append(f"  1. Open <{meta.get('url')}>.")
        lines.append("  2. Click **Download** (top-right of the page).")
        lines.append("  3. Unzip the CSVs into `data/raw/`.")
    lines.append("")
    lines.append(
        "Raw files are never modified after download. The geolocation dataset "
        "is optional and intentionally not required."
    )
    lines.append("")

    for key, names in EXPECTED_FILES.items():
        meta = DATASET_METADATA.get(key, {})
        slug = slugs.get(key, "")
        lines.append(f"## {meta.get('title', key)}")
        lines.append("")
        lines.append(f"- **Kaggle dataset:** `{slug}`")
        lines.append(f"- **URL:** <{meta.get('url')}>")
        lines.append(f"- **Publisher:** {PUBLISHER}")
        lines.append(f"- **License:** {LICENSE}")
        lines.append("")
        lines.append("Expected files:")
        lines.append("")
        for name in names:
            lines.append(f"- `{name}`")
        lines.append("")
        lines.append("| File | Row count | sha256 | Date range |")
        lines.append("| --- | --- | --- | --- |")
        lines.extend(_render_table_rows(catalog.get(key, {})))
        lines.append("")

    doc_path.parent.mkdir(parents=True, exist_ok=True)
    doc_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("Wrote data source catalog to %s", doc_path)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Download and catalog the raw Olist source data."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=f"Path to the YAML config (default: {DEFAULT_CONFIG_PATH}).",
    )
    parser.add_argument(
        "--raw-dir",
        type=Path,
        default=None,
        help="Override the raw data directory (default: read from config).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: INFO).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the download + catalog workflow. Returns a process exit code."""
    args = parse_args(argv)
    configure_logging(args.log_level)

    config = load_config(args.config)
    raw_dir = args.raw_dir if args.raw_dir is not None else resolve_raw_dir(config)
    slugs = dataset_slugs(config)
    raw_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Raw data directory: %s", raw_dir)
    missing = find_missing_files(raw_dir)

    if not missing:
        logger.info("All expected raw files are present; skipping download.")
    else:
        logger.info("%d expected file(s) missing: %s", len(missing), ", ".join(missing))
        if kaggle_token_available():
            logger.info("Kaggle token found at %s; attempting download.", KAGGLE_TOKEN_PATH)
            downloaded = download_datasets(slugs, raw_dir)
            if downloaded:
                missing = find_missing_files(raw_dir)
            if missing:
                logger.warning("Some files still missing after download attempt.")
                print_manual_instructions(slugs, raw_dir)
        else:
            logger.warning("No Kaggle token at %s.", KAGGLE_TOKEN_PATH)
            print_manual_instructions(slugs, raw_dir)

    # Catalog step: always runs, over whatever files exist (honest about gaps).
    catalog = build_catalog(raw_dir)
    doc_path = PROJECT_ROOT / "docs" / "data_sources.md"
    write_data_sources_doc(doc_path, slugs, catalog)

    remaining = find_missing_files(raw_dir)
    if remaining:
        logger.warning(
            "DATA INCOMPLETE: %d expected file(s) still missing. See %s for the "
            "catalog and %s for manual steps.",
            len(remaining),
            doc_path,
            DATASET_METADATA["ecommerce"]["url"],
        )
    else:
        logger.info("All expected raw files present and cataloged.")

    # Exit 0 even when data is missing, so a Makefile chain is not aborted purely
    # because the operator has not downloaded data yet. The missing state is
    # reported above and recorded in the catalog.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
