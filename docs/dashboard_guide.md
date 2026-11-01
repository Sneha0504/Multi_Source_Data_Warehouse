# Dashboard Build Guide

**Project:** Multi-Source Data Warehouse (Olist e-commerce + marketing CRM)
**Author:** mishra.sn@northeastern.edu
**Tool:** Tableau or Power BI (both covered below)

This guide describes a three-page dashboard built on the CSV extracts written by
`scripts/export_dashboard.py` into `outputs/dashboard/`. For every visual it lists
the source extract, the fields, the measure (as a **Power BI DAX** formula and a
**Tableau calculated field**), and the filters.

## Data sources (extracts)

| Extract CSV | Grain | Rows | Used on page |
| --- | --- | ---: | --- |
| `mart_monthly_kpis.csv` | one row per purchase month | 25 | 1, 3 |
| `mart_seller_acquisition.csv` | one row per marketing channel | 10 | 2 |
| `fct_orders.csv` | one row per order | 99,441 | 1, 3 |
| `reconciliation_summary.csv` | one row per reconciliation status | 5 | 3 |
| `dim_seller.csv` | one row per seller | 3,557 | 2 |
| `dim_product.csv` | one row per product | 32,951 | 2 (optional) |

**Model relationships** (set these up before building visuals):
`fct_orders[order_id]` → `fct_order_items` is not exported (order grain is enough
for these pages); link `fct_orders[customer_unique_id]` only if you add
`dim_customer`. On page 2, `dim_seller[lead_origin]` relates to
`mart_seller_acquisition[origin]`. Keep most visuals single-source to stay simple.

A reusable money format: BRL, thousands separator, 2 decimals. A reusable percent
format: 0.0%.

---

## Page 1 — Executive KPIs

Audience: leadership. Question: how is the marketplace trending? Primary source:
`mart_monthly_kpis.csv`, with four order-grain KPI cards from `fct_orders.csv`.

### Visual 1.1 — KPI card: Total orders
- **Source:** `fct_orders.csv`
- **Field:** `order_id`
- **Measure — DAX:** `Total Orders = DISTINCTCOUNT(fct_orders[order_id])`
- **Measure — Tableau:** `COUNTD([Order Id])`
- **Filters:** Date range slicer on `order_purchase_timestamp` (default: all).

### Visual 1.2 — KPI card: Gross revenue
- **Source:** `fct_orders.csv`
- **Field:** `total_item_value`
- **Measure — DAX:** `Gross Revenue = SUM(fct_orders[total_item_value])`
- **Measure — Tableau:** `SUM([Total Item Value])`
- **Filters:** same date slicer. Format as BRL.

### Visual 1.3 — KPI card: Average order value
- **Source:** `fct_orders.csv`
- **Fields:** `total_item_value`, `order_id`
- **Measure — DAX:** `AOV = DIVIDE([Gross Revenue], [Total Orders])`
- **Measure — Tableau:** `SUM([Total Item Value]) / COUNTD([Order Id])`
- **Filters:** same date slicer. (Reference: ~R$150–170 in mature months.)

### Visual 1.4 — KPI card: Repeat-order rate
- **Source:** `fct_orders.csv`
- **Field:** `customer_unique_id`
- **Measure — DAX:**
  `Repeat Rate = VAR people = DISTINCTCOUNT(fct_orders[customer_unique_id]) VAR repeat = COUNTROWS(FILTER(VALUES(fct_orders[customer_unique_id]), CALCULATE(DISTINCTCOUNT(fct_orders[order_id])) > 1)) RETURN DIVIDE(repeat, people)`
- **Measure — Tableau:** LOD —
  `COUNTD(IF {FIXED [Customer Unique Id] : COUNTD([Order Id])} > 1 THEN [Customer Unique Id] END) / COUNTD([Customer Unique Id])`
- **Filters:** none (whole history). Reference value: 3.12%. Format 0.0%.

### Visual 1.5 — Line chart: Orders and revenue by month
- **Source:** `mart_monthly_kpis.csv`
- **Fields:** axis `month_start`; line 1 `orders`; line 2 `gross_revenue` (secondary axis).
- **Measure:** plot the columns directly (`SUM(orders)`, `SUM(gross_revenue)`).
- **Filters:** **exclude partial months** — `is_partial_month = FALSE`, so the
  truncated boundary months (2016-09, 2018-09, 2018-10) do not distort the trend.
