-- Singular test (WARN): the order window is truncated at both ends, so the first
-- and last months in the series hold only a sliver of real activity. Such a
-- boundary month with very low volume should be marked is_partial_month so
-- downstream charts can exclude it. This is a sanity check on that flag: it warns
-- if the earliest or latest month has very low volume (< 5% of the busiest
-- month's orders) yet is NOT flagged as partial. WARN, not error -- the flagging
-- heuristic is a convenience, not a hard data-integrity rule.

{{ config(severity='warn') }}

with kpis as (

    select
        month_start,
        orders,
        is_partial_month
    from {{ ref('mart_monthly_kpis') }}

),

bounds as (

    select
        min(month_start) as first_month,
        max(month_start) as last_month,
        0.05 * max(orders) as low_volume_threshold
    from kpis

)

select
    k.month_start,
    k.orders,
    k.is_partial_month
from kpis as k
cross join bounds as b
where k.month_start in (b.first_month, b.last_month)
  and k.orders < b.low_volume_threshold
  and not k.is_partial_month
