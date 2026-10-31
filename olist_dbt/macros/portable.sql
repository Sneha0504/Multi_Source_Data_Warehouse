{#-
  Portable SQL macros.

  All database-specific SQL lives here, branching on target.type for 'duckdb',
  'snowflake' and 'bigquery'. Models must call ONLY these macros and never use a
  dialect-specific function directly, so the same models run on every adapter.
  Unknown adapters raise a clear compile-time error.
-#}


{% macro string_type() %}
  {#- The adapter's variable-length string type (used by casts in other macros). -#}
  {%- if target.type in ['duckdb', 'snowflake'] -%}
    varchar
  {%- elif target.type == 'bigquery' -%}
    string
  {%- else -%}
    {{ exceptions.raise_compiler_error("string_type: unsupported adapter '" ~ target.type ~ "'") }}
  {%- endif -%}
{% endmacro %}


{% macro hash_key(cols) %}
  {#-
    Data-Vault-style hash. Takes a list of column expressions and returns the
    md5 of their normalized, concatenated values. Used for BOTH hub/link
    business keys and satellite hashdiffs (pass the attribute columns for a
    hashdiff). Each column is cast to string, trimmed, upper-cased and
    coalesced to '' so NULLs and case/whitespace differences are stable.
  -#}
  {%- set parts = [] -%}
  {%- for c in cols -%}
    {%- do parts.append("upper(trim(coalesce(cast(" ~ c ~ " as " ~ string_type() ~ "), '')))") -%}
  {%- endfor -%}
  {%- if target.type in ['duckdb', 'snowflake'] -%}
    md5(concat_ws('||', {{ parts | join(', ') }}))
  {%- elif target.type == 'bigquery' -%}
    to_hex(md5(array_to_string([{{ parts | join(', ') }}], '||')))
  {%- else -%}
    {{ exceptions.raise_compiler_error("hash_key: unsupported adapter '" ~ target.type ~ "'") }}
  {%- endif -%}
{% endmacro %}


{% macro to_timestamp(col) %}
  {#- Parse a (usually string) column into a timestamp, NULL on failure. -#}
  {%- if target.type == 'duckdb' -%}
    try_cast({{ col }} as timestamp)
  {%- elif target.type == 'snowflake' -%}
    try_to_timestamp_ntz({{ col }})
  {%- elif target.type == 'bigquery' -%}
    safe_cast({{ col }} as timestamp)
  {%- else -%}
    {{ exceptions.raise_compiler_error("to_timestamp: unsupported adapter '" ~ target.type ~ "'") }}
  {%- endif -%}
{% endmacro %}


{% macro days_between(start_col, end_col) %}
  {#- Whole days from start to end (end - start). Negative if end precedes start. -#}
  {%- if target.type == 'duckdb' -%}
    date_diff('day', cast({{ start_col }} as timestamp), cast({{ end_col }} as timestamp))
  {%- elif target.type == 'snowflake' -%}
    datediff('day', {{ start_col }}, {{ end_col }})
  {%- elif target.type == 'bigquery' -%}
    date_diff(cast({{ end_col }} as date), cast({{ start_col }} as date), day)
  {%- else -%}
    {{ exceptions.raise_compiler_error("days_between: unsupported adapter '" ~ target.type ~ "'") }}
  {%- endif -%}
{% endmacro %}


{% macro to_month(col) %}
  {#- Truncate a date/timestamp to the first day of its month. -#}
  {%- if target.type == 'duckdb' -%}
    date_trunc('month', cast({{ col }} as timestamp))
  {%- elif target.type == 'snowflake' -%}
    date_trunc('month', {{ col }})
  {%- elif target.type == 'bigquery' -%}
    date_trunc(cast({{ col }} as date), month)
  {%- else -%}
    {{ exceptions.raise_compiler_error("to_month: unsupported adapter '" ~ target.type ~ "'") }}
  {%- endif -%}
{% endmacro %}


{% macro add_days(col, n) %}
  {#- Add n (integer) days to a date/timestamp, returning a date. -#}
  {%- if target.type == 'duckdb' -%}
    (cast({{ col }} as date) + {{ n }})
  {%- elif target.type == 'snowflake' -%}
    dateadd('day', {{ n }}, {{ col }})
  {%- elif target.type == 'bigquery' -%}
    date_add(cast({{ col }} as date), interval {{ n }} day)
  {%- else -%}
    {{ exceptions.raise_compiler_error("add_days: unsupported adapter '" ~ target.type ~ "'") }}
  {%- endif -%}
{% endmacro %}
