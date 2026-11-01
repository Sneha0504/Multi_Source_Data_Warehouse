# Data Lineage

**Project:** Multi-Source Data Warehouse (Olist e-commerce + marketing CRM)
**Author:** mishra.sn@northeastern.edu
**Generated from:** `dbt docs generate --target duckdb` (29 models, 10 sources).

## Generating and serving the docs

```bash
# From the repo root, with the dbt env exported (see README "How to run").
cd olist_dbt
dbt docs generate --target duckdb   # writes target/manifest.json + target/catalog.json
dbt docs serve --target duckdb      # serves the interactive lineage graph at http://localhost:8080
```

`dbt docs generate` succeeds and writes three artifacts into
`olist_dbt/target/`:

- `manifest.json` — every model, its config, and its full dependency graph.
- `catalog.json` — the materialized column/type catalog read back from the warehouse.
- `index.html` — the self-contained docs site (`dbt docs serve` hosts this;
  `Ctrl+C` stops the server).

The Mermaid graph below is the same dependency graph, read from `manifest.json`,
grouped by layer. It is accurate to the actual `ref()` / `source()` calls: note
that some hubs, links, and satellites read the **raw source** directly (not only
staging) so they can reconstruct first-seen load timestamps from the full version
history — those edges are drawn too.

## Layered lineage

```mermaid
flowchart LR
  classDef raw fill:#e8eef7,stroke:#4472c4,color:#1a1a1a;
  classDef stg fill:#e9f5e9,stroke:#548235,color:#1a1a1a;
  classDef intg fill:#fdf0e1,stroke:#c55a11,color:#1a1a1a;
  classDef mart fill:#f2e9f7,stroke:#7030a0,color:#1a1a1a;

  subgraph RAW["RAW sources (loaded by scripts/load_raw.py)"]
    r_customers[customers]
    r_orders[orders]
    r_order_items[order_items]
    r_order_payments[order_payments]
    r_order_reviews[order_reviews]
    r_products[products]
    r_cat[product_category_name_translation]
    r_sellers[sellers]
    r_mql[marketing_qualified_leads]
    r_deals[closed_deals]
  end

  subgraph STG["STAGING (views)"]
    s_customers[stg_customers]
    s_orders[stg_orders]
    s_orders_hist[stg_orders_history]
    s_order_items[stg_order_items]
    s_order_payments[stg_order_payments]
    s_order_reviews[stg_order_reviews]
    s_products[stg_products]
    s_sellers[stg_sellers]
    s_mql[stg_marketing_qualified_leads]
    s_deals[stg_closed_deals]
  end

  subgraph INTG["INTEGRATION (Data Vault: hubs / links / satellites, incremental)"]
    h_customer[hub_customer]
    h_order[hub_order]
    h_product[hub_product]
    h_seller[hub_seller]
    h_lead[hub_lead]
    l_order_customer[link_order_customer]
    l_order_item[link_order_item]
    l_lead_seller[link_lead_seller]
    sat_status[sat_order_status]
    sat_lead[sat_lead_details]
    sat_deal[sat_deal_details]
  end

  subgraph MARTS["MARTS (tables)"]
    d_customer[dim_customer]
    d_seller[dim_seller]
    d_product[dim_product]
    d_date[dim_date]
    f_items[fct_order_items]
    f_orders[fct_orders]
    m_acq[mart_seller_acquisition]
    m_kpis[mart_monthly_kpis]
  end

  %% raw -> staging
  r_customers --> s_customers
  r_orders --> s_orders
  r_orders --> s_orders_hist
  r_order_items --> s_order_items
  r_order_payments --> s_order_payments
  r_order_reviews --> s_order_reviews
  r_products --> s_products
  r_cat --> s_products
  r_sellers --> s_sellers
  r_mql --> s_mql
  r_deals --> s_deals

  %% raw/staging -> integration (hubs)
  r_customers --> h_customer
  s_customers --> h_customer
  s_orders_hist --> h_order
  r_products --> h_product
  s_products --> h_product
  r_sellers --> h_seller
  r_deals --> h_seller
  r_mql --> h_lead
  s_mql --> h_lead

  %% integration (links)
  s_customers --> l_order_customer
  s_orders --> l_order_customer
  s_orders_hist --> l_order_customer
  r_order_items --> l_order_item
  s_order_items --> l_order_item
  r_deals --> l_lead_seller
  s_deals --> l_lead_seller

  %% integration (satellites)
  s_orders_hist --> sat_status
  r_mql --> sat_lead
  r_deals --> sat_deal

  %% integration/staging -> marts
  h_customer --> d_customer
  l_order_customer --> d_customer
  s_customers --> d_customer

  h_seller --> d_seller
  l_lead_seller --> d_seller
  sat_lead --> d_seller
  s_sellers --> d_seller

  h_product --> d_product
  s_products --> d_product

  l_order_item --> f_items
  s_order_items --> f_items

  l_order_customer --> f_orders
  s_orders --> f_orders
  s_order_items --> f_orders
  s_order_payments --> f_orders
  s_order_reviews --> f_orders

  f_orders --> m_kpis
  f_orders --> m_acq
  f_items --> m_acq
  s_mql --> m_acq
  s_deals --> m_acq

  class r_customers,r_orders,r_order_items,r_order_payments,r_order_reviews,r_products,r_cat,r_sellers,r_mql,r_deals raw;
  class s_customers,s_orders,s_orders_hist,s_order_items,s_order_payments,s_order_reviews,s_products,s_sellers,s_mql,s_deals stg;
  class h_customer,h_order,h_product,h_seller,h_lead,l_order_customer,l_order_item,l_lead_seller,sat_status,sat_lead,sat_deal intg;
  class d_customer,d_seller,d_product,d_date,f_items,f_orders,m_acq,m_kpis mart;
```

## Layer summary

| Layer | Models | Materialization | Purpose |
| --- | --- | --- | --- |
| Raw | 10 source tables | tables (loaded by `load_raw.py`) | Untouched landing zone; every row version retained. |
| Staging | 10 `stg_*` | views | Rename, cast, trim, normalize, dedupe. `stg_orders` = latest version; `stg_orders_history` = every landed version. |
| Integration | 5 hubs, 3 links, 3 sats | incremental, insert-only | Data Vault on hashed business keys; satellites keep change history (`sat_order_status` reads full history so a rebuild is identical). |
| Marts | 4 dims, 2 facts, 2 aggregate marts | tables | Conformed reporting layer feeding the dashboards and the memo. |

**Notes on the graph.**
- `dim_date` has no upstream model — it is a generated date spine (`dbt_utils.date_spine`), so it is a standalone node.
- `hub_order`, `link_order_customer`, and `sat_order_status` read `stg_orders_history` (every version) rather than `stg_orders` (latest), which is what makes a full rebuild reproduce the identical status history.
- Hubs/links/satellites that read a raw source directly (e.g. `hub_seller`, `sat_lead_details`, `link_order_item`) do so to establish first-seen load timestamps from the complete version history.
