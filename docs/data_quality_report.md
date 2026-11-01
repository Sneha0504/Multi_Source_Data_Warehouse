# Data Quality Report

**Project:** Multi-Source Data Warehouse (Olist e-commerce + marketing CRM)
**Date generated:** 2026-10-31
**Warehouse profiled:** `olist_warehouse.duckdb` (raw / staging / integration / marts, fully built)
**Author:** mishra.sn@northeastern.edu

Every number below comes from an actual query against the built warehouse or a
direct read of the raw CSVs in `data/raw/` (per PROJECT.md Step 11, nothing is
invented). Where a figure differs from the project's rough expectation, the
difference is called out explicitly in the rule.

---

## 1. Sources profiled

| Source system | File / table | Rows (parsed) | Notes |
| --- | --- | ---: | --- |
| ecommerce | `olist_customers_dataset.csv` | 99,441 | 1 row per `customer_id` |
| ecommerce | `olist_orders_dataset.csv` | 99,441 | 1 row per `order_id`; 2016-09-04 → 2018-10-17 |
| ecommerce | `olist_order_items_dataset.csv` | 112,650 | grain (order_id, order_item_id) |
| ecommerce | `olist_order_payments_dataset.csv` | 103,886 | multiple payment rows per order |
| ecommerce | `olist_order_reviews_dataset.csv` | 99,224 | see Rule 4 & 10 (embedded newlines) |
| ecommerce | `olist_products_dataset.csv` | 32,951 | see Rule 5 |
| ecommerce | `olist_sellers_dataset.csv` | 3,095 | ecommerce seller catalog |
| ecommerce | `product_category_name_translation.csv` | 71 | PT → EN category lookup |
| crm | `olist_marketing_qualified_leads_dataset.csv` | 8,000 | 2017-06-14 → 2018-05-31 |
| crm | `olist_closed_deals_dataset.csv` | 842 | 2017-12-05 → 2018-11-14 |

(The optional geolocation dataset is present on disk but intentionally not
loaded or modeled.)

**Note on raw row versioning:** the raw landing zone keeps *every* landed version
of every row across the two simulated loads (Batch 1 AS-OF snapshot + Batch 2
full CDC feed). For example `raw.order_reviews` holds 99,903 physical rows, which
the staging layer collapses to the 99,224 current reviews. All source-level
quality numbers below are measured against the raw CSVs (the true source) so the
batch simulation does not distort the counts.

---

## 2. Data quality rules

Severity key: **critical** = maps to an error-severity dbt test; a failure
**stops the pipeline** (`dbt build` fails). **warning** = surfaced via a
warn-severity test or a status column, but never fatal. In every case **rows are
kept — never dropped or silently changed**; problems are flagged in columns or
tests so they stay visible and auditable.

| # | Rule / finding | Severity | Count (actual) | How it is handled |
| --- | --- | --- | ---: | --- |
| 1 | `customer_id` is issued per order, not per person | warning | 99,441 ids → 96,096 people; 2,997 repeat buyers (3.12%), max 17 orders/person | `hub_customer` / `dim_customer` keyed on `customer_unique_id`; `customer_id` treated as per-order |
| 2 | Orders with no line items / no payment | warning | 775 no_items; 1 no_payment | `fct_orders.reconciliation_status` buckets `no_items` / `no_payment`; excluded from reconcile test; rows kept |
| 3 | Payment total differs from item+freight by > $1 | warning | 249 (232 overpaid + 17 underpaid) | `reconciliation_status` = `overpaid`/`underpaid`; `assert_payments_reconcile` (WARN) surfaces count |
| 4 | Duplicate `review_id` across orders; orders with >1 review | warning | 789 review_ids reused; 547 orders with >1 review | `stg_order_reviews` dedups to one row per (review_id, order_id) → 99,224; `fct_orders` keeps latest review/order |
| 5 | Products with no category; categories missing from translation file | warning | 610 no category; 13 products in 2 untranslated categories | `stg_products.category_translation_status` = `missing` / `untranslated`; rows kept |
| 6 | Closed deals whose seller never appears in the seller catalog / order items | warning | 462 CRM-only sellers; 462 deals with 0 sales; 17 / 50 won after data ends | `hub_seller` fed by both systems; `dim_seller` keeps CRM-only sellers (NULL geo); mart tracks `deals_won_after_order_data_ends`; `assert_closed_deal_traces_to_lead` (ERROR) guarantees every deal still ties to a lead |
| 7 | Marketing lead origin blank or `unknown` | warning | 60 blank + 1,099 `unknown` = 1,159 | `stg_marketing_qualified_leads` normalizes blank → `unknown` |
| 8a | Delivered orders with no delivery date | warning | 8 | `delivery_days` left NULL; order kept with its stated status |
| 8b | Delivery before purchase (causal impossibility) | **critical** | 0 | `assert_no_delivery_before_purchase` (ERROR) fails build if any appear |
| 9 | Partial (low-volume boundary) months | warning | 2016-09 (4), 2018-09 (16), 2018-10 (4) | `mart_monthly_kpis.is_partial_month = true`; `assert_partial_months_flagged` (WARN) |
| 10 | Review text with embedded newlines that break naive CSV reads | critical if mishandled | 104,719 physical lines vs 99,224 parsed (5,495 extra) | `load_raw.read_source_csv` uses pandas quoted parsing; load would corrupt rows otherwise |

