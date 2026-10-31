# Data Sources

This file is generated and refreshed by `scripts/download_data.py`. Row counts, checksums, and date ranges below are computed from the actual files present in `data/raw/` at the last run; files that have not been downloaded yet are marked accordingly.

- **Publisher:** Olist
- **License:** CC BY-NC-SA 4.0
- **Date accessed / last cataloged:** 2026-10-31

## Acquisition routes

**Route A — Kaggle API (automated):**

1. Install the Kaggle client (included in `requirements.txt`).
2. Create an API token at <https://www.kaggle.com/settings/api> ("Create New Token") and save it to `~/.kaggle/kaggle.json` (`chmod 600 ~/.kaggle/kaggle.json`).
3. Run `python scripts/download_data.py` (or `make download`).

**Route B — Manual download:**

- Brazilian E-Commerce Public Dataset by Olist (olistbr/brazilian-ecommerce):
  1. Open <https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce>.
  2. Click **Download** (top-right of the page).
  3. Unzip the CSVs into `data/raw/`.
- Marketing Funnel by Olist (olistbr/marketing-funnel-olist):
  1. Open <https://www.kaggle.com/datasets/olistbr/marketing-funnel-olist>.
  2. Click **Download** (top-right of the page).
  3. Unzip the CSVs into `data/raw/`.

Raw files are never modified after download. The geolocation dataset is optional and intentionally not required.

## Brazilian E-Commerce Public Dataset by Olist

- **Kaggle dataset:** `olistbr/brazilian-ecommerce`
- **URL:** <https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce>
- **Publisher:** Olist
- **License:** CC BY-NC-SA 4.0

Expected files:

- `olist_customers_dataset.csv`
- `olist_orders_dataset.csv`
- `olist_order_items_dataset.csv`
- `olist_order_payments_dataset.csv`
- `olist_order_reviews_dataset.csv`
- `olist_products_dataset.csv`
- `olist_sellers_dataset.csv`
- `product_category_name_translation.csv`

| File | Row count | sha256 | Date range |
| --- | --- | --- | --- |
| `olist_customers_dataset.csv` | 99,441 | `983a422239e1712ded753b3bf9ecf47dc73f144d306029dcfa99e70a226883d2` | n/a |
| `olist_orders_dataset.csv` | 99,441 | `8df58ef3d2d7e9944010f7beecd9b75367f5588ec6e3c91cec19ae3345ef9ecf` | 2016-09-04 21:15:19 -> 2018-10-17 17:30:18 |
| `olist_order_items_dataset.csv` | 112,650 | `0bc4d068c4fe38cbb01bd90e8746e3c613fe7b4baef75fab7b0e329701c3e279` | n/a |
| `olist_order_payments_dataset.csv` | 103,886 | `4f713964f2815dbbaa40b9488268c55aac3627bfce5aa96cf58d1f3616de3cc0` | n/a |
| `olist_order_reviews_dataset.csv` | 99,224 | `012b61c7593e34f51fa614efdf802b9c7056ce6aae5307ddb93236e7cfc797d7` | n/a |
| `olist_products_dataset.csv` | 32,951 | `3e6569628a17fbc75fd206ee357b59e20364b9afa90f5b6cd5b4d624c58aa9cc` | n/a |
| `olist_sellers_dataset.csv` | 3,095 | `1f643d2b950373b85735e7794b20986f528d7a000432e7c6f9bcbb44d0846a0e` | n/a |
| `product_category_name_translation.csv` | 71 | `a81f0d1f27b27e7293f761bc79e3ce8f348ee39c4b3ed3e49bde38f478586278` | n/a |

## Marketing Funnel by Olist

- **Kaggle dataset:** `olistbr/marketing-funnel-olist`
- **URL:** <https://www.kaggle.com/datasets/olistbr/marketing-funnel-olist>
- **Publisher:** Olist
- **License:** CC BY-NC-SA 4.0

Expected files:

- `olist_marketing_qualified_leads_dataset.csv`
- `olist_closed_deals_dataset.csv`

| File | Row count | sha256 | Date range |
| --- | --- | --- | --- |
| `olist_marketing_qualified_leads_dataset.csv` | 8,000 | `5537753b7549ce4068e52e2402c29f2773c5375fc0575520ae8f3f301f666573` | 2017-06-14 00:00:00 -> 2018-05-31 00:00:00 |
| `olist_closed_deals_dataset.csv` | 842 | `1433f402d8fff00ca167579de641d9525f6e846368958c166f699ef1b0c31f3d` | 2017-12-05 02:00:00 -> 2018-11-14 18:04:19 |

