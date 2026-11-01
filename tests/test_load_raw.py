"""Tests for the raw ingestion / incremental loader (``scripts/load_raw.py``).

These use tiny synthetic CSV fixtures in a temp directory and a temp DuckDB file
(via the ``tmp_path`` fixture). They never touch ``data/raw`` or the real
warehouse. Coverage: idempotent re-load, batch-1 AS-OF masking + exclusion, and
deterministic row hashing (content-sensitive, metadata-insensitive).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import duckdb
import pandas as pd
import pytest

# Import scripts/load_raw.py by path so the tests run without packaging. The
# module is registered in sys.modules before execution so that @dataclass can
# resolve its own module during class creation.
_MODULE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "load_raw.py"
_spec = importlib.util.spec_from_file_location("load_raw", _MODULE_PATH)
load_raw = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules["load_raw"] = load_raw
_spec.loader.exec_module(load_raw)


# --------------------------------------------------------------------------- #
# Synthetic fixtures
# --------------------------------------------------------------------------- #
# The full set of source files must exist for prepare_batch_frames to read them.
# Most are trivial; orders/items/payments/reviews/leads/deals carry the rows the
# assertions care about. The cutoff used throughout is 2018-01-01.

# o1: fully delivered before cutoff -> unchanged across batches, status delivered.
# o2: purchased before cutoff, handed to carrier before it, delivered AFTER it
#     -> kept in batch 1, delivery date blanked, status rolled back to 'shipped'.
#     Its estimated delivery date (>= cutoff) is NOT masked (known at purchase).
# o3: purchased before cutoff, only approved before it -> status 'processing'.
# o4: purchased AFTER cutoff -> excluded from batch 1, present in batch 2.
# o5: purchased before cutoff, CANCELED, nothing masked -> stays 'canceled', and
#     produces NO new version in batch 2 (its true row is identical).
# o6: purchased before cutoff, INVOICED with an approved_at before the cutoff,
#     nothing masked -> must KEEP its source status 'invoiced' (the earlier bug
#     would have rewritten it to 'processing').
# o7: purchased before cutoff, CANCELED, carrier handoff AFTER the cutoff (masked)
#     -> status stays 'canceled' (cancellation time unknown); true row differs, so
#     it gains a new version in batch 2.
_ORDERS_CSV = (
    "order_id,customer_id,order_status,order_purchase_timestamp,order_approved_at,"
    "order_delivered_carrier_date,order_delivered_customer_date,"
    "order_estimated_delivery_date\n"
    "o1,c1,delivered,2017-11-01 10:00:00,2017-11-01 11:00:00,2017-11-02 10:00:00,"
    "2017-11-05 10:00:00,2017-11-10 00:00:00\n"
    "o2,c2,delivered,2017-12-20 10:00:00,2017-12-20 11:00:00,2017-12-28 10:00:00,"
    "2018-01-05 10:00:00,2018-01-08 00:00:00\n"
    "o3,c3,delivered,2017-12-30 10:00:00,2017-12-30 23:00:00,2018-01-03 10:00:00,"
    "2018-01-06 10:00:00,2018-01-09 00:00:00\n"
    "o4,c4,delivered,2018-03-01 10:00:00,2018-03-01 11:00:00,2018-03-02 10:00:00,"
    "2018-03-05 10:00:00,2018-03-10 00:00:00\n"
    "o5,c5,canceled,2017-12-05 10:00:00,,,,2017-12-15 00:00:00\n"
    "o6,c6,invoiced,2017-12-10 10:00:00,2017-12-11 10:00:00,,,2017-12-20 00:00:00\n"
    "o7,c7,canceled,2017-12-22 10:00:00,2017-12-22 12:00:00,2018-01-04 10:00:00,,"
    "2018-01-09 00:00:00\n"
)

_ORDER_ITEMS_CSV = (
    "order_id,order_item_id,product_id,seller_id,shipping_limit_date,price,"
    "freight_value\n"
    "o1,1,p1,s1,2017-11-03 10:00:00,10.00,1.00\n"
    "o2,1,p1,s1,2017-12-27 10:00:00,20.00,2.00\n"
    "o3,1,p2,s2,2018-01-02 10:00:00,30.00,3.00\n"
    "o4,1,p2,s2,2018-03-01 10:00:00,40.00,4.00\n"
)

_ORDER_PAYMENTS_CSV = (
    "order_id,payment_sequential,payment_type,payment_installments,payment_value\n"
    "o1,1,credit_card,1,11.00\n"
    "o2,1,credit_card,1,22.00\n"
    "o3,1,boleto,1,33.00\n"
    "o4,1,credit_card,1,44.00\n"
)

# r1: created and answered before cutoff -> unchanged.
# r2: created before cutoff but answered AFTER it -> kept, answer blanked.
# r3: created AFTER cutoff -> excluded from batch 1.
_ORDER_REVIEWS_CSV = (
    "review_id,order_id,review_score,review_comment_title,review_comment_message,"
    "review_creation_date,review_answer_timestamp\n"
    "r1,o1,5,,good,2017-11-06 00:00:00,2017-11-07 10:00:00\n"
    "r2,o2,4,,ok,2017-12-29 00:00:00,2018-01-06 10:00:00\n"
    "r3,o3,3,,meh,2018-01-07 00:00:00,2018-01-10 10:00:00\n"
)

_CUSTOMERS_CSV = (
    "customer_id,customer_unique_id,customer_zip_code_prefix,customer_city,"
    "customer_state\n"
    "c1,u1,01000,sao paulo,SP\n"
    "c2,u2,02000,rio,RJ\n"
    "c3,u3,03000,belo,MG\n"
    "c4,u4,04000,curitiba,PR\n"
)

_PRODUCTS_CSV = (
    "product_id,product_category_name,product_name_lenght,product_description_lenght,"
    "product_photos_qty,product_weight_g,product_length_cm,product_height_cm,"
    "product_width_cm\n"
    "p1,cama_mesa_banho,40,200,1,500,20,10,15\n"
    "p2,beleza_saude,35,180,2,300,15,8,12\n"
)

_SELLERS_CSV = (
    "seller_id,seller_zip_code_prefix,seller_city,seller_state\n"
    "s1,05000,sao paulo,SP\n"
    "s2,06000,campinas,SP\n"
)

_TRANSLATION_CSV = (
    "product_category_name,product_category_name_english\n"
    "cama_mesa_banho,bed_bath_table\n"
    "beleza_saude,health_beauty\n"
)

# l1 before cutoff (kept), l2 after cutoff (excluded in batch 1).
_LEADS_CSV = (
    "mql_id,first_contact_date,landing_page_id,origin\n"
    "l1,2017-12-01,lp1,organic_search\n"
    "l2,2018-02-01,lp2,paid_search\n"
)

# d1 won before cutoff (kept), d2 won after cutoff (excluded in batch 1).
_DEALS_CSV = (
    "mql_id,seller_id,sdr_id,sr_id,won_date,business_segment,lead_type,"
    "lead_behaviour_profile,has_company,has_gtin,average_stock,business_type,"
    "declared_product_catalog_size,declared_monthly_revenue\n"
    "l1,s1,sdr1,sr1,2017-12-15 10:00:00,home,online,cat,true,true,1,reseller,10,1000\n"
    "l2,s2,sdr2,sr2,2018-03-15 10:00:00,beauty,online,eagle,false,false,2,manufacturer,20,2000\n"
)

_FIXTURES = {
    "olist_customers_dataset.csv": _CUSTOMERS_CSV,
    "olist_orders_dataset.csv": _ORDERS_CSV,
    "olist_order_items_dataset.csv": _ORDER_ITEMS_CSV,
    "olist_order_payments_dataset.csv": _ORDER_PAYMENTS_CSV,
    "olist_order_reviews_dataset.csv": _ORDER_REVIEWS_CSV,
    "olist_products_dataset.csv": _PRODUCTS_CSV,
    "olist_sellers_dataset.csv": _SELLERS_CSV,
    "product_category_name_translation.csv": _TRANSLATION_CSV,
    "olist_marketing_qualified_leads_dataset.csv": _LEADS_CSV,
    "olist_closed_deals_dataset.csv": _DEALS_CSV,
}


@pytest.fixture()
def raw_dir(tmp_path: Path) -> Path:
    """Write the synthetic CSV fixtures into a temp raw directory."""
    directory = tmp_path / "raw"
    directory.mkdir()
    for name, content in _FIXTURES.items():
        (directory / name).write_text(content, encoding="utf-8")
    return directory


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    """Path to a temp DuckDB file (not created until first connect)."""
    return tmp_path / "test_warehouse.duckdb"


def _load(raw_dir: Path, db_path: Path, batch: int) -> dict[str, int]:
    """Run one batch load into the temp DuckDB and return inserts per table."""
    cutoff = pd.Timestamp("2018-01-01") if batch == 1 else None
    loaded_at = "2018-01-01 06:00:00" if batch == 1 else "2018-10-18 06:00:00"
    target = load_raw.DuckDBTarget(db_path)
    try:
        return load_raw.run_load(target, raw_dir, cutoff, loaded_at)
    finally:
        target.close()


def _count(db_path: Path, table: str) -> int:
    con = duckdb.connect(str(db_path))
    try:
        return con.execute(f'SELECT count(*) FROM raw."{table}"').fetchone()[0]
    finally:
        con.close()


def _fetch(db_path: Path, table: str) -> pd.DataFrame:
    con = duckdb.connect(str(db_path))
    try:
        return con.execute(f'SELECT * FROM raw."{table}"').fetchdf()
    finally:
        con.close()


# --------------------------------------------------------------------------- #
# 1. Idempotent re-load
# --------------------------------------------------------------------------- #
def test_reloading_same_batch_adds_nothing(raw_dir: Path, db_path: Path) -> None:
    """Loading a batch twice leaves every table's row count unchanged."""
    _load(raw_dir, db_path, batch=1)
    counts_after_first = {spec.table: _count(db_path, spec.table) for spec in load_raw.SOURCE_SPECS}

    second = _load(raw_dir, db_path, batch=1)
    assert sum(second.values()) == 0, "second identical load inserted rows"

    counts_after_second = {spec.table: _count(db_path, spec.table) for spec in load_raw.SOURCE_SPECS}
    assert counts_after_first == counts_after_second