### Rule detail

**1 — `customer_id` is per order, not per person.**
`raw.customers`: 99,441 rows, 99,441 distinct `customer_id`, but only **96,096**
distinct `customer_unique_id`. So `customer_id` is a per-order surrogate; the real
person is `customer_unique_id`. 2,997 people (3.12% of 96,096) placed more than one
order; the busiest placed 17. The warehouse resolves the person via
`hub_customer`/`link_order_customer`/`dim_customer` on `customer_unique_id`.

**2 — Orders with no items / no payment.** From `fct_orders`
(`reconciliation_status`): `match` 98,416, `no_items` **775**, `overpaid` 232,
`underpaid` 17, `no_payment` **1** (order `bfbd0f9bdef84302105ad712db648a6c`).
These empty-side cases are classified first so an empty side is never mislabeled
as a payment mismatch.

**3 — Payment vs item+freight off by > $1.** **249** orders exceed the $1.00
tolerance: 232 overpaid (diff $1.01 → $182.81) and 17 underpaid (diff $1.60 →
$51.62). Example magnitudes: `ce6d150f…` overpaid by $182.81 (1403.66 vs 1586.47);
`6e5fe736…` overpaid by $119.01 (287.91 vs 406.92); `70b74279…` overpaid by
$111.89 (466.93 vs 578.82). These are legitimate marketplace behaviour (vouchers,
installment rounding, partial refunds), so `assert_payments_reconcile` is a
**WARN**, not a build-breaker.

**4 — Duplicate reviews.** The reviews CSV parses to 99,224 rows with 98,410
distinct `review_id` but 99,224 distinct (review_id, order_id) pairs — so
(review_id, order_id) is unique, while **789** review_ids are reused across
multiple orders (1,603 rows involved) and **547** orders carry more than one
review. `stg_order_reviews` dedups to one row per (review_id, order_id) (99,224),
and `fct_orders` keeps only the latest review score per order.

**5 — Missing / untranslated categories.** `raw.products`: **610** of 32,951
products have a blank category (`category_translation_status = 'missing'`).
`stg_products` buckets: translated 32,328, missing 610, untranslated **13**. The 13
untranslated products fall in **2** category names absent from the translation
file: **`pc_gamer`** and
**`portateis_cozinha_e_preparadores_de_alimentos`**. No product row is dropped;
each carries its translation status.

