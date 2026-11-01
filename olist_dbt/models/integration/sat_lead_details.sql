-- Satellite: lead details (hangs off hub_lead).
-- Change-capturing: reads EVERY landed version from raw.marketing_qualified_leads
-- (staging exposes only one version and no _loaded_at), applies the same
-- standardization staging uses for the tracked attributes, and keeps a row only
-- when a version's hashdiff differs from the immediately preceding version's
-- hashdiff for that lead (consecutive identical versions collapsed).
-- load_ts = that version's own _loaded_at. This makes a full refresh reproduce
-- the same history and keeps the model rebuild-safe.
--
-- Tracked attributes: origin, first_contact_date, landing_page_id.
-- Insert-only incremental, guarded on the composite (lead_hk, load_ts).

with versions as (

    select
        trim(mql_id)                                            as mql_id,
        {{ to_timestamp('_loaded_at') }}                        as load_ts,
        coalesce(nullif(lower(trim(origin)), ''), 'unknown')    as origin,
        {{ to_timestamp('first_contact_date') }}                as first_contact_date,
        nullif(trim(landing_page_id), '')                       as landing_page_id
    from {{ source('raw', 'marketing_qualified_leads') }}
    where mql_id is not null
      and trim(mql_id) <> ''

),

hashed as (

    select
        *,
        {{ hash_key([
            'origin',
            'first_contact_date',
            'landing_page_id'
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
        origin,
        first_contact_date,
        landing_page_id,
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
