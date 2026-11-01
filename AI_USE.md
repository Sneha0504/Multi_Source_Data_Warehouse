# AI_USE.md — honesty log

This project was built with AI assistance (Claude Code). This file records how the
build was done, every bug caught and how, deliberate deviations, and anything not
run or not verified. Per the project's honesty rules: a tool is only listed as
"used" once code actually ran with it, and no number appears in the README or memo
unless it came from query output.

## How the build was run

- Work proceeded milestone by milestone. Each coding unit was written on Claude
  Opus 4.8 and then checked by an independent Opus 5.5 verification pass before the
  milestone was committed to local git. Several milestones went through a
  fix → re-verify loop (see bugs below).
- Local git commits were made at each milestone. Pushing to GitHub was **not** done
  automatically — the push commands are handed over to run manually.

## Tools actually used (ran)

- **Python 3.12.6** (venv) + pip — ✅ ran.
- **pandas, DuckDB 2.0.0** — ✅ ran (ingestion + all queries).
- **dbt-core 1.12.6 + dbt-duckdb 1.9.6, dbt_utils 1.3.0** — ✅ ran: `dbt deps`,
  `dbt build` (×2, after each batch), `dbt test`, `dbt docs generate` all ran on
  the real warehouse. `make all` is green from a clean state.
- **Git** — ✅ used for local commits at each milestone (not pushed).
- **Kaggle datasets** — ✅ present and used. The raw CSVs were obtained via a
  **manual** Kaggle download (no API token was on the machine) and placed in
  `data/raw/`; `scripts/download_data.py` ran in catalog mode over the real files
  (kaggle 2.2.4 is installed but did not perform the download).
- **Snowflake** (dbt-snowflake 1.9.4, snowflake-connector-python 3.18.1) — ⬜
  installed and the code path is written, but **NOT run** against a real account.

## Bugs / issues caught during the build

1. **Custom subagents didn't register mid-session.** The `coder`/`verifier`/
   `simple-task` agent definitions were added to `.claude/agents/` during the same
   session, so the Agent tool briefly only saw built-in agents.
   - *How caught:* an Agent call returned "Agent type 'coder' not found".
   - *Handling:* coding continued on Opus 4.8 and verification on an Opus 5.5
     subagent; the named agents registered shortly after and were used for the rest.

2. **`pip install` failed: dbt's experimental parser couldn't verify TLS.**
   Installing dbt pulled `dbt-core-experimental-parser`, whose build step downloads
   a wheel from github.com via raw `urlopen`; this python.org framework Python had
   no CA bundle, so it failed with `SSL: CERTIFICATE_VERIFY_FAILED`.
   - *How caught:* background install exited non-zero; the log showed the failing
     URL and SSL error.
   - *Handling:* installed `certifi` and set `SSL_CERT_FILE`/`REQUESTS_CA_BUNDLE`
     for the install; baked into the Makefile `setup` target so `make setup` works.

3. **Batch-1 status was rewritten for ALL pre-cutoff orders (not just masked ones).**
   The first `load_raw.py` re-derived `order_status` for every kept order, so ~905
   pre-cutoff orders with no masked timestamp got a fake status (e.g.
   processing→unavailable, processing→invoiced), injecting ~905 bogus status changes
   into raw history and corrupting the CDC story behind Key Question #4.
   - *How caught:* the M3 Opus 5.5 verification pass compared each batch-1 order to
     the true row and found 905 that differed only in status.
   - *Handling:* a per-row `masked` flag now gates re-derivation; unmasked orders
     keep their true status; terminal statuses (canceled/unavailable) are kept.
     Fixed and re-verified (0 spurious rewrites). See deviations below.

4. **The lead/deal satellites were not actually change-capturing.** `sat_lead_details`
   and `sat_deal_details` first joined each row to `MIN(_loaded_at)` with no
   per-version hashdiff/lag collapse, which violated the "new row only when
   attributes change" rule and would have broken the full-refresh rebuild check if a
   changed version ever landed.
   - *How caught:* the M5 verification pass flagged it as a latent rebuild-safety bug.
   - *Handling:* both rewritten to the `sat_order_status` per-version hashdiff + lag
     pattern; re-verified rebuild-identical (EXCEPT diffs 0/0).

5. **`is_late` counted same-day deliveries as late.** `fct_orders` compared the
   delivery timestamp against the estimated date stored at midnight, so an order
   delivered any time on the promised day was flagged late (8.11%).
   - *How caught:* the M6 verification pass recomputed both definitions.
   - *Handling:* switched to a calendar-date comparison (delivered date > promised
     date). Correct late rate is **6.77%** (6,535 / 96,476 delivered).

6. **`make clean` failed on the space in the project path.** `rm -f $(DUCKDB_PATH)`
   was unquoted and the path contains "Coding Projects".
   - *How caught:* running `make clean` before the final `make all`.
   - *Handling:* quoted to `rm -f "$(DUCKDB_PATH)"`.

7. **A single build could never pass the rebuild check.** The spec's literal `all`
   list built only once (after batch 1), so the incremental warehouse held only
   batch-1 data while the full-refresh copy held both batches; the rebuild check
   reported 15 differing relations.
   - *How caught:* running `make all` end to end.
   - *Handling:* added a **second `dbt build` after batch 2** (see deviations). After
     that, the rebuild check is IDENTICAL (29/29).

8. **Doc number precision ("2,505 changed status").** The final verification pass
   found that 2,505 orders got a second version but only 2,504 actually changed
   status (one stayed `canceled`; only a timestamp filled in). The memo and README
   were corrected to say "2,505 updated (2,504 with a changed status)".

## Deliberate deviations from the spec

- **Batch-1 masking leaves `order_estimated_delivery_date` intact.** The spec says
  blank "any order timestamp" on/after the cutoff, but the estimate is the delivery
  promise known at purchase time, so in a true as-of snapshot it was already known.
  Masking it produced ~2,499 spurious "delivered → delivered" versions differing
  only by a blanked-then-restored estimate. Only the three real event timestamps
  (approved_at, delivered_carrier_date, delivered_customer_date) are masked.
- **Batch-1 status is re-derived only for masked orders**, and terminal statuses
  (canceled/unavailable) are kept even when masked (the cancellation time is
  unknown, so no rollback is invented). See bug #3.
- **`make all` runs `dbt build` twice** (after each batch), unlike Step 8's single
  build in its `all` list. This is required for the insert-only integration layer to
  accumulate both batches and match a from-scratch full-refresh (Step 7/12's
  identical requirement, and Step 5/Step 10's two-load design). See bug #7.

## Verified end state

- `make all` runs green from a clean state: two loads, two builds, rebuild check
  **IDENTICAL (29/29 models)**, `dbt test` **101 pass / 1 warn / 0 error**, pytest
  **10 passed**, docs generated.
- The single dbt-test warning is `assert_payments_reconcile` = 249 rows
  (232 overpaid + 17 underpaid), surfaced by design (severity warn), not a failure.

## Not run / not verified

- **Snowflake has not been run.** The project is written and configured for it
  (dbt-snowflake target, `profiles.yml.example`, `load_raw.py --target snowflake`,
  README Step-10 steps), but no Snowflake run, numbers, or "works on Snowflake"
  claim is made. It will only be true once the Step-10 steps are actually executed.
- No other external services were used.
