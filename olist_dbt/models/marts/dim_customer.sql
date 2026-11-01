-- Dimension: customer. Grain: one row per person (customer_unique_id).
--
-- Business logic:
--   * The ecommerce source keys orders by a per-order customer_id, but the real
--     person is customer_unique_id (see Key Question #2). hub_customer already
--     holds one row per customer_unique_id with its stable hash and record_source.
--   * A person can appear under many per-order customer_id rows, each carrying a
--     city/state. We pick ONE representative city/state deterministically: the
--     most frequent (city, state) pair for that person, ties broken alphabetically
--     by city then state. This keeps a full refresh identical to incremental runs.
--   * orders_count = distinct orders tied to the person via link_order_customer.
--   * is_repeat_customer = orders_count > 1.

with hub as (

    select
        customer_hk,
        customer_unique_id
    from {{ ref('hub_customer') }}

),

-- Count each (person, city, state) combination so we can pick the dominant one.
geo_counts as (

    select
        customer_unique_id,
        customer_city,
        customer_state,
        count(*) as location_frequency
    from {{ ref('stg_customers') }}
    group by
        customer_unique_id,
        customer_city,
        customer_state

),

-- Deterministic representative location: most frequent, then alphabetical.
geo_ranked as (

    select
        customer_unique_id,
        customer_city,
        customer_state,
        row_number() over (
            partition by customer_unique_id
            order by location_frequency desc, customer_city asc, customer_state asc
        ) as location_rank
    from geo_counts

),

representative_geo as (

    select
        customer_unique_id,
        customer_city,
        customer_state
    from geo_ranked
    where location_rank = 1

),

-- Distinct orders per person through the order<->customer link.
orders_per_person as (

    select
        customer_unique_id,
        count(distinct order_id) as orders_count
    from {{ ref('link_order_customer') }}
    group by customer_unique_id

),

final as (

    select
        h.customer_hk,
        h.customer_unique_id,
        g.customer_city                     as city,
        g.customer_state                    as state,
        coalesce(o.orders_count, 0)         as orders_count,
        coalesce(o.orders_count, 0) > 1     as is_repeat_customer
    from hub as h
    left join representative_geo as g
        on h.customer_unique_id = g.customer_unique_id
    left join orders_per_person as o
        on h.customer_unique_id = o.customer_unique_id

)

select * from final
