-- Satellite: closed-deal details (hangs off hub_lead).
-- A closed deal is keyed by mql_id (one deal per converted lead), so the deal
-- attributes describe the lead's conversion and hang off lead_hk.
--
-- Change-capturing: reads EVERY landed version from raw.closed_deals (staging
-- exposes only one version and no _loaded_at), applies the same standardization
-- staging uses for the tracked attributes, and keeps a row only when a version's
-- hashdiff differs from the immediately preceding version's hashdiff for that
-- deal (consecutive identical versions collapsed). load_ts = that version's own
-- _loaded_at. This makes a full refresh reproduce the same history and keeps the
-- model rebuild-safe.
--
-- Tracked attributes: business_segment, lead_type, business_type,
-- declared_monthly_revenue, won_date, plus the other deal descriptors.
-- Insert-only incremental, guarded on the composite (lead_hk, load_ts).

with versions as (

    select
        trim(mql_id)                                            as mql_id,
        {{ to_timestamp('_loaded_at') }}                        as load_ts,
        nullif(lower(trim(business_segment)), '')               as business_segment,
        nullif(lower(trim(lead_type)), '')                      as lead_type,
        nullif(lower(trim(business_type)), '')                  as business_type,
        cast(nullif(trim(declared_monthly_revenue), '') as decimal(16, 2)) as declared_monthly_revenue,
        {{ to_timestamp('won_date') }}                          as won_date,
        nullif(lower(trim(lead_behaviour_profile)), '')         as lead_behaviour_profile,
        nullif(lower(trim(has_company)), '')                    as has_company,
        nullif(lower(trim(has_gtin)), '')                       as has_gtin,
        nullif(lower(trim(average_stock)), '')                  as average_stock,
        cast(nullif(trim(declared_product_catalog_size), '') as integer) as declared_product_catalog_size
    from {{ source('raw', 'closed_deals') }}
    where mql_id is not null
      and trim(mql_id) <> ''

),

hashed as (

    select
        *,
        {{ hash_key([
            'business_segment',
            'lead_type',
            'business_type',
            'declared_monthly_revenue',
            'won_date',
            'lead_behaviour_profile',
            'has_company',
            'has_gtin',
            'average_stock',
            'declared_product_catalog_size'
        ]) }} as hashdiff
    from versions

),

flagged as (

    select
        *,
        lag(hashdiff) over (
            partition by mql_id
            order by load_ts, hashdiff
        ) as prev_hashdiff
    from hashed

),

changed as (

    select *
    from flagged
    where prev_hashdiff is null
       or hashdiff <> prev_hashdiff

),

final as (

    select
        {{ hash_key(['mql_id']) }} as lead_hk,
        load_ts,
        hashdiff,
        business_segment,
        lead_type,
        business_type,
        declared_monthly_revenue,
        won_date,
        lead_behaviour_profile,
        has_company,
        has_gtin,
        average_stock,
        declared_product_catalog_size,
        'crm'                      as record_source
    from changed

)

select * from final

{% if is_incremental() %}
where not exists (
    select 1
    from {{ this }} as t
    where t.lead_hk = final.lead_hk
      and t.load_ts = final.load_ts
)
{% endif %}
