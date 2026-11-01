-- Staging: order payments. Rename and cast.
-- Grain: (order_id, payment_sequential). payment_type lower-cased for
-- consistent downstream grouping.

with source as (

    select * from {{ source('raw', 'order_payments') }}

),

renamed as (

    select
        trim(order_id)                                          as order_id,
        cast(nullif(trim(payment_sequential), '') as integer)   as payment_sequential,
        lower(trim(payment_type))                               as payment_type,
        cast(nullif(trim(payment_installments), '') as integer) as payment_installments,
        cast(nullif(trim(payment_value), '') as decimal(12, 2))      as payment_value
    from source

)

select * from renamed
