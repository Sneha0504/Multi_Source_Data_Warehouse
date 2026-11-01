#!/usr/bin/env python3
"""Rebuild-identical check: a full refresh must reproduce the warehouse exactly.

This script implements the "rebuild check" half of Step 7 ("Tests and checks").
It proves that the warehouse built incrementally (batch 1 then batch 2) is
byte-for-byte reproducible from the raw landing zone with a single from-scratch
``dbt build --full-refresh`` -- i.e. that no model is non-deterministic.

Procedure:

1. Resolve the source DuckDB warehouse (env ``DUCKDB_PATH`` first, then
   ``config.paths.duckdb_path``) -- the file already built by batch 1 + batch 2.
2. Copy that file to a temporary location. The copy carries the same raw tables,
   so a full refresh rebuilds staging / integration / marts from identical input.
3. Run ``dbt build --full-refresh --target duckdb`` against the COPY (via the
   ``DUCKDB_PATH`` env var), writing dbt artifacts to a throwaway target dir so
   the real ``target/`` is untouched. The REAL warehouse is never full-refreshed.
4. Enumerate every model relation (tables AND views) in ``main_staging``,
   ``main_integration`` and ``main_marts`` from the original warehouse.
5. ATTACH both files read-only in one connection and, for each relation, compare
   FULL contents order-independently with ``EXCEPT ALL`` in both directions over a
   stable column list. A relation is identical when both directions return zero
   rows (this also catches duplicate-row count drift, not just set membership).
6. Print a per-model table and a final verdict. Exit 0 only if every relation is
   identical (and the rebuild itself succeeded); non-zero otherwise.

The temporary copy and its dbt artifacts are always cleaned up.

If a model ever differs, the fix belongs in the MODEL (a non-deterministic
``ORDER BY`` without a tiebreak, an incremental model that disagrees with a
from-scratch build, etc.), never in weakening this check.
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import duckdb
import yaml

logger = logging.getLogger("compare_full_refresh")

# Project root = parent of the directory that holds this script (scripts/).
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"
DBT_PROJECT_DIR = PROJECT_ROOT / "olist_dbt"

# The analytics schemas dbt materializes (profile schema 'main' + layer suffix).
# Only these are compared; the shared raw landing zone is identical by construction
# (it is copied, not rebuilt).
COMPARED_SCHEMAS = ["main_staging", "main_integration", "main_marts"]


@dataclass(frozen=True)
class Relation:
    """One model relation to compare (schema-qualified)."""

    schema: str
    name: str
    kind: str  # 'table' or 'view'

    @property
    def fqn(self) -> str:
        """Schema-qualified, quoted name (without a catalog prefix)."""
        return f'"{self.schema}"."{self.name}"'


@dataclass
class RelationDiff:
    """Comparison result for one relation."""

    relation: Relation
    orig_rows: int
    copy_rows: int
    diff_count: int
    error: str | None = None

    @property
    def identical(self) -> bool:
        return self.error is None and self.diff_count == 0


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


def resolve_duckdb_path(config: dict) -> Path:
    """Return the source DuckDB file path, reading env ``DUCKDB_PATH`` first.

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


def resolve_dbt_executable() -> str:
    """Return the dbt executable, preferring the one in the running venv."""
    candidate = Path(sys.executable).parent / "dbt"
    if candidate.exists():
        return str(candidate)
    return "dbt"


