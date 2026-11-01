-- Singular test (ERROR): within any satellite, a given (entity_hk, hashdiff) must
-- appear at most once. The satellites collapse consecutive identical versions, so
-- the same hashed state should never be stored twice for one entity. A duplicate
-- would mean a state was re-inserted (e.g. an incremental run that disagrees with
-- a from-scratch build), so this fails the build. All three satellites are checked
-- via one UNION ALL, each tagged with its satellite name for a readable failure.

{{ config(severity='error') }}

with all_sats as (

    select 'sat_order_status' as satellite, order_hk as entity_hk, hashdiff
    from {{ ref('sat_order_status') }}

    union all

    select 'sat_lead_details' as satellite, lead_hk as entity_hk, hashdiff
    from {{ ref('sat_lead_details') }}

    union all

    select 'sat_deal_details' as satellite, lead_hk as entity_hk, hashdiff
    from {{ ref('sat_deal_details') }}

)

select
    satellite,
    entity_hk,
    hashdiff,
    count(*) as occurrences
from all_sats
group by satellite, entity_hk, hashdiff
having count(*) > 1
