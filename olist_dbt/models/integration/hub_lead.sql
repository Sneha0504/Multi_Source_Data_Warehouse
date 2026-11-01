-- Hub: lead (marketing qualified lead). Business key = mql_id. One row per key.
-- first-seen load_ts = MIN(_loaded_at) across the full raw history.
-- record_source 'crm' (leads originate in the marketing/sales CRM).
-- Insert-only incremental.

with keys as (

    select distinct mql_id
    from {{ ref('stg_marketing_qualified_leads') }}
    where mql_id is not null
      and mql_id <> ''

),

first_seen as (

    select
        trim(mql_id)                        as mql_id,
        min({{ to_timestamp('_loaded_at') }}) as load_ts
    from {{ source('raw', 'marketing_qualified_leads') }}
    group by trim(mql_id)

),

final as (

    select
        {{ hash_key(['k.mql_id']) }} as lead_hk,
        k.mql_id,
        'crm'                        as record_source,
        fs.load_ts
    from keys as k
    inner join first_seen as fs
        on k.mql_id = fs.mql_id

)

select * from final

{% if is_incremental() %}
where lead_hk not in (select lead_hk from {{ this }})
{% endif %}