# --------------------------------------------------------------------------- #
# Rebuild (dbt full refresh against the copy)
# --------------------------------------------------------------------------- #
def run_full_refresh(copy_path: Path, target_path: Path) -> None:
    """Run ``dbt build --full-refresh`` against the copied warehouse.

    The real warehouse is never touched: dbt is pointed at ``copy_path`` via the
    ``DUCKDB_PATH`` env var and writes its artifacts to a throwaway ``target_path``
    so the project's real ``target/`` directory is left intact. The surrounding
    environment (``DBT_PROFILES_DIR``, ``SSL_CERT_FILE``, ...) is inherited.

    Raises:
        RuntimeError: If the dbt build exits non-zero.
    """
    env = os.environ.copy()
    env["DUCKDB_PATH"] = str(copy_path)
    env["DBT_TARGET_PATH"] = str(target_path)
    # dbt's experimental parser may fetch over TLS; make sure a CA bundle is set
    # on framework Python even when the caller did not export one.
    if "SSL_CERT_FILE" not in env:
        try:
            import certifi

            env["SSL_CERT_FILE"] = certifi.where()
        except ImportError:
            pass

    cmd = [
        resolve_dbt_executable(),
        "build",
        "--full-refresh",
        "--target",
        "duckdb",
        "--target-path",
        str(target_path),
    ]
    logger.info("Running full refresh against the copy: %s", " ".join(cmd))
    result = subprocess.run(
        cmd,
        cwd=str(DBT_PROJECT_DIR),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        logger.error("dbt build failed (exit %d).", result.returncode)
        logger.error("stdout:\n%s", result.stdout)
        logger.error("stderr:\n%s", result.stderr)
        raise RuntimeError(f"dbt build --full-refresh failed (exit {result.returncode}).")
    logger.info("Full refresh completed successfully.")


# --------------------------------------------------------------------------- #
# Enumeration + comparison
# --------------------------------------------------------------------------- #
def enumerate_relations(con: duckdb.DuckDBPyConnection, catalog: str) -> list[Relation]:
    """Return every table/view in the compared schemas of ``catalog``, sorted.

    Uses DuckDB's ``duckdb_tables()`` / ``duckdb_views()`` system functions, which
    expose a ``database_name`` column and so can be filtered to one attached
    catalog (``information_schema`` cannot be catalog-qualified in DuckDB).
    """
    placeholders = ", ".join("?" for _ in COMPARED_SCHEMAS)
    table_rows = con.execute(
        f"""
        select schema_name, table_name
        from duckdb_tables()
        where database_name = ? and schema_name in ({placeholders})
        """,
        [catalog, *COMPARED_SCHEMAS],
    ).fetchall()
    view_rows = con.execute(
        f"""
        select schema_name, view_name
        from duckdb_views()
        where database_name = ? and schema_name in ({placeholders})
        """,
        [catalog, *COMPARED_SCHEMAS],
    ).fetchall()
    relations: list[Relation] = [
        Relation(schema=s, name=n, kind="table") for s, n in table_rows
    ] + [Relation(schema=s, name=n, kind="view") for s, n in view_rows]
    return sorted(relations, key=lambda r: (r.schema, r.name))


def ordered_columns(
    con: duckdb.DuckDBPyConnection, catalog: str, relation: Relation
) -> list[str]:
    """Return the relation's column names ordered by position within the relation."""
    rows = con.execute(
        """
        select column_name
        from duckdb_columns()
        where database_name = ? and schema_name = ? and table_name = ?
        order by column_index
        """,
        [catalog, relation.schema, relation.name],
    ).fetchall()
    return [r[0] for r in rows]


def compare_relation(
    con: duckdb.DuckDBPyConnection,
    relation: Relation,
    src_catalog: str,
    rebuilt_catalog: str,
) -> RelationDiff:
    """Compare one relation's full contents between the two attached catalogs.

    Uses ``EXCEPT ALL`` in both directions over a stable, ordinal-ordered column
    list, so the comparison is order-independent but still sensitive to duplicate
    row counts. ``diff_count`` is the total number of non-matching rows across both
    directions (0 means identical).
    """
    src_rel = f"{src_catalog}.{relation.fqn}"
    rebuilt_rel = f"{rebuilt_catalog}.{relation.fqn}"
    try:
        orig_rows = con.execute(f"select count(*) from {src_rel}").fetchone()[0]
        copy_rows = con.execute(f"select count(*) from {rebuilt_rel}").fetchone()[0]

        columns = ordered_columns(con, src_catalog, relation)
        col_list = ", ".join(f'"{c}"' for c in columns)

        missing_in_copy = con.execute(
            f"""
            select count(*) from (
                select {col_list} from {src_rel}
                except all
                select {col_list} from {rebuilt_rel}
            )
            """
        ).fetchone()[0]
        extra_in_copy = con.execute(
            f"""
            select count(*) from (
                select {col_list} from {rebuilt_rel}
                except all
                select {col_list} from {src_rel}
            )
            """
        ).fetchone()[0]
        diff_count = int(missing_in_copy) + int(extra_in_copy)
        return RelationDiff(relation, int(orig_rows), int(copy_rows), diff_count)
    except duckdb.Error as exc:  # pragma: no cover - defensive
        return RelationDiff(relation, -1, -1, -1, error=str(exc))


def compare_warehouses(src_path: Path, copy_path: Path) -> list[RelationDiff]:
    """ATTACH both warehouses read-only and compare every model relation."""
    con = duckdb.connect(":memory:")
    try:
        con.execute(f"ATTACH '{src_path}' AS src (READ_ONLY)")
        con.execute(f"ATTACH '{copy_path}' AS rebuilt (READ_ONLY)")
        relations = enumerate_relations(con, "src")
        logger.info("Comparing %d model relation(s) across %s.", len(relations), COMPARED_SCHEMAS)
        return [compare_relation(con, rel, "src", "rebuilt") for rel in relations]
    finally:
        con.close()


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def report(diffs: list[RelationDiff]) -> bool:
    """Print the per-model table + final verdict. Returns True if all identical."""
    name_width = max((len(f"{d.relation.schema}.{d.relation.name}") for d in diffs), default=20)
    name_width = max(name_width, len("model"))

    header = f"{'model':<{name_width}}  {'kind':<5}  {'orig_rows':>10}  {'copy_rows':>10}  {'diff':>8}  status"
    print(header)
    print("-" * len(header))
    for d in sorted(diffs, key=lambda x: (x.relation.schema, x.relation.name)):
        full = f"{d.relation.schema}.{d.relation.name}"
        if d.error is not None:
            status = f"ERROR: {d.error}"
            print(f"{full:<{name_width}}  {d.relation.kind:<5}  {'n/a':>10}  {'n/a':>10}  {'n/a':>8}  {status}")
        else:
            status = "identical" if d.identical else "DIFFERENT"
            print(
                f"{full:<{name_width}}  {d.relation.kind:<5}  {d.orig_rows:>10}  "
                f"{d.copy_rows:>10}  {d.diff_count:>8}  {status}"
            )

    offending = [d for d in diffs if not d.identical]
    print()
    if not offending:
        print("REBUILD CHECK: IDENTICAL")
        return True
    names = ", ".join(f"{d.relation.schema}.{d.relation.name}" for d in offending)
    print(f"REBUILD CHECK: DIFFERENCES FOUND ({len(offending)}): {names}")
    return False


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Verify a full-refresh rebuild reproduces the warehouse exactly."
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
    """Run the rebuild-identical check. Returns a process exit code."""
    args = parse_args(argv)
    configure_logging(args.log_level)

    config = load_config(args.config)
    src_path = resolve_duckdb_path(config).resolve()
    if not src_path.exists():
        logger.error("Source warehouse not found: %s", src_path)
        logger.error("Build it first (load-batch1, build, load-batch2) before the rebuild check.")
        return 2
    logger.info("Source warehouse: %s", src_path)

    # Temp copy + throwaway dbt target dir, both always cleaned up.
    tmp_dir = Path(tempfile.mkdtemp(prefix="rebuild_check_"))
    copy_path = tmp_dir / "rebuild_copy.duckdb"
    target_path = tmp_dir / "dbt_target"
    try:
        logger.info("Copying warehouse to %s", copy_path)
        shutil.copy2(src_path, copy_path)

        run_full_refresh(copy_path, target_path)

        diffs = compare_warehouses(src_path, copy_path)
        all_identical = report(diffs)
        return 0 if all_identical else 1
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        logger.info("Cleaned up temporary copy and dbt artifacts in %s", tmp_dir)


if __name__ == "__main__":
    raise SystemExit(main())