# --------------------------------------------------------------------------- #
# 2. AS-OF masking (batch 1)
# --------------------------------------------------------------------------- #
def test_batch1_masks_post_cutoff_order_timestamps_and_rolls_back_status(
    raw_dir: Path, db_path: Path
) -> None:
    """A pre-cutoff order delivered after the cutoff is rolled back to 'shipped'."""
    _load(raw_dir, db_path, batch=1)
    orders = _fetch(db_path, "orders").set_index("order_id")

    # o2: purchased + handed to carrier before cutoff, delivered after it.
    o2 = orders.loc["o2"]
    assert o2["order_delivered_customer_date"] == ""  # blanked (>= cutoff)
    assert o2["order_delivered_carrier_date"] == "2017-12-28 10:00:00"  # kept
    # estimated delivery date is NOT masked (known at purchase time).
    assert o2["order_estimated_delivery_date"] == "2018-01-08 00:00:00"
    assert o2["order_status"] == "shipped"

    # o3: only approved before cutoff -> 'processing'.
    o3 = orders.loc["o3"]
    assert o3["order_delivered_carrier_date"] == ""
    assert o3["order_delivered_customer_date"] == ""
    assert o3["order_approved_at"] == "2017-12-30 23:00:00"
    assert o3["order_status"] == "processing"

    # o1: fully delivered before cutoff -> unchanged, still 'delivered'.
    assert orders.loc["o1"]["order_status"] == "delivered"


