-- Staging: orders (LATEST version only).
-- Raw keeps every landed version of an order (CDC feed). We keep the most
-- recent version per order_id, ranked by _loaded_at descending, so one row
-- per order. Timestamps parsed via the portable to_timestamp macro.
-- Text convention: order_status lower-cased; ids kept as strings.

with source as (

    select * from {{ source('raw', 'orders') }}

),

ranked as (

    select
        *,
        row_number() over (
            partition by order_id
            order by _loaded_at desc
        ) as _version_rank
    from source

),

latest as (

    select * from ranked where _version_rank = 1

),

renamed as (

    select
        trim(order_id)                                  as order_id,
        trim(customer_id)                               as customer_id,
        lower(trim(order_status))                       as order_status,
        {{ to_timestamp('order_purchase_timestamp') }}      as order_purchase_timestamp,
        {{ to_timestamp('order_approved_at') }}             as order_approved_at,
        {{ to_timestamp('order_delivered_carrier_date') }}  as order_delivered_carrier_date,
        {{ to_timestamp('order_delivered_customer_date') }} as order_delivered_customer_date,
        {{ to_timestamp('order_estimated_delivery_date') }} as order_estimated_delivery_date,
        -- load timestamp of the latest version retained
        _loaded_at                                      as _loaded_at
    from latest

)

select * from renamed
