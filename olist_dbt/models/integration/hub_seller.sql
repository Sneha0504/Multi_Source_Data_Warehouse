-- Hub: seller. Business key = seller_id. FED BY BOTH SYSTEMS so it also captures
-- sellers that exist only in the CRM (closed deals) and never appear in the
-- ecommerce seller catalog.
--
-- Seller "events" (seller_id + load timestamp + system) are unioned from:
--   * raw.sellers      -> system 'ecommerce'
--   * raw.closed_deals -> system 'crm'
-- We use the raw tables (not the stg views) because staging does not expose
-- _loaded_at and the first-seen rule requires the full landed history; seller_id
-- is trimmed to match the staging standardization.
--
-- For each seller_id:
--   load_ts       = MIN(_loaded_at) across both systems (first-seen).
--   record_source = the system of the EARLIEST occurrence; ties resolve to
--                   'ecommerce' (sort key puts ecommerce before crm).
-- Insert-only incremental.

with seller_events as (

    select
        trim(seller_id)                 as seller_id,
        'ecommerce'                     as system,
        {{ to_timestamp('_loaded_at') }} as loaded_at
    from {{ source('raw', 'sellers') }}
    where seller_id is not null
      and trim(seller_id) <> ''

    union all

    select
        trim(seller_id)                 as seller_id,
        'crm'                           as system,
        {{ to_timestamp('_loaded_at') }} as loaded_at
    from {{ source('raw', 'closed_deals') }}
    where seller_id is not null
      and trim(seller_id) <> ''

),

first_seen as (

    select
        seller_id,
        min(loaded_at) as load_ts
    from seller_events
    group by seller_id

),

earliest_system as (

    -- the system of the earliest event per seller; tie -> ecommerce
    select
        seller_id,
        system as record_source
    from (
        select
            seller_id,
            system,
            row_number() over (
                partition by seller_id
                order by
                    loaded_at asc,
                    case when system = 'ecommerce' then 0 else 1 end
            ) as rn
        from seller_events
    ) ranked
    where rn = 1

),

final as (

    select
        {{ hash_key(['fs.seller_id']) }} as seller_hk,
        fs.seller_id,
        es.record_source,
        fs.load_ts
    from first_seen as fs
    inner join earliest_system as es
        on fs.seller_id = es.seller_id

)

select * from final

{% if is_incremental() %}
where seller_hk not in (select seller_hk from {{ this }})
{% endif %}
