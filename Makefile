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

.PHONY: setup download load-batch1 build load-batch2 rebuild-check test docs all clean

# Create the venv and install deps. Installs certifi first and points
# SSL_CERT_FILE at it so build-time downloads (dbt's experimental parser) verify
# TLS on python.org framework Python.
setup:
	python3.12 -m venv $(VENV)
	$(PIP) install --upgrade pip setuptools wheel
	$(PIP) install certifi
	SSL_CERT_FILE="$$($(PY) -m certifi)" REQUESTS_CA_BUNDLE="$$($(PY) -m certifi)" $(PIP) install -r requirements.txt
	cd olist_dbt && SSL_CERT_FILE="$$(../$(PY) -m certifi)" $(DBT) deps

download:
	$(PY) scripts/download_data.py

load-batch1:
	$(PY) scripts/load_raw.py --batch 1

build:
	cd olist_dbt && $(DBT) build --target duckdb

load-batch2:
	$(PY) scripts/load_raw.py --batch 2

rebuild-check:
	$(PY) scripts/compare_full_refresh.py

test:
	$(PY) -m pytest tests/ -q
	cd olist_dbt && $(DBT) test --target duckdb

docs:
	cd olist_dbt && $(DBT) docs generate --target duckdb

# Full end-to-end run on DuckDB.
all: setup download load-batch1 build load-batch2 rebuild-check test docs

clean:
	rm -f $(DUCKDB_PATH)
	rm -rf olist_dbt/target olist_dbt/dbt_packages olist_dbt/logs logs
