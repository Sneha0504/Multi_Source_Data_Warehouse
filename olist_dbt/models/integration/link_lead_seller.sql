-- Link: lead <-> seller (CROSS-SYSTEM: CRM lead to ecommerce/CRM seller).
-- Grain: one row per (mql_id, seller_id) from closed deals that have a seller_id.
-- FKs lead_hk -> hub_lead, seller_hk -> hub_seller.
-- first-seen load_ts = MIN(_loaded_at) across the full raw history.
-- Insert-only incremental.

with deals as (

    select distinct
        mql_id,
        seller_id
    from {{ ref('stg_closed_deals') }}
    where mql_id is not null
      and mql_id <> ''
      and seller_id is not null
      and seller_id <> ''

),

first_seen as (

    select
        trim(mql_id)                        as mql_id,
        trim(seller_id)                     as seller_id,
        min({{ to_timestamp('_loaded_at') }}) as load_ts
    from {{ source('raw', 'closed_deals') }}
    where trim(mql_id) <> ''
      and trim(seller_id) <> ''
    group by
        trim(mql_id),
        trim(seller_id)

),

final as (

    select
        {{ hash_key(['d.mql_id', 'd.seller_id']) }} as link_lead_seller_hk,
        {{ hash_key(['d.mql_id']) }}                as lead_hk,
        {{ hash_key(['d.seller_id']) }}             as seller_hk,
        d.mql_id,
        d.seller_id,
        'crm'                                       as record_source,
        fs.load_ts
    from deals as d
    inner join first_seen as fs
        on d.mql_id = fs.mql_id
        and d.seller_id = fs.seller_id

)

select * from final

{% if is_incremental() %}
where link_lead_seller_hk not in (select link_lead_seller_hk from {{ this }})
{% endif %}
