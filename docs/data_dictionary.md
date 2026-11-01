# Data Dictionary — Reporting Marts

**Project:** Multi-Source Data Warehouse (Olist e-commerce + marketing CRM)
**Layer documented:** marts (DuckDB schema `main_marts`; Snowflake `OLIST.ANALYTICS`)
**Author:** mishra.sn@northeastern.edu
**Date:** 2026-10-31

Every mart is materialized as a table. Grain and row counts below are measured
against the built `olist_warehouse.duckdb`. Column types are the DuckDB types
reported by `information_schema` (Snowflake equivalents in parentheses where they
differ). All money is in Brazilian Real (BRL), the currency of the source data.

A note on keys: a `*_hk` column is a **hash key** — the md5 of the business key,
produced by the portable `hash_key()` macro. It is the stable surrogate used to
join facts to dimensions and links to hubs. The natural business key (e.g.
`order_id`, `customer_unique_id`) is always carried alongside it.

---

## dim_customer

**Grain:** one row per person (`customer_unique_id`). **Rows:** 96,096.

The e-commerce source issues a new `customer_id` for every order, so the real
person is `customer_unique_id` (Key Question 2). This dimension resolves to the
person.

| Column | Type | Description |
| --- | --- | --- |
| `customer_hk` | VARCHAR | Hash key of `customer_unique_id`. Unique, not null. |
| `customer_unique_id` | VARCHAR | Business key: the real person across all their orders. Unique, not null. |
| `city` | VARCHAR | Representative customer city: the most frequent `(city, state)` pair for the person, ties broken alphabetically (deterministic so a full rebuild is identical). |
| `state` | VARCHAR | Representative customer state (two-letter Brazilian UF), chosen with the same rule as `city`. |
| `orders_count` | BIGINT | Distinct orders placed by the person (via `link_order_customer`). |
| `is_repeat_customer` | BOOLEAN | True when `orders_count > 1`. 2,997 people (3.12%) are repeat buyers; max is 17 orders. |

---

## dim_seller

**Grain:** one row per `seller_id`. **Rows:** 3,557 (3,095 e-commerce + 462 CRM-only).

Fed by **both** source systems. 462 sellers exist only in the CRM closed-deals
feed and never appeared in the e-commerce catalog, so their `city`/`state` are
NULL — the seller is kept, not dropped (Key Question 1).

| Column | Type | Description |
| --- | --- | --- |
| `seller_hk` | VARCHAR | Hash key of `seller_id`. Unique, not null. |
| `seller_id` | VARCHAR | Business key. Unique, not null. |
| `city` | VARCHAR | Seller city from the e-commerce catalog. NULL for the 462 CRM-only sellers. |
| `state` | VARCHAR | Seller state (Brazilian UF). NULL for CRM-only sellers. |
| `lead_origin` | VARCHAR | Marketing channel of the acquiring lead (e.g. `organic_search`, `paid_search`, `unknown`), resolved through `link_lead_seller` → latest `sat_lead_details`. NULL if the seller did not come from a tracked marketing deal. |
| `acquired_via_marketing` | BOOLEAN | True when the seller appears in `link_lead_seller` (came from a closed marketing deal). 842 sellers are marketing-acquired. |
| `record_source` | VARCHAR | Which system saw the seller first: `ecommerce` or `crm`. Accepted values enforced by test. |

---

## dim_product

**Grain:** one row per `product_id`. **Rows:** 32,951.

| Column | Type | Description |
| --- | --- | --- |
| `product_hk` | VARCHAR | Hash key of `product_id`. Unique, not null. |
| `product_id` | VARCHAR | Business key. Unique, not null. |
| `product_category` | VARCHAR | English category name (translated from Portuguese). NULL for products with no category. |
| `category_translation_status` | VARCHAR | `translated` (32,328), `missing` (610 products with blank category), or `untranslated` (13 products in 2 categories absent from the translation file: `pc_gamer`, `portateis_cozinha_e_preparadores_de_alimentos`). |
| `product_weight_g` | DECIMAL(12,2) | Product weight in grams. |
| `product_length_cm` | DECIMAL(12,2) | Package length in cm. |
| `product_height_cm` | DECIMAL(12,2) | Package height in cm. |
| `product_width_cm` | DECIMAL(12,2) | Package width in cm. |

---

## dim_date

**Grain:** one row per calendar day. **Rows:** 1,096 (2016-01-01 .. 2018-12-31).

Built from a portable `dbt_utils.date_spine` over the `date_spine_start` /
`date_spine_end` project vars.

| Column | Type | Description |
| --- | --- | --- |
| `date_day` | DATE | The calendar day. Unique, not null. |
| `year` | BIGINT | Four-digit year. |
| `month_number` | BIGINT | Month of year, 1–12. |
| `month_start` | DATE | First day of the day's month. Join target for `fct_orders.order_purchase_month`. |
| `day_of_week` | BIGINT | Day of week, 0 = Sunday .. 6 = Saturday (ANSI `extract(dow …)`). |
| `day_name` | VARCHAR | Day name (`Sunday` .. `Saturday`), derived from `day_of_week`. |
| `is_weekend` | BOOLEAN | True for Saturday (6) and Sunday (0). |

---

## fct_order_items

**Grain:** one row per order line (`order_id`, `order_item_id`). **Rows:** 112,650.