- **Notes:** label the peak, November 2017 (7,544 orders, ~R$1.18M).

### Visual 1.6 — Combo chart: Average review score by month
- **Source:** `mart_monthly_kpis.csv`
- **Fields:** axis `month_start`; bar `avg_review_score`.
- **Measure — DAX:** `Avg Review = AVERAGE(mart_monthly_kpis[avg_review_score])`
  (the column is already a monthly average; use `AVERAGE` only if you roll up).
- **Measure — Tableau:** `AVG([Avg Review Score])`
- **Filters:** `is_partial_month = FALSE`. Reference line at 4.0.

### Visual 1.7 — Line chart: Late-delivery rate by month
- **Source:** `mart_monthly_kpis.csv`
- **Fields:** axis `month_start`; line `late_delivery_rate`.
- **Measure:** plot `late_delivery_rate` (already a rate). Format 0.0%.
- **Filters:** `is_partial_month = FALSE` (boundary months have no delivered orders).
- **Notes:** reference line at the overall 6.77%; spikes to ~19% (2018-03).

---

## Page 2 — Marketing funnel by channel

Audience: marketing / growth. Question: which channels bring leads that become
sellers, and which of those sellers actually sell? Primary source:
`mart_seller_acquisition.csv`.

### Visual 2.1 — Funnel / stacked bar: Leads → deals → sellers with sales
- **Source:** `mart_seller_acquisition.csv`
- **Fields:** category `origin`; values `leads`, `closed_deals`, `sellers_with_sales`.
- **Measure:** plot the three columns side by side per channel
  (`SUM(leads)`, `SUM(closed_deals)`, `SUM(sellers_with_sales)`).
- **Filters:** sort channels by `closed_deals` descending.
- **Notes:** `organic_search` has the most volume (2,296 leads → 271 deals);
  `unknown` is a bucket, not a real channel.

### Visual 2.2 — Bar chart: Conversion rate by channel
- **Source:** `mart_seller_acquisition.csv`
- **Fields:** category `origin`; value `conversion_rate`.
- **Measure — DAX:** `Conversion Rate = DIVIDE(SUM([closed_deals]), SUM([leads]))`
  (or plot the stored `conversion_rate` column directly).
- **Measure — Tableau:** `SUM([Closed Deals]) / SUM([Leads])`
- **Filters:** optional — exclude `unknown` to compare true channels; among those,
  `paid_search` is best (~12.3%). Format 0.0%.

### Visual 2.3 — Bar chart: Gross sales and sales-per-deal by channel
- **Source:** `mart_seller_acquisition.csv`
- **Fields:** category `origin`; bar `gross_sales`; label `sales_per_deal`.
- **Measure — DAX:** `Sales per Deal = DIVIDE(SUM([gross_sales]), SUM([closed_deals]))`
- **Measure — Tableau:** `SUM([Gross Sales]) / SUM([Closed Deals])`
- **Filters:** none. Format `gross_sales` as BRL.

### Visual 2.4 — KPI card: Signed-but-not-selling sellers
- **Source:** `dim_seller.csv`
- **Fields:** `acquired_via_marketing`, `record_source`.
- **Measure — DAX:**
  `CRM-only Sellers = CALCULATE(DISTINCTCOUNT(dim_seller[seller_id]), dim_seller[record_source] = "crm")`
- **Measure — Tableau:** `COUNTD(IF [Record Source] = "crm" THEN [Seller Id] END)`
- **Filters:** none. Reference value: 462 sellers were signed via the CRM but
  never listed a product (0 sales).

### Visual 2.5 — Scatter / bar: Deals won after the order window closed
- **Source:** `mart_seller_acquisition.csv`
- **Fields:** category `origin`; value `deals_won_after_order_data_ends`.
- **Measure:** plot the column directly (`SUM(deals_won_after_order_data_ends)`).
- **Filters:** none. Explains part of why some signed sellers show no sales (the
  order data ends 2018-10-17).

