-- Hub: order. Business key = order_id. One row per business key.
-- Sourced from stg_orders_history (EVERY landed version) so first-seen load_ts =
-- MIN(_loaded_at) across all versions (an order first seen in batch 1 keeps its
-- batch-1 timestamp even though its latest version landed in batch 2).
-- Insert-only incremental.

with first_seen as (

    select
        order_id,
        min({{ to_timestamp('_loaded_at') }}) as load_ts
    from {{ ref('stg_orders_history') }}
    where order_id is not null
      and order_id <> ''
    group by order_id

),

final as (

    select
        {{ hash_key(['order_id']) }} as order_hk,
        order_id,
        'ecommerce'                  as record_source,
        load_ts
    from first_seen

)

select * from final

{% if is_incremental() %}
where order_hk not in (select order_hk from {{ this }})
{% endif %}