| Column | Type | Description |
| --- | --- | --- |
| `order_hk` | VARCHAR | Hash key of the order (FK to `hub_order` / `fct_orders`). Not null. |
| `order_id` | VARCHAR | Order business key. |
| `order_item_id` | INTEGER | Line sequence within the order (1, 2, …). |
| `product_hk` | VARCHAR | Hash key of the product (FK to `dim_product`). Not null. |
| `seller_hk` | VARCHAR | Hash key of the seller (FK to `dim_seller`). Not null. |
| `product_id` | VARCHAR | Product business key (carried for direct joins). |
| `seller_id` | VARCHAR | Seller business key (carried for direct joins). |
| `price` | DECIMAL(12,2) | Item price (BRL). |
| `freight_value` | DECIMAL(12,2) | Freight charged for the line (BRL). |
| `item_value` | DECIMAL(13,2) | `price + freight_value` — the full amount charged for the line. |

---

## fct_orders

**Grain:** one row per `order_id`. **Rows:** 99,441.

Order header with money rolled up from the line items and reconciled against the
payments.

| Column | Type | Description |
| --- | --- | --- |
| `order_hk` | VARCHAR | Hash key of `order_id`. Unique, not null. |
| `order_id` | VARCHAR | Business key. Unique, not null. |
| `customer_unique_id` | VARCHAR | The person behind the order (FK to `dim_customer`), resolved via `link_order_customer`. |
| `order_status` | VARCHAR | Latest order status. Accepted values: `delivered`, `shipped`, `canceled`, `unavailable`, `invoiced`, `processing`, `created`, `approved`. |
| `order_purchase_timestamp` | TIMESTAMP | When the order was placed. |
| `order_purchase_month` | TIMESTAMP | First day of the purchase month (FK to `dim_date.month_start`). |
| `item_value` | DECIMAL(38,2) | Sum of line `price` across the order (0 if no items). |
| `freight` | DECIMAL(38,2) | Sum of line `freight_value` across the order. |
| `total_item_value` | DECIMAL(38,2) | `item_value + freight` — the order's expected charge. |
| `payment_value` | DECIMAL(38,2) | Sum of all payment rows for the order. NULL if the order has no payment. |
| `reconciliation_status` | VARCHAR | Payment vs `total_item_value` bucket (tolerance = $1.00). Values: `no_items` (no line items; 775), `no_payment` (no payment rows; 1), `match` (within tolerance; 98,416), `overpaid` (payment > total + tolerance; 232), `underpaid` (payment < total − tolerance; 17). Empty-side cases (`no_items`/`no_payment`) are classified first so they are never mislabeled as a mismatch. |
| `delivery_days` | BIGINT | Days from purchase to customer delivery. NULL unless delivered (and NULL for the 8 delivered orders missing a delivery date). |
| `is_late` | BOOLEAN | **Late = delivered after the promised calendar day:** `cast(delivered_date as date) > cast(estimated_date as date)`. Compared at date level so delivery any time on the promised day is on-time. NULL unless delivered. Overall late rate 6.77%. |
| `latest_review_score` | INTEGER | Score (1–5) of the most recent review for the order (newest answered, then newest created). NULL for orders with no review (768 orders). |

---

## mart_seller_acquisition

**Grain:** one row per lead origin / marketing channel (including `unknown`).
**Rows:** 10. Answers Key Question 3.

| Column | Type | Description |
| --- | --- | --- |
| `origin` | VARCHAR | Marketing channel (lead origin), e.g. `organic_search`, `paid_search`, `social`, `unknown`. Unique, not null. |
| `leads` | BIGINT | Marketing qualified leads with that origin. |
| `closed_deals` | BIGINT | Closed deals whose lead has that origin (deal keyed by `mql_id`). |
| `conversion_rate` | DOUBLE | `closed_deals / leads`. |
| `sellers_with_sales` | BIGINT | Distinct sellers from that origin's deals that have at least one line in `fct_order_items` (actually sold something). |
| `deals_won_after_order_data_ends` | BIGINT | Closed deals won after the last order in the e-commerce data (`won_date > max(order_purchase_timestamp)` = 2018-10-17); these sellers cannot show sales simply because the order window ended. |
| `gross_sales` | DECIMAL(38,2) | Total `item_value` (price + freight) across that origin's sellers (BRL). |
| `sales_per_deal` | DOUBLE | `gross_sales / closed_deals`. |

Totals across all origins: 8,000 leads, 842 closed deals, 380 sellers with
sales, R$775,815.63 gross.

---

## mart_monthly_kpis

**Grain:** one row per purchase month. **Rows:** 25 (2016-09 .. 2018-10).

| Column | Type | Description |
| --- | --- | --- |
| `month_start` | TIMESTAMP | First day of the purchase month. Unique, not null. |
| `orders` | BIGINT | Orders placed that month. |
| `distinct_customers` | BIGINT | Distinct people (`customer_unique_id`) who ordered that month. |
| `gross_revenue` | DECIMAL(38,2) | Sum of `total_item_value` (price + freight) for the month (BRL). |
| `avg_order_value` | DOUBLE | `gross_revenue / orders`. |
| `late_delivery_rate` | DOUBLE | `late_deliveries / delivered_orders`. Orders not yet delivered are excluded from the denominator. |
| `avg_review_score` | DOUBLE | Average `latest_review_score` for the month's orders. |
| `is_partial_month` | BOOLEAN | **True for low-volume boundary months only.** The order window is truncated at both ends; walking inward from the first and last month, a month is flagged while its order count stays below 5% of the median monthly count (~214), and the run stops at the first month at/above it. Flagged: 2016-09 (4 orders), 2018-09 (16), 2018-10 (4). Interior sparse months (e.g. 2016-12 with 1 order) are **not** flagged. |
