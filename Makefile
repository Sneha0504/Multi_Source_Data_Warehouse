# Multi-Source Data Warehouse — end-to-end on DuckDB.
# `make all` runs the whole pipeline (assumes the raw CSVs are already in
# data/raw/ — see `make download`). Targets use the project-local venv.

VENV := .venv
PY   := $(VENV)/bin/python
PIP  := $(VENV)/bin/pip
DBT  := ../$(VENV)/bin/dbt

# Single absolute DuckDB file shared by the ingestion scripts AND dbt (which
# runs from olist_dbt/). Keeping it absolute avoids two different files.
export DUCKDB_PATH := $(CURDIR)/olist_warehouse.duckdb

# Local dbt profiles directory. `setup` generates olist_dbt/profiles.yml (which
# is git-ignored) from profiles.yml.example, so the 'olist' profile is found
# WITHOUT any manual ~/.dbt setup. Exported so every dbt invocation below — and
# the child `dbt build` spawned by scripts/compare_full_refresh.py, which
# inherits the environment — reads the profile from olist_dbt/ instead of
# ~/.dbt/profiles.yml. (The README's Snowflake section still uses ~/.dbt.)
export DBT_PROFILES_DIR := $(CURDIR)/olist_dbt

.PHONY: setup download load-batch1 build load-batch2 rebuild-check test docs dashboard all clean

# Create the venv and install deps. Installs certifi first and points
# SSL_CERT_FILE at it so build-time downloads (dbt's experimental parser) verify
# TLS on python.org framework Python. Finally generates the local dbt profile by
# always overwriting olist_dbt/profiles.yml from the committed example, so the
# DuckDB run needs no manual profiles setup.
setup:
	python3.12 -m venv $(VENV)
	$(PIP) install --upgrade pip setuptools wheel
	$(PIP) install certifi
	SSL_CERT_FILE="$$($(PY) -m certifi)" REQUESTS_CA_BUNDLE="$$($(PY) -m certifi)" $(PIP) install -r requirements.txt
	cp olist_dbt/profiles.yml.example olist_dbt/profiles.yml
	cd olist_dbt && SSL_CERT_FILE="$$(../$(PY) -m certifi)" $(DBT) deps

download:
	$(PY) scripts/download_data.py

load-batch1:
	$(PY) scripts/load_raw.py --batch 1

build:
	cd olist_dbt && SSL_CERT_FILE="$$(../$(PY) -m certifi)" $(DBT) build --target duckdb

load-batch2:
	$(PY) scripts/load_raw.py --batch 2

rebuild-check:
	$(PY) scripts/compare_full_refresh.py

test:
	$(PY) -m pytest tests/ -q
	cd olist_dbt && SSL_CERT_FILE="$$(../$(PY) -m certifi)" $(DBT) test --target duckdb

docs:
	cd olist_dbt && SSL_CERT_FILE="$$(../$(PY) -m certifi)" $(DBT) docs generate --target duckdb

# Convenience: refresh the dashboard CSV extracts against the built warehouse.
# NOT part of `all` (run it after `make all`).
dashboard:
	$(PY) scripts/export_dashboard.py

# Full end-to-end run on DuckDB.
#
# Order follows the spec (setup, download, load-batch1, build, load-batch2,
# build, rebuild-check, test, docs). `build` runs TWICE on purpose -- once after
# each batch load -- because the integration layer is insert-only/incremental: a
# build after batch 1 and a build after batch 2 accumulate history exactly as a
# single `dbt build --full-refresh` would, which is what `rebuild-check` asserts.
# (Without the second build the incremental warehouse would reflect only batch 1
# while the rebuild copy sees both batches, so the check would never be
# identical.) Each step is a separate sub-make because make builds any one target
# at most once per invocation, so listing `build` twice as a prerequisite would
# not re-run it.
all:
	$(MAKE) setup
	$(MAKE) download
	$(MAKE) load-batch1
	$(MAKE) build
	$(MAKE) load-batch2
	$(MAKE) build
	$(MAKE) rebuild-check
	$(MAKE) test
	$(MAKE) docs

clean:
	rm -f "$(DUCKDB_PATH)"
	rm -rf olist_dbt/target olist_dbt/dbt_packages olist_dbt/logs logs
