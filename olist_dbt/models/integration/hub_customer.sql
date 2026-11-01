-- Hub: customer. Business key = customer_unique_id (the person, not the per-order
-- customer_id). One row per business key.
-- first-seen load_ts = MIN(_loaded_at) across the FULL raw history for that key
-- (raw.customers holds every landed version; staging does not expose _loaded_at).
-- Insert-only incremental: later runs add only business keys not already present.

with keys as (

    -- authoritative, standardized business-key set from staging
    select distinct customer_unique_id
    from {{ ref('stg_customers') }}
    where customer_unique_id is not null
      and customer_unique_id <> ''

),

first_seen as (

    -- earliest landed timestamp per key across all raw versions
    select
        trim(customer_unique_id)            as customer_unique_id,
        min({{ to_timestamp('_loaded_at') }}) as load_ts
    from {{ source('raw', 'customers') }}
    group by trim(customer_unique_id)

),

final as (

    select
        {{ hash_key(['k.customer_unique_id']) }} as customer_hk,
        k.customer_unique_id,
        'ecommerce'                              as record_source,
        fs.load_ts
    from keys as k
    inner join first_seen as fs
        on k.customer_unique_id = fs.customer_unique_id

)

select * from final

{% if is_incremental() %}
where customer_hk not in (select customer_hk from {{ this }})
{% endif %}
