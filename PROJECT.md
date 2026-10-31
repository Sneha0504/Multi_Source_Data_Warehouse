# Multi-Source Data Warehouse — Project Spec

> This is the standing project specification, saved verbatim from the prompt that
> kicked off the build. It is the source of truth for scope and requirements.

## Project details
- **Project name:** Multi-Source Data Warehouse
- **Target roles:** Data Engineer, Analytics Engineer, BI / Data Analyst (data platform teams)
- **Goal:** Integrate two real source systems from Olist, a Brazilian marketplace (its e-commerce
  platform and its marketing/sales CRM), into a tested, incremental dbt warehouse with a business-key
  integration layer that keeps full change history, and reporting marts on top.

### Key questions
1. How do the two systems connect, and where do their keys fail to match?
2. Who is the real customer? (Check whether `customer_id` is per order or per person.)
3. Which marketing channels bring in leads that become sellers, and which of those sellers actually sell?
4. Can the warehouse load data incrementally, capture changes over time (e.g. order status updates),
   and still produce identical results when fully rebuilt from raw?
5. What data quality problems exist in the sources, and how is each one handled?

### Required tools
Python, SQL, dbt (dbt-core with dbt-duckdb locally; dbt-snowflake for Snowflake), DuckDB,
Snowflake (free trial), Git. Optional: Tableau or Power BI for the dashboard.

### Deliverables
Ingestion script, dbt project (staging, integration, marts), dbt tests, rebuild check, data quality
report, data dictionary, lineage, dashboard extracts + build guide, memo, README.

---

## Staged requirements (from the prompt)

**STEP 0 — Read skills first (mandatory):** Read every file in `.claude` (project first, then `~/.claude`).
Follow them throughout. If a skill conflicts with this prompt, say which and ask before proceeding. List
the skills found and how they'll be applied.

**STEP 1 — Folder setup:** Create `Multi_Source_Data_Warehouse/` with the prescribed structure. Initialize
git. `.gitignore` excludes `data/raw`, `*.duckdb`, `target/`, `logs/`, `.env`, venvs. Commit at each
milestone. Credentials in `.env` (never committed); provide `.env.example`.

**STEP 2 — Scope and plan (PAUSE FOR APPROVAL):** Write `PLAN.md` (goal + key questions in own words, the
data and why it fits, every model per layer, incremental load design, tests, deliverables, risks with
fallbacks, out of scope). Then STOP and wait for approval.

**STEP 3 — Getting the data:** Two Kaggle datasets — Brazilian E-Commerce (olistbr/brazilian-ecommerce)
and Marketing Funnel (olistbr/marketing-funnel-olist). `scripts/download_data.py` via Kaggle API; if token
missing, print exact manual steps. Document both routes in `docs/data_sources.md` (URL, publisher, license
CC BY-NC-SA 4.0, date accessed, file names, row counts, date ranges). Record checksum + row count per raw
file. Never edit raw files. (geolocation dataset optional — skip unless needed.)

**STEP 4 — Profiling and data quality:** Profile every file before modeling → `docs/data_quality_report.md`.
Verify with actual counts: customer_id vs customer_unique_id; orders with no items / no payment; payment
total vs item+freight off by >$1; duplicate review_id, orders with >1 review; products with no category and
categories missing from translation; closed deals whose seller_id never appears in sellers/order items (and
why, checking won_date vs last order date); blank/'unknown' lead origins; delivered orders with no delivery
date / delivery before purchase; partial months at either end; text fields with embedded newlines breaking
naive CSV reads. Each → numbered rule with severity (critical/warning), failure count, handling. Critical
failures stop the pipeline. Never drop/change rows silently.

**STEP 5 — Ingestion and incremental loads:** `scripts/load_raw.py` lands each CSV unchanged into raw tables
adding `_source_system` ('ecommerce'|'crm'), `_source_file`, `_loaded_at`, `_row_hash` (hash of all source
columns). Re-loading identical rows adds nothing (skip existing `_row_hash`). Two targets via config:
DuckDB (local) and Snowflake (snowflake-connector-python, write_pandas, creds from `.env`). Simulate two
loads:
- **Batch 1** (`--cutoff 2018-01-01 --loaded-at "2018-01-01 06:00:00"`): AS-OF snapshot. Only orders
  purchased before cutoff (and their items/payments/reviews created before cutoff). Any order timestamp
  on/after cutoff is blanked; order_status derived as of cutoff (delivered after cutoff → 'shipped' if
  shipped else 'processing').
- **Batch 2** (no cutoff, `--loaded-at "2018-10-18 06:00:00"`): everything. New orders arrive; changed
  orders arrive as new versions (different `_row_hash`), like a CDC feed.
