-- Hub: product. Business key = product_id. One row per business key.
-- first-seen load_ts = MIN(_loaded_at) across the full raw history (raw.products).
-- Insert-only incremental.

with keys as (

    select distinct product_id
    from {{ ref('stg_products') }}
    where product_id is not null
      and product_id <> ''

),

first_seen as (

    select
        trim(product_id)                    as product_id,
        min({{ to_timestamp('_loaded_at') }}) as load_ts
    from {{ source('raw', 'products') }}
    group by trim(product_id)

),

final as (

    select
        {{ hash_key(['k.product_id']) }} as product_hk,
        k.product_id,
        'ecommerce'                      as record_source,
        fs.load_ts
    from keys as k
    inner join first_seen as fs
        on k.product_id = fs.product_id

)

select * from final

{% if is_incremental() %}
where product_hk not in (select product_hk from {{ this }})
{% endif %}
