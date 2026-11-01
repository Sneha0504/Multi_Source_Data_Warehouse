-- Mart: monthly KPIs. Grain: one row per purchase month.
--
-- Business logic (from fct_orders, bucketed by order_purchase_month):
--   * orders            = orders placed that month.
--   * distinct_customers = distinct people (customer_unique_id) who ordered.
--   * gross_revenue     = sum of total_item_value (price + freight).
--   * avg_order_value   = gross_revenue / orders.
--   * late_delivery_rate = late deliveries / delivered orders (orders not yet
--     delivered are excluded from the denominator).
--   * avg_review_score  = average latest review score.
--
-- is_partial_month rule: the order window is truncated at both ends, so the
-- calendar months at each end hold only a sliver of real activity (the real order
-- stream starts late Sept 2016 and ends 2018-08-31). We flag the LOW-VOLUME
-- BOUNDARY RUN at each end: walking inward from the first and last month, a month
-- is flagged while its order count stays below the threshold, and the run STOPS at
-- the first month at/above it. Interior low months are never flagged.
--   threshold      = 5% of the median monthly order count (median ~4,285 -> ~214).
--   flagged months = 2016-09 (4 orders), 2018-09 (16), 2018-10 (4).
--   NOT flagged    = ramp-up months 2016-10 (324) and 2017-01 (800), which are
--                    above the threshold, and interior 2016-12 (1 order), which is
--                    sparse but not part of either boundary run.

with orders as (

    select
        order_purchase_month,
        customer_unique_id,
        total_item_value,
        latest_review_score,
        is_late
    from {{ ref('fct_orders') }}
    where order_purchase_month is not null

),

monthly as (

    select
        order_purchase_month                                    as month_start,
        count(*)                                                as orders,
        count(distinct customer_unique_id)                      as distinct_customers,
        sum(total_item_value)                                   as gross_revenue,
        sum(case when is_late then 1 else 0 end)                as late_deliveries,
        sum(case when is_late is not null then 1 else 0 end)    as delivered_orders,
        avg(cast(latest_review_score as decimal(10, 4)))        as avg_review_score
    from orders
    group by order_purchase_month

),

-- Median monthly order count via ordered-position averaging (portable: no
-- dialect percentile/median function). For n rows the median sits at positions
-- floor((n+1)/2) and floor((n+2)/2) (same position when n is odd).
ranked as (

    select
        orders,
        row_number() over (order by orders) as position,
        count(*) over ()                     as n
    from monthly

),

median_orders as (

    select avg(cast(orders as decimal(18, 4))) as median_orders
    from ranked
    where position = floor((n + 1) / 2.0)
       or position = floor((n + 2) / 2.0)

),

-- Mark low-volume months (below 5% of the median) so boundary runs can be found.
sparse_flags as (

    select
        m.*,
        (m.orders < 0.05 * mo.median_orders) as is_sparse
    from monthly as m
    cross join median_orders as mo

),

-- For each month, count the non-sparse months before and after it. A month in a
-- leading boundary run has zero non-sparse months before it; a trailing-run month
-- has zero after it. This stops each run at the first non-sparse month.
boundary as (

    select
        *,
        sum(case when is_sparse then 0 else 1 end) over (
            order by month_start
            rows between unbounded preceding and 1 preceding
        ) as nonsparse_before,
        sum(case when is_sparse then 0 else 1 end) over (
            order by month_start
            rows between 1 following and unbounded following
        ) as nonsparse_after
    from sparse_flags

),

final as (

    select
        month_start,
        orders,
        distinct_customers,
        gross_revenue,
        gross_revenue / nullif(orders, 0)                           as avg_order_value,
        cast(late_deliveries as decimal(18, 6))
            / nullif(delivered_orders, 0)                           as late_delivery_rate,
        avg_review_score,
        (
            is_sparse
            and (coalesce(nonsparse_before, 0) = 0 or coalesce(nonsparse_after, 0) = 0)
        )                                                           as is_partial_month
    from boundary

)

select * from final