Raw tables keep every version of every row.

**STEP 6 — dbt models:** dbt-core. All DB-specific SQL in `macros/portable.sql` with branches for duckdb,
snowflake, bigquery: `hash_key(cols)`, `to_timestamp`, `days_between`, `to_month`, `add_days`, string type.
Models must not contain dialect-specific functions directly.
- **Staging (views)**, one per source table: rename, cast, trim/standardize text, normalize blank origins to
  'unknown', translate category names (with 'missing'/'untranslated' status), dedupe reviews to one row per
  (review_id, order_id). `stg_orders` = LATEST version; `stg_orders_history` = EVERY landed version.
- **Integration (incremental, insert-only, business keys hashed with `hash_key`):**
  Hubs: hub_customer (customer_unique_id), hub_order, hub_product, hub_seller (fed by BOTH systems;
  record_source = which system saw the seller first), hub_lead. Links: link_order_customer, link_order_item
  (order, product, seller), link_lead_seller (cross-system). Satellites with hashdiff (new row only when
  attributes change): sat_order_status (reads from stg_orders_history so full refresh rebuilds same
  history), sat_lead_details, sat_deal_details. First-seen load timestamps from full version history, not
  latest.
- **Marts (tables):** dim_customer, dim_seller (lead origin + acquired_via_marketing), dim_product,
  dim_date (date spine via macro), fct_order_items (grain: order line), fct_orders (grain: order — item
  value, freight, payments, reconciliation_status, delivery days, late flag, latest review score),
  mart_seller_acquisition (by lead origin: leads, closed deals, conversion rate, sellers with sales, deals
  won after order data ends, gross sales, sales per deal), mart_monthly_kpis (is_partial_month flag).

**STEP 7 — Tests and checks:** schema.yml: unique + not_null on every hash key; relationships links→hubs and
facts→dims; accepted_values for order_status, reconciliation_status, record_source. Singular tests: payments
reconcile (warn), no delivery before purchase, every closed deal traces to a lead, satellite history no
duplicate (key, hashdiff), fct_orders item value = sum of fct_order_items, partial months flagged (warn).
`scripts/compare_full_refresh.py`: copy warehouse after batch 2, run `dbt build --full-refresh` on the copy,
compare FULL table contents (not just counts) for every model → must report "identical"; if differs, fix
the model, don't weaken the check. pytest for load_raw.py: idempotent re-load, as-of masking, row hashing.

**STEP 8 — Code standards:** Python 3.11+, pinned requirements.txt, venv. Small typed functions with
docstrings, logging not print, explicit error handling, config in config.yaml, no hard-coded paths/creds.
SQL: CTEs, consistent naming (stg_/hub_/link_/sat_/dim_/fct_/mart_), comments on business logic. ruff for
Python, consistent SQL formatting. Makefile: setup, download, load-batch1, build, load-batch2,
rebuild-check, test, docs, all. `make all` runs everything end to end on DuckDB.

**STEP 9 — Analysis and deliverables:** Answer each key question with numbers from marts. docs/lineage.md
(`dbt docs generate` + Mermaid diagram), docs/data_dictionary.md (every mart), outputs/dashboard/ CSV
extracts + docs/dashboard_guide.md (3-page dashboard: Executive KPIs; Marketing funnel by channel; Data
quality and reconciliation — every visual/field/measure/filter). Charts: conventional matplotlib, labeled
axes/units, gridlines, standard palette. docs/memo.md: plain-language findings, recommendations, limitations.

**STEP 10 — Running on Snowflake:** After DuckDB passes, write exact steps in README.md (create trial,
database OLIST, schemas RAW + ANALYTICS, fill .env, load_raw.py target snowflake both batches, copy
profiles.yml.example to ~/.dbt/profiles.yml, `dbt build --target snowflake` after each batch, then rebuild
check). SQL failures on Snowflake → fix in macros, not models. Only claim it runs on Snowflake once it does.

**STEP 11 — Honesty rules:** Only list a tool as used if code actually ran with it. Never invent numbers —
every number in README/memo comes from query output. AI_USE.md lists every bug found and how caught, plus
anything unverified.

**STEP 12 — Final check and summary:** Run `make all` from clean + all tests; rebuild check says identical.
Confirm every README/memo number matches outputs; no raw data or secrets committed. Then provide: (1) what
was built + exact run commands (DuckDB + Snowflake), (2) key findings plain language, (3) DQ issues + how
handled, (4) bugs caught + how, (5) anything not run/verified, (6) a resume entry (project name, tools
actually used, two plain-language bullets — goal/what built, then what found; few key numbers, no em dashes).
