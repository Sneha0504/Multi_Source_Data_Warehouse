-- Staging: orders history (EVERY landed version, no dedupe).
-- The downstream Data Vault satellite sat_order_status reads from here so a
-- full refresh reproduces the complete change history. _row_hash is kept so
-- the satellite can detect attribute changes across versions.
-- Text convention: order_status lower-cased; ids kept as strings.

with source as (

    select * from {{ source('raw', 'orders') }}

),

renamed as (

    select
        trim(order_id)                                  as order_id,
        lower(trim(order_status))                       as order_status,
        {{ to_timestamp('order_purchase_timestamp') }}      as order_purchase_timestamp,
        {{ to_timestamp('order_approved_at') }}             as order_approved_at,
        {{ to_timestamp('order_delivered_carrier_date') }}  as order_delivered_carrier_date,
        {{ to_timestamp('order_delivered_customer_date') }} as order_delivered_customer_date,
        {{ to_timestamp('order_estimated_delivery_date') }} as order_estimated_delivery_date,
        _loaded_at                                      as _loaded_at,
        _row_hash                                       as _row_hash
    from source

)

select * from renamed
