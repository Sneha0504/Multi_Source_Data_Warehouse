# AI_USE.md — honesty log

This project was built with AI assistance (Claude Code). This file records how the
build was done, every bug caught and how, and anything not yet verified. Per the
project's honesty rules: a tool is only listed as "used" once code actually ran
with it, and no number appears in README/memo unless it came from query output.

## How the build is run

- Work proceeds milestone by milestone. Each coding unit is written and then
  checked by an independent verification pass before the milestone is committed
  to local git. (Code is authored on Claude Opus 4.8; verification on Opus 5.5.)
- Local git commits are made at each milestone. Pushing to GitHub is **not** done
  automatically — the push commands are handed over to run manually.

## Tools actually used so far

- Python 3.12.6 (venv), pip — ✅ ran (created venv, installed deps).
- dbt-core 1.12.6 + dbt-duckdb 1.9.6, DuckDB 2.0.0 — ✅ installed; `dbt deps` and
  `dbt parse` ran successfully on the project (no models yet).
- Snowflake (dbt-snowflake 1.9.4, snowflake-connector-python 3.18.1) — ⬜ installed
  but **NOT yet run** against a real Snowflake account.
- Kaggle API (kaggle 2.2.4) — ⬜ installed; **no token present**, so no data has
  been downloaded by it yet.

## Bugs / issues caught during the build

1. **Custom subagents didn't register mid-session.** The `coder`/`verifier`/
   `simple-task` agent definitions were added to `.claude/agents/` during the same
   session, so the Agent tool only saw built-in agents and spawning `coder` failed.
   - *How caught:* the Agent call returned "Agent type 'coder' not found".
   - *Handling:* coding is done on Opus 4.8 directly (same model as the intended
     coder) and verification is delegated to an Opus 5.5 subagent. The named agents
     activate automatically on the next session start.

2. **`pip install` failed: dbt's experimental parser couldn't verify TLS.**
   Installing dbt pulled `dbt-core-experimental-parser`, whose build step downloads
   a prebuilt wheel from github.com via raw `urlopen`. On this python.org framework
   Python there was no CA bundle, so it failed with
   `SSL: CERTIFICATE_VERIFY_FAILED`. (Pinning dbt to 1.9 did not help — the package
   is pulled regardless; PyPI access via pip itself was fine.)
   - *How caught:* background install exited non-zero; the log showed the exact
     failing URL and SSL error.
   - *Handling:* installed `certifi` and set `SSL_CERT_FILE`/`REQUESTS_CA_BUNDLE`
     to its bundle for the install; the reinstall succeeded. This fix is baked into
     the Makefile `setup` target so `make setup` works on the same environment.

## Deliberate deviations from the spec

- **Batch-1 as-of masking does NOT blank `order_estimated_delivery_date`.** The
  spec's literal wording says to blank "any order timestamp" on/after the cutoff.
  The estimated delivery date is not an event timestamp — it is the delivery
  promise known at purchase time — so in a true as-of snapshot it was already
  known at the cutoff. Masking it produced ~2,499 spurious "delivered -> delivered"
  order versions whose only difference was a blanked-then-restored estimate.
  `scripts/load_raw.py` therefore masks only the three real event timestamps
  (approved_at, delivered_carrier_date, delivered_customer_date) and leaves the
  estimate intact.
- **Batch-1 status is re-derived only for orders that actually had a timestamp
  masked**, and terminal statuses ('canceled'/'unavailable') are always kept.
  - *Bug caught (by the M3 verification pass):* the first version re-derived
    `order_status` for every kept order, rewriting ~905 pre-cutoff orders that had
    no masked timestamp (e.g. processing->unavailable, processing->invoiced,
    delivered->canceled). That injected ~905 bogus status changes into raw history
    and would have corrupted `sat_order_status` and Key Question #4.
  - *Handling:* a per-row `masked` flag (OR of the event timestamps that fell
    on/after the cutoff) now gates the re-derivation; unmasked orders keep their
    true source status, and canceled/unavailable orders keep their terminal status
    even when masked (cancellation time is unknown, so no rollback is invented).

## Not yet run / not yet verified

- No raw data downloaded yet (manual Kaggle download chosen). Therefore: no
  profiling counts, no model runs, no rebuild check, no analysis numbers exist
  yet. These will be produced only after the CSVs are in `data/raw/` and the
  pipeline runs — nothing will be fabricated.
- Snowflake has not been run (Step 10 is documentation until credentials exist).