**6 — Closed deals whose seller never sells.** Of the 842 closed deals' sellers,
**462** have no row in `stg_sellers` (CRM-only sellers that were won but never
appeared in the ecommerce catalog), and the same **462** have zero rows in
`fct_order_items` — only **380 of 842** deal sellers ever sold anything.
(Every one of the 3,095 catalog sellers does have order items, so "no catalog
row" and "no sales" are the same 462 sellers.) *Why they never sell:* two causes.
(a) **Deals won after the order data ends.** The order window runs to a max
purchase of **2018-10-17**, but closed-deal `won_date` runs to **2018-11-14**;
**17** deals were won after the absolute last purchase. More importantly the
*effective* order stream ends **2018-08-31** (Sept/Oct 2018 are partial boundary
months — see Rule 9); **50** deals were won after 2018-08-31 and so had almost no
runway to list and sell. (b) **Sellers who signed but never listed a product.**
The remainder are sellers onboarded via the CRM who simply never created
listings. Handling: `hub_seller` is fed by both systems, `dim_seller` retains
CRM-only sellers (NULL city/state) rather than dropping them, and
`mart_seller_acquisition.deals_won_after_order_data_ends` quantifies the cutoff
effect. *Note vs expectation:* the mart's `deals_won_after_order_data_ends` uses
the absolute max purchase and so reports **17**; the effective-end (2018-08-31)
count is **50**, not the ~53 roughly expected.

**7 — Blank / unknown lead origin.** `raw.marketing_qualified_leads` (8,000 rows):
**60** have a blank/empty `origin` and **1,099** are already literally `unknown`.
`stg_marketing_qualified_leads` normalizes blank → `unknown`, giving **1,159**
`unknown` leads so the funnel has no empty channel.

**8 — Delivery dates.** **8** orders carry status `delivered` but no
`order_delivered_customer_date` (delivery_days left NULL, order kept). **0** orders
have a delivery date earlier than purchase — enforced by the error-severity
`assert_no_delivery_before_purchase`, which would fail the build if any appeared.

**9 — Partial boundary months.** The order series is truncated at both ends, so the
first and last calendar months hold only a sliver of activity: **2016-09 (4
orders), 2018-09 (16), 2018-10 (4)** are flagged `is_partial_month = true` in
`mart_monthly_kpis`. (Interior month 2016-12 has just 1 order but is *not* flagged
— it is not part of a boundary run.) `assert_partial_months_flagged` (WARN) sanity
-checks that low-volume boundary months stay flagged.

**10 — Embedded newlines in review text.** The reviews CSV has **104,719** physical
data lines but only **99,224** logical rows — 5,495 extra physical lines come from
newline characters embedded inside quoted `review_comment_message` fields. A naive
line-based reader would shatter 5,495 reviews into corrupt partial rows.
`scripts/load_raw.read_source_csv` loads via pandas' quoting-aware C parser
(`dtype=str`, `na_filter=False`), so each review lands as exactly one row. The
other files have no embedded-newline gap (e.g. orders: 99,442 physical lines =
header + 99,441 rows).

---

## 3. Other notable findings

- **No duplicate business keys at grain.** `order_id` is unique across 99,441
  orders; all hub hash keys pass their `unique` + `not_null` tests. This is
  enforced, not assumed.
- **Seller funnel skew.** Conversion is concentrated: `organic_search` (2,296
  leads → 271 deals), `paid_search` (1,586 → 195) and `unknown` (1,159 → 193)
  account for most closed deals; `other_publicities` closed 3 deals and 0 of its
  sellers ever sold.
- **Reviews ≠ orders.** 99,224 reviews vs 99,441 orders means a minority of orders
  have no review at all, while 547 have several — reviews are optional and
  many-to-one, which is why `fct_orders.latest_review_score` can be NULL.

---

## 4. How data quality is enforced

Quality is enforced in the dbt build, not by hand. The rule severities above map
directly to tests under `olist_dbt/tests/` and the `schema.yml` files:

**Critical (error severity — a failure stops `dbt build`):**
- `tests/assert_no_delivery_before_purchase.sql` — Rule 8b (0 failures).
- `tests/assert_closed_deal_traces_to_lead.sql` — every closed deal traces to a
  lead in `hub_lead` (backs Rule 6).
- `tests/assert_fct_orders_item_value.sql` — header item value = Σ line items.
- `tests/assert_satellite_no_duplicate_key_hashdiff.sql` — no (entity_hk,
  hashdiff) stored twice (guarantees full-refresh = incremental history).
- All generic `unique`, `not_null`, `relationships`, and `accepted_values` tests
  in the staging / integration / marts `schema.yml` files run at dbt's default
  **error** severity — including `accepted_values` on `reconciliation_status`,
  which keeps the buckets in Rules 2 & 3 well-defined.

**Warning (warn severity — surfaced, never fatal):**
- `tests/assert_payments_reconcile.sql` — Rule 3 (249 surfaced).
- `tests/assert_partial_months_flagged.sql` — Rule 9.

**Status columns (visible flags, no row loss):**
`reconciliation_status` (Rules 2–3), `category_translation_status` (Rule 5),
`origin` normalization (Rule 7), `is_partial_month` (Rule 9), and NULL-left
metrics such as `delivery_days` (Rule 8a).

**Core principle:** critical failures halt the pipeline so corrupt data cannot
reach the marts; warnings and status columns keep known, benign issues visible for
analysis. **No row is ever dropped or silently altered** — every source row is
retained and every quality issue is expressed as a flag, a status value, or a
test result.
