-- Staging: customers. Rename, cast and standardize text.
-- Text convention: ids kept as strings (trimmed); city lower-cased;
-- state upper-cased (2-letter code).

with source as (

    select * from {{ source('raw', 'customers') }}

),

renamed as (

    select
        trim(customer_id)                                       as customer_id,
        trim(customer_unique_id)                                as customer_unique_id,
        cast(nullif(trim(customer_zip_code_prefix), '') as integer) as customer_zip_code_prefix,
        lower(trim(customer_city))                              as customer_city,
        upper(trim(customer_state))                             as customer_state
    from source

)

select * from renamed
