# PLAN — Multi-Source Data Warehouse

Status: **Awaiting approval (Step 2).** Nothing in Steps 3–12 will be built until you approve this plan.

---

## 0. Skills found and how they'll be applied

Read from `coding projects/.claude/` (there is no separate `~/.claude` project skill set beyond memory):

| Skill / file | How it applies here |
|---|---|
| `build-project` | This whole run is driven by it: ingest spec → read skills → plan → build via coder/verifier → hand off to push-to-github. |
| `push-to-github` | Used for version control. **See the conflict in §7 — needs your decision.** |
| `.claude/agent-routing.md` + `agents/` | All real coding is delegated to the **`coder`** agent (Opus 4.8) and checked by the **`verifier`** agent (Opus 5.5); trivial bits to `simple-task` (Sonnet 5.5). This is *additive* to your spec — it adds an independent verification pass on every unit. |

**Spec ↔ skill conflict to resolve before building:** your spec Step 1 says *"Initialize git … commit at each milestone,"* but the `push-to-github` skill says I must **never run any git command myself** — only hand you commands. These collide on who runs local git. Resolution options are in §7; I will not run `git init` until you choose.

No other conflicts. (The `build-project` skill's "then build" step is consistent with your Step 2 STOP — it says to share the plan before large work.)

---

## 1. Goal and key questions (in my own words)

Build a local-first, fully tested analytics warehouse that stitches together **two real Olist source
systems** — the e-commerce platform (orders, items, payments, reviews, products, sellers, customers) and
the marketing CRM (qualified leads → closed deals) — using a **Data-Vault-style integration layer** (hubs /
links / satellites keyed on real business keys) that preserves **full change history**, with **incremental
loads** on top of raw, and clean **reporting marts**. Everything must **rebuild identically from raw**.

What I'm actually answering:
1. **How do the two systems join, and where do keys break?** The bridge is `seller_id`: the CRM's closed
   deals produce sellers that should appear in the e-commerce sellers/order-items — but some won't (deals
   won after the data window, or sellers who never sold). I'll quantify the non-matches and explain each.
2. **Who is the real customer?** Test whether `customer_id` is per-order (a new id each purchase) vs
   `customer_unique_id` (the actual person) — and report real-person count and repeat-purchase rate.
3. **Which marketing channels produce sellers who actually sell?** Funnel by `origin`: leads → closed
   deals → sellers with ≥1 real sale, with conversion rates and gross sales per channel.
4. **Does incremental + history + full-rebuild all hold?** Prove it with a two-batch CDC simulation and a
   byte-for-byte full-refresh comparison.
5. **What are the data quality problems and how is each handled?** A numbered DQ rule set with real counts,
   severities, and explicit handling (no silent drops).

---

## 2. The data and why it fits

- **Brazilian E-Commerce Public Dataset by Olist** (`olistbr/brazilian-ecommerce`): ~100k orders 2016–2018
  across customers, orders, items, payments, reviews, products, sellers + category translation. Realistic
  messy transactional data with status timestamps → perfect for staging, history, and incremental CDC.
- **Marketing Funnel by Olist** (`olistbr/marketing-funnel-olist`): qualified leads + closed deals, joinable
  to the e-commerce sellers via `seller_id`. Gives a genuine **cross-system** integration story and the
  key-mismatch problem that makes the integration layer meaningful.
- License **CC BY-NC-SA 4.0** (non-commercial, attribution, share-alike) — fine for a portfolio project with
  attribution. Geolocation file skipped unless a model needs it.

Why it fits the brief: two independently-keyed real systems, a natural but imperfect join key, status fields
that change over time (for CDC/history), and enough DQ warts to make the quality work real.

---

## 3. Models per layer

**Staging (views, 1:1 with source tables):** `stg_customers`, `stg_orders` (latest version),
`stg_orders_history` (every landed version), `stg_order_items`, `stg_order_payments`, `stg_order_reviews`
(deduped to one row per review_id+order_id), `stg_products` (category translated, with
missing/untranslated status), `stg_sellers`, `stg_marketing_qualified_leads` (blank origin → 'unknown'),
`stg_closed_deals`.

**Integration (incremental, insert-only, hashed business keys):**
- Hubs: `hub_customer` (customer_unique_id), `hub_order`, `hub_product`, `hub_seller` (**fed by both
  systems**, record_source = first system to see it), `hub_lead`.
- Links: `link_order_customer`, `link_order_item` (order×product×seller), `link_lead_seller` (cross-system).
- Satellites (hashdiff, new row only on attribute change): `sat_order_status` (**reads
  stg_orders_history** so a rebuild reproduces history), `sat_lead_details`, `sat_deal_details`.
  First-seen timestamps come from the full version history.

