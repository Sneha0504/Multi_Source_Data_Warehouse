-- Satellite: order status (change history).
-- Reads from stg_orders_history (EVERY landed version) so a full refresh rebuilds
-- the exact same history from raw.
--
-- Tracked attributes: order_status + the delivery lifecycle timestamps. For each
-- order the versions are ordered by _loaded_at and we emit a satellite row only
-- when a version's hashdiff differs from the immediately preceding version's
-- hashdiff (consecutive identical versions are collapsed). load_ts = that
-- version's _loaded_at.
--
-- Insert-only incremental, guarded on the composite (order_hk, load_ts): a
-- version already loaded is never reinserted.

with versions as (

    select
        order_id,
        {{ to_timestamp('_loaded_at') }}  as load_ts,
        order_status,
        order_approved_at,
        order_delivered_carrier_date,
        order_delivered_customer_date,
        order_estimated_delivery_date,
        {{ hash_key([
            'order_status',
            'order_approved_at',
            'order_delivered_carrier_date',
            'order_delivered_customer_date',
            'order_estimated_delivery_date'
        ]) }} as hashdiff
    from {{ ref('stg_orders_history') }}
    where order_id is not null
      and order_id <> ''

),

flagged as (

    select
        *,
        lag(hashdiff) over (
            partition by order_id
            order by load_ts, hashdiff
        ) as prev_hashdiff
    from versions

),

changed as (

    select *
    from flagged
    where prev_hashdiff is null
       or hashdiff <> prev_hashdiff

),

final as (

    select
        {{ hash_key(['order_id']) }}  as order_hk,
        load_ts,
        hashdiff,
        order_status,
        order_approved_at,
        order_delivered_carrier_date,
        order_delivered_customer_date,
        order_estimated_delivery_date,
        'ecommerce'                   as record_source
    from changed

)

select * from final

{% if is_incremental() %}
where not exists (
    select 1
    from {{ this }} as t
    where t.order_hk = final.order_hk
      and t.load_ts = final.load_ts
)
{% endif %}