def test_batch1_keeps_source_status_when_nothing_masked(
    raw_dir: Path, db_path: Path
) -> None:
    """A pre-cutoff order with no masked timestamp keeps its true source status.

    This guards the regression where status was re-derived for every kept order:
    o6 is 'invoiced' with an approved_at before the cutoff; naive re-derivation
    would rewrite it to 'processing'. Nothing is masked, so it must stay 'invoiced'.
    """
    _load(raw_dir, db_path, batch=1)
    orders = _fetch(db_path, "orders").set_index("order_id")
    assert orders.loc["o6"]["order_status"] == "invoiced"
    # All its timestamps are pre-cutoff / blank, so none were masked.
    assert orders.loc["o6"]["order_approved_at"] == "2017-12-11 10:00:00"


def test_batch1_keeps_canceled_status_and_no_new_version_in_batch2(
    raw_dir: Path, db_path: Path
) -> None:
    """A pre-cutoff canceled order stays 'canceled' and gains no new version.

    o5 is canceled with nothing masked: it comes through batch 1 still 'canceled'
    and, because its true batch-2 row is identical, produces no new version.
    o7 is canceled but had its carrier handoff (>= cutoff) masked: its status is
    still kept as 'canceled' (cancellation time unknown), and its true row differs
    so it DOES gain a second version in batch 2.
    """
    _load(raw_dir, db_path, batch=1)
    orders_b1 = _fetch(db_path, "orders").set_index("order_id")
    assert orders_b1.loc["o5"]["order_status"] == "canceled"
    assert orders_b1.loc["o7"]["order_status"] == "canceled"
    assert orders_b1.loc["o7"]["order_delivered_carrier_date"] == ""  # masked

    _load(raw_dir, db_path, batch=2)
    orders = _fetch(db_path, "orders")
    assert len(orders[orders["order_id"] == "o5"]) == 1  # single version
    assert len(orders[orders["order_id"] == "o7"]) == 2  # masked -> new version
    assert set(orders[orders["order_id"] == "o7"]["order_status"]) == {"canceled"}