**Marts (tables):** `dim_customer`, `dim_seller` (lead origin + acquired_via_marketing), `dim_product`,
`dim_date` (macro date spine), `fct_order_items` (grain: order line), `fct_orders` (grain: order — item
value, freight, payments, reconciliation_status, delivery days, late flag, latest review score),
`mart_seller_acquisition` (by origin: leads, deals, conversion, sellers-with-sales, deals-won-after-window,
gross sales, sales/deal), `mart_monthly_kpis` (is_partial_month flag).

---

## 4. Incremental load design

- `load_raw.py` lands CSVs **unchanged** into raw tables + metadata columns `_source_system`,
  `_source_file`, `_loaded_at`, `_row_hash` (hash of all source columns). Re-load is **idempotent**: skip
  rows whose `_row_hash` already exists. Raw keeps **every version**.
- **Batch 1** = AS-OF snapshot at `2018-01-01`: only pre-cutoff orders; post-cutoff timestamps blanked;
  status derived as-of (delivered-after-cutoff → shipped/processing).
- **Batch 2** = full load at `2018-10-18`: new orders + changed orders as **new versions** (new `_row_hash`)
  → a real CDC feed.
- Integration incrementals append only; satellites add a row only when `hashdiff` changes → status changes
  between batches become new `sat_order_status` rows. `dim_date` via macro.
- Portability: all dialect-specific SQL in `macros/portable.sql` (duckdb/snowflake/bigquery branches);
  models call macros only.

---

## 5. Tests

- **schema.yml:** unique + not_null on every hash key; relationships links→hubs and facts→dims;
  accepted_values for order_status, reconciliation_status, record_source.
- **Singular:** payments reconcile (warn), no delivery-before-purchase, every closed deal traces to a lead,
  satellite no duplicate (key, hashdiff), fct_orders item value = sum of fct_order_items, partial months
  flagged (warn).
- **Rebuild check** (`compare_full_refresh.py`): snapshot warehouse after batch 2, `dbt build
  --full-refresh` on a copy, compare **full table contents** of every model → must say "identical"; any
  diff → fix the model, never weaken the check.
- **pytest** (load_raw.py): idempotent re-load, as-of masking, row hashing.

---

## 6. Deliverables (maps to Steps 3,9,10,12)

Ingestion scripts; dbt project (staging/integration/marts) + tests; rebuild check; `docs/data_sources.md`,
`docs/data_quality_report.md`, `docs/data_dictionary.md`, `docs/lineage.md` (dbt docs + Mermaid),
`docs/dashboard_guide.md`, `docs/memo.md`; `outputs/dashboard/` CSV extracts; `README.md` (DuckDB +
Snowflake run steps); `AI_USE.md` (bugs caught, anything unverified); Makefile (`make all` end-to-end on
DuckDB); final summary + resume entry.

---

## 7. Risks and fallbacks

| Risk | Fallback |
|---|---|
| **Git ownership conflict** (spec wants commits; push-to-github skill forbids me running git) | **Your decision — see question below.** Default I recommend: I run **local** git (init + milestone commits) only, and still **never push** — push stays a command block you run. |
| **No Kaggle token** (`~/.kaggle/kaggle.json` missing) | `download_data.py` detects this and prints exact manual download steps; build proceeds once CSVs are in `data/raw/`. I cannot download the data myself without the token or the files. |
| **dbt / duckdb not installed; default python is 3.8** | I'll create a **python3.12** venv (3.12.6 is present ✓) and pin dbt-core, dbt-duckdb, duckdb, pandas, etc. in requirements.txt during setup. |
| **Snowflake** needs your trial account | Everything is proven on DuckDB first; Snowflake (Step 10) is run only when you provide credentials. Won't claim Snowflake works until it actually runs. |
| **`gh` not installed** (for PRs at push time) | push-to-github block will offer the browser route as well as `gh`. |
| Rebuild not identical (e.g. history lost on refresh) | Root-cause and fix the model (likely satellites reading latest instead of history); log the bug in AI_USE.md. |

---

## 8. Out of scope

Geolocation dataset (unless a model needs it); building the actual Tableau/Power BI file (I deliver extracts
+ a build guide); orchestration tooling (Airflow/Dagster); CI/CD; real-time streaming; any non-Olist data.

---

## 9. Build order (after approval)

1. Setup: python3.12 venv + requirements, config.yaml, Makefile, dbt scaffolding, `.env.example`. *(milestone commit)*
2. Data: `download_data.py` (+ manual fallback) → you place CSVs → `docs/data_sources.md` with checksums/counts.
3. Profiling → `docs/data_quality_report.md`. *(milestone)*
4. `load_raw.py` + pytest; run batch 1. *(milestone)*
5. dbt staging + integration + marts + macros. *(milestone)*
6. dbt tests + run batch 2 + `compare_full_refresh.py` until identical. *(milestone)*
7. Analysis, docs, dashboard extracts, memo, README, lineage. *(milestone)*
8. `make all` from clean + final summary + resume entry. *(milestone)*
9. Snowflake steps documented; run only with your credentials.

Each coding unit: **coder (Opus 4.8) → verifier (Opus 5.5)**, loop until it passes.
