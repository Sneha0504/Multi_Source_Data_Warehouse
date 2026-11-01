-- Dimension: seller. Grain: one row per seller_id.
--
-- Business logic:
--   * hub_seller is fed by BOTH systems, so it includes CRM-only sellers that were
--     won as closed deals but never appeared in the ecommerce seller catalog
--     (~462 of them). Those have no stg_sellers row, so city/state stay NULL — we
--     keep the seller rather than dropping it. record_source carries the system
--     that first saw the seller.
--   * acquired_via_marketing = the seller_id appears in link_lead_seller (i.e. it
--     came from a closed marketing deal).
--   * lead_origin = the marketing origin of the acquiring lead, read from the
--     latest sat_lead_details version via link_lead_seller. If a seller maps to
--     more than one lead, pick deterministically (alphabetical origin).

with hub as (

    select
        seller_hk,
        seller_id,
        record_source
    from {{ ref('hub_seller') }}

),

-- City/state from the ecommerce seller catalog (one row per seller_id).
seller_geo as (

    select
        seller_id,
        seller_city,
        seller_state
    from {{ ref('stg_sellers') }}

),

-- Latest known origin per lead (sat_lead_details keeps change history).
lead_origin_latest as (

    select
        lead_hk,
        origin
    from (
        select
            lead_hk,
            origin,
            row_number() over (
                partition by lead_hk
                order by load_ts desc, origin asc
            ) as version_rank
        from {{ ref('sat_lead_details') }}
    ) ranked
    where version_rank = 1

),

-- Resolve one origin per seller via the cross-system lead<->seller link.
seller_origin as (

    select
        seller_hk,
        origin
    from (
        select
            ll.seller_hk,
            lo.origin,
            row_number() over (
                partition by ll.seller_hk
                order by lo.origin asc
            ) as origin_rank
        from {{ ref('link_lead_seller') }} as ll
        left join lead_origin_latest as lo
            on ll.lead_hk = lo.lead_hk
    ) ranked
    where origin_rank = 1

),

-- Sellers acquired via marketing = present in the lead<->seller link.
marketing_sellers as (

    select distinct seller_hk
    from {{ ref('link_lead_seller') }}

),

final as (

    select
        h.seller_hk,
        h.seller_id,
        g.seller_city                           as city,
        g.seller_state                          as state,
        so.origin                               as lead_origin,
        (m.seller_hk is not null)               as acquired_via_marketing,
        h.record_source
    from hub as h
    left join seller_geo as g
        on h.seller_id = g.seller_id
    left join seller_origin as so
        on h.seller_hk = so.seller_hk
    left join marketing_sellers as m
        on h.seller_hk = m.seller_hk

)

select * from final