def test_batch1_excludes_post_cutoff_orders_and_dependents(
    raw_dir: Path, db_path: Path
) -> None:
    """An order purchased after the cutoff (and its children) is excluded."""
    _load(raw_dir, db_path, batch=1)

    orders = _fetch(db_path, "orders")
    assert "o4" not in set(orders["order_id"])
    assert set(orders["order_id"]) == {"o1", "o2", "o3", "o5", "o6", "o7"}

    items = _fetch(db_path, "order_items")
    assert "o4" not in set(items["order_id"])

    payments = _fetch(db_path, "order_payments")
    assert "o4" not in set(payments["order_id"])

    # Reviews: r3 created after cutoff excluded; r2 answer blanked.
    reviews = _fetch(db_path, "order_reviews").set_index("review_id")
    assert set(reviews.index) == {"r1", "r2"}
    assert reviews.loc["r2"]["review_answer_timestamp"] == ""
    assert reviews.loc["r1"]["review_answer_timestamp"] == "2017-11-07 10:00:00"

    # Leads / deals filtered on their own date columns.
    leads = _fetch(db_path, "marketing_qualified_leads")
    assert set(leads["mql_id"]) == {"l1"}
    deals = _fetch(db_path, "closed_deals")
    assert set(deals["mql_id"]) == {"l1"}


def test_reference_tables_loaded_in_full_in_batch1(raw_dir: Path, db_path: Path) -> None:
    """Customers/products/sellers/translation are not filtered by the cutoff."""
    _load(raw_dir, db_path, batch=1)
    assert _count(db_path, "customers") == 4
    assert _count(db_path, "products") == 2
    assert _count(db_path, "sellers") == 2
    assert _count(db_path, "product_category_name_translation") == 2


# --------------------------------------------------------------------------- #
# Batch 1 -> Batch 2 CDC behaviour
# --------------------------------------------------------------------------- #
def test_batch2_appends_new_versions_for_changed_orders(
    raw_dir: Path, db_path: Path
) -> None:
    """Changed orders land as new versions; unchanged ones are skipped."""
    _load(raw_dir, db_path, batch=1)
    inserts = _load(raw_dir, db_path, batch=2)

    # o2, o3, o7 were masked in batch 1, so their true rows are new versions.
    # o4 is brand new. o1/o5/o6 were unchanged -> no new version.
    # Net new order rows in batch 2 = o2 + o3 + o7 + o4 = 4.
    assert inserts["orders"] == 4

    orders = _fetch(db_path, "orders")
    # o2 now has two versions (masked + true); table holds 4 orders * ... check o2.
    o2_versions = orders[orders["order_id"] == "o2"]
    assert len(o2_versions) == 2
    statuses = set(o2_versions["order_status"])
    assert statuses == {"shipped", "delivered"}

    # o1 still a single version.
    assert len(orders[orders["order_id"] == "o1"]) == 1


# --------------------------------------------------------------------------- #
# 3. Row hashing
# --------------------------------------------------------------------------- #
def test_identical_content_hashes_identically() -> None:
    """Two frames with identical source content produce identical hashes."""
    cols = ["a", "b"]
    frame1 = pd.DataFrame({"a": ["1", "x"], "b": ["2", "y"]})
    frame2 = pd.DataFrame({"a": ["1", "x"], "b": ["2", "y"]})
    h1 = load_raw.compute_row_hash(frame1, cols)
    h2 = load_raw.compute_row_hash(frame2, cols)
    assert list(h1) == list(h2)


def test_changed_field_changes_hash() -> None:
    """Changing any source value changes the row hash."""
    cols = ["a", "b"]
    base = pd.DataFrame({"a": ["1"], "b": ["2"]})
    changed = pd.DataFrame({"a": ["1"], "b": ["3"]})
    assert load_raw.compute_row_hash(base, cols)[0] != load_raw.compute_row_hash(changed, cols)[0]


def test_loaded_at_does_not_affect_row_hash(raw_dir: Path, db_path: Path) -> None:
    """``_loaded_at`` is metadata and must not influence ``_row_hash``.

    The same order row loaded under two different loaded_at values produces the
    same hash, which is exactly why an unchanged row is skipped in batch 2.
    """
    source_cols = ["order_id", "order_status"]
    frame = pd.DataFrame({"order_id": ["o1"], "order_status": ["delivered"]})
    enriched_a = load_raw.add_metadata_columns(
        frame, source_cols, "ecommerce", "orders.csv", "2018-01-01 06:00:00"
    )
    enriched_b = load_raw.add_metadata_columns(
        frame, source_cols, "ecommerce", "orders.csv", "2018-10-18 06:00:00"
    )
    assert enriched_a[load_raw.META_LOADED_AT][0] != enriched_b[load_raw.META_LOADED_AT][0]
    assert enriched_a[load_raw.META_ROW_HASH][0] == enriched_b[load_raw.META_ROW_HASH][0]
