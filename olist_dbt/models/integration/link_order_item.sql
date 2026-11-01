-- Link: order line. Grain: one row per (order_id, order_item_id).
-- Connects the order, product and seller that make up each line item.
-- link_hk is the hash of the GRAIN (order_id, order_item_id); the other hashes
-- are the FKs to hub_order / hub_product / hub_seller.
-- first-seen load_ts = MIN(_loaded_at) of the line across the full raw history.
-- Insert-only incremental.

with items as (

    select
        order_id,
        order_item_id,
        product_id,
        seller_id
    from {{ ref('stg_order_items') }}
    where order_id is not null
      and order_id <> ''
      and order_item_id is not null

),

first_seen as (

    select
        trim(order_id)                      as order_id,
        cast(nullif(trim(order_item_id), '') as integer) as order_item_id,
        min({{ to_timestamp('_loaded_at') }}) as load_ts
    from {{ source('raw', 'order_items') }}
    group by
        trim(order_id),
        cast(nullif(trim(order_item_id), '') as integer)

),

final as (

    select
        {{ hash_key(['i.order_id', 'i.order_item_id']) }} as link_order_item_hk,
        {{ hash_key(['i.order_id']) }}                    as order_hk,
        {{ hash_key(['i.product_id']) }}                  as product_hk,
        {{ hash_key(['i.seller_id']) }}                   as seller_hk,
        i.order_id,
        i.order_item_id,
        i.product_id,
        i.seller_id,
        'ecommerce'                                       as record_source,
        fs.load_ts
    from items as i
    inner join first_seen as fs
        on i.order_id = fs.order_id
        and i.order_item_id = fs.order_item_id

)

select * from final

{% if is_incremental() %}
where link_order_item_hk not in (select link_order_item_hk from {{ this }})
{% endif %}
