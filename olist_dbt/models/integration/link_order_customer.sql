-- Link: order <-> customer. Grain: one row per order.
-- The order carries customer_id (per-order id); we resolve it to the person
-- (customer_unique_id) by joining stg_orders -> stg_customers on customer_id.
-- first-seen load_ts = MIN(_loaded_at) of the order across the full raw history.
-- Insert-only incremental.

with orders as (

    select
        order_id,
        customer_id
    from {{ ref('stg_orders') }}
    where order_id is not null
      and order_id <> ''

),

customers as (

    -- Dedupe to exactly one row per customer_id so the lookup is 1:1 and the
    -- link grain stays one-row-per-order even if the source ever carried
    -- duplicate customer versions. Deterministic pick keeps a full refresh
    -- identical to an incremental run.
    select
        customer_id,
        customer_unique_id
    from (
        select
            customer_id,
            customer_unique_id,
            row_number() over (
                partition by customer_id
                order by customer_unique_id
            ) as _rn
        from {{ ref('stg_customers') }}
    ) deduped
    where _rn = 1

),

relationship as (

    select
        o.order_id,
        c.customer_unique_id
    from orders as o
    inner join customers as c
        on o.customer_id = c.customer_id
    where c.customer_unique_id is not null
      and c.customer_unique_id <> ''

),

first_seen as (

    select
        order_id,
        min({{ to_timestamp('_loaded_at') }}) as load_ts
    from {{ ref('stg_orders_history') }}
    group by order_id

),

final as (

    select
        {{ hash_key(['r.order_id', 'r.customer_unique_id']) }} as link_order_customer_hk,
        {{ hash_key(['r.order_id']) }}                         as order_hk,
        {{ hash_key(['r.customer_unique_id']) }}               as customer_hk,
        r.order_id,
        r.customer_unique_id,
        'ecommerce'                                            as record_source,
        fs.load_ts
    from relationship as r
    inner join first_seen as fs
        on r.order_id = fs.order_id

)

select * from final

{% if is_incremental() %}
where link_order_customer_hk not in (select link_order_customer_hk from {{ this }})
{% endif %}
