-- Staging: marketing qualified leads (funnel entry).
-- origin is lower-cased and blank/empty values normalized to 'unknown' so the
-- marketing funnel has no empty channel. first_contact_date parsed via the
-- portable to_timestamp macro.

with source as (

    select * from {{ source('raw', 'marketing_qualified_leads') }}

),

renamed as (

    select
        trim(mql_id)                                    as mql_id,
        {{ to_timestamp('first_contact_date') }}        as first_contact_date,
        nullif(trim(landing_page_id), '')               as landing_page_id,
        -- normalize blank/empty origin to 'unknown'
        coalesce(nullif(lower(trim(origin)), ''), 'unknown') as origin
    from source

)

select * from renamed
