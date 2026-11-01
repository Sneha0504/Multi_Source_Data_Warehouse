-- Staging: sellers. Rename, cast and standardize text.
-- Text convention: id kept as string; city lower-cased; state upper-cased.

with source as (

    select * from {{ source('raw', 'sellers') }}

),

renamed as (

    select
        trim(seller_id)                                           as seller_id,
        cast(nullif(trim(seller_zip_code_prefix), '') as integer) as seller_zip_code_prefix,
        lower(trim(seller_city))                                  as seller_city,
        upper(trim(seller_state))                                 as seller_state
    from source

)

select * from renamed
