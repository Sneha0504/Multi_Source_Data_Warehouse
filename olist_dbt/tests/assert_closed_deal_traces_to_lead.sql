-- Singular test (ERROR): every closed deal must trace back to a marketing lead.
-- A closed deal is keyed by mql_id, so its mql_id must exist in hub_lead (which is
-- built from the full marketing-qualified-leads history). A deal with no matching
-- lead would be an orphaned conversion and break the acquisition funnel, so this
-- fails the build.

{{ config(severity='error') }}

select
    d.mql_id,
    d.seller_id
from {{ ref('stg_closed_deals') }} as d
left join {{ ref('hub_lead') }} as h
    on {{ hash_key(['d.mql_id']) }} = h.lead_hk
where h.lead_hk is null