### Visual 2.6 (optional) — Table: Top seller categories
- **Source:** `dim_product.csv`
- **Fields:** `product_category`, count of `product_id`; filter
  `category_translation_status`.
- **Measure — DAX:** `Products = DISTINCTCOUNT(dim_product[product_id])`
- **Measure — Tableau:** `COUNTD([Product Id])`
- **Filters:** `category_translation_status = "translated"` to drop missing /
  untranslated categories from the category breakdown.

---

## Page 3 — Data quality & reconciliation

Audience: data / finance. Question: how trustworthy is the data, and where does
money not tie out? Primary sources: `reconciliation_summary.csv`,
`fct_orders.csv`, `mart_monthly_kpis.csv`.

### Visual 3.1 — Donut / bar: Orders by reconciliation status
- **Source:** `reconciliation_summary.csv`
- **Fields:** category `reconciliation_status`; value `order_count`; label `pct_of_orders`.
- **Measure:** plot `order_count` directly (one pre-aggregated row per status).
- **Filters:** none. Reference: `match` 98,416; `no_items` 775; `overpaid` 232;
  `underpaid` 17; `no_payment` 1.

### Visual 3.2 — KPI card: Reconciliation match rate
- **Source:** `reconciliation_summary.csv`
- **Fields:** `reconciliation_status`, `order_count`
- **Measure — DAX:**
  `Match Rate = DIVIDE(CALCULATE(SUM([order_count]), reconciliation_summary[reconciliation_status] = "match"), SUM([order_count]))`
- **Measure — Tableau:**
  `SUM(IF [Reconciliation Status] = "match" THEN [Order Count] END) / SUM([Order Count])`
- **Filters:** none. Format 0.0% (≈ 99.0%).

### Visual 3.3 — Histogram: Payment vs items difference (mismatched orders)
- **Source:** `fct_orders.csv`
- **Fields:** bin a computed difference; count `order_id`.
- **Measure — DAX (calculated column):**
  `Pay Diff = fct_orders[payment_value] - fct_orders[total_item_value]`
- **Measure — Tableau (calculated field):** `[Payment Value] - [Total Item Value]`
- **Filters:** `reconciliation_status IN ("overpaid","underpaid")` so the 249
  mismatched orders are isolated. Bin the difference (e.g. R$10 bins).

### Visual 3.4 — Bar chart: Delivered-order quality — late rate and review by month
- **Source:** `mart_monthly_kpis.csv`
- **Fields:** axis `month_start`; bar `late_delivery_rate`; line `avg_review_score`.
- **Measure:** plot both columns (dual axis).
- **Filters:** `is_partial_month = FALSE`.
- **Notes:** this is the late-delivery / review link; the detail is in 3.5.

### Visual 3.5 — Bar chart: Review score, late vs on-time
- **Source:** `fct_orders.csv`
- **Fields:** category `is_late`; value average of `latest_review_score`.
- **Measure — DAX:** `Avg Review (by late) = AVERAGE(fct_orders[latest_review_score])`
- **Measure — Tableau:** `AVG([Latest Review Score])`
- **Filters:** exclude nulls — `is_late` is not null **and** `latest_review_score`
  is not null. Reference: late orders average **2.27**, on-time **4.29**.

### Visual 3.6 — KPI card: Partial / truncated months flagged
- **Source:** `mart_monthly_kpis.csv`
- **Fields:** `is_partial_month`
- **Measure — DAX:**
  `Partial Months = CALCULATE(COUNTROWS(mart_monthly_kpis), mart_monthly_kpis[is_partial_month] = TRUE())`
- **Measure — Tableau:** `COUNT(IF [Is Partial Month] THEN [Month Start] END)`
- **Filters:** none. Reference value: 3 (2016-09, 2018-09, 2018-10). Use this
  card plus a note so viewers know those months are excluded elsewhere.

---

## Global dashboard filters (apply to all pages where relevant)

- **Date range** on `order_purchase_timestamp` / `month_start`.
- **Exclude partial months** (`is_partial_month = FALSE`) on every monthly trend.
- **Channel** (`origin`) selector on page 2.
- Keep one consistent color per reconciliation status and per channel across pages.
