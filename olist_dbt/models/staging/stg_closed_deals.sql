-- Staging: closed deals (leads that converted into sellers).
-- Links a lead (mql_id) to a seller (seller_id). Categorical fields are
-- lower-cased for consistent grouping; won_date parsed via portable
-- to_timestamp macro; declared revenue/catalog size cast to numeric.

with source as (

    select * from {{ source('raw', 'closed_deals') }}

),

renamed as (

    select
        trim(mql_id)                                            as mql_id,
        trim(seller_id)                                         as seller_id,
        nullif(trim(sdr_id), '')                                as sdr_id,
        nullif(trim(sr_id), '')                                 as sr_id,
        {{ to_timestamp('won_date') }}                          as won_date,
        nullif(lower(trim(business_segment)), '')               as business_segment,
        nullif(lower(trim(lead_type)), '')                      as lead_type,
        nullif(lower(trim(lead_behaviour_profile)), '')         as lead_behaviour_profile,
        nullif(lower(trim(has_company)), '')                    as has_company,
        nullif(lower(trim(has_gtin)), '')                       as has_gtin,
        nullif(lower(trim(average_stock)), '')                  as average_stock,
        nullif(lower(trim(business_type)), '')                  as business_type,
        cast(nullif(trim(declared_product_catalog_size), '') as integer)   as declared_product_catalog_size,
        cast(nullif(trim(declared_monthly_revenue), '') as decimal(16, 2))  as declared_monthly_revenue
    from source

)

select * from renamed
