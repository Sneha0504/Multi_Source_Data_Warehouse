-- Staging: order items. Rename and cast.
-- Grain: (order_id, order_item_id). ids kept as strings; money as decimal;
-- shipping_limit_date parsed via portable to_timestamp macro.

with source as (

    select * from {{ source('raw', 'order_items') }}

),

renamed as (

    select
        trim(order_id)                                    as order_id,
        cast(nullif(trim(order_item_id), '') as integer)  as order_item_id,
        trim(product_id)                                  as product_id,
        trim(seller_id)                                   as seller_id,
        {{ to_timestamp('shipping_limit_date') }}         as shipping_limit_date,
        cast(nullif(trim(price), '') as decimal(12, 2))         as price,
        cast(nullif(trim(freight_value), '') as decimal(12, 2)) as freight_value
    from source

)

select * from renamed
