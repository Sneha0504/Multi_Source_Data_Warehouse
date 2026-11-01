-- Fact: orders. Grain: one row per order_id (99,441 orders).
--
-- Business logic:
--   * Order header from stg_orders (latest version). Money is rolled up from the
--     line items (price + freight) and compared to the payments to reconcile.
--   * reconciliation_status (tolerance = var recon_tolerance, default $1.00):
--       no_items   -> the order has no line items
--       no_payment -> the order has no payment rows
--       match      -> |payment_value - total_item_value| <= tolerance
--       overpaid   -> payment_value  >  total_item_value + tolerance
--       underpaid  -> payment_value  <  total_item_value - tolerance
--     no_items / no_payment are checked first so an empty side is never treated as
--     a mismatch.
--   * delivery_days = days from purchase to customer delivery (only when delivered).
--   * is_late = delivered after the promised calendar day, i.e.
--     cast(delivered as date) > cast(estimated as date) (only when delivered; NULL otherwise).
--   * latest_review_score = score of the most recent review for the order.
--   * customer_unique_id resolved to the person via link_order_customer.

with orders as (

    select
        order_id,
        order_status,
        order_purchase_timestamp,
        order_delivered_customer_date,
        order_estimated_delivery_date
    from {{ ref('stg_orders') }}

),

-- Item money rolled up to the order.
items_agg as (

    select
        order_id,
        sum(price)          as item_value,
        sum(freight_value)  as freight,
        count(*)            as item_count
    from {{ ref('stg_order_items') }}
    group by order_id

),

-- Payments rolled up to the order.
payments_agg as (

    select
        order_id,
        sum(payment_value)  as payment_value,
        count(*)            as payment_count
    from {{ ref('stg_order_payments') }}
    group by order_id

),

-- Most recent review per order (newest answered, then newest created).
latest_review as (

    select
        order_id,
        review_score
    from (
        select
            order_id,
            review_score,
            row_number() over (
                partition by order_id
                order by review_answer_timestamp desc nulls last,
                         review_creation_date desc nulls last,
                         review_id desc
            ) as review_rank
        from {{ ref('stg_order_reviews') }}
    ) ranked
    where review_rank = 1

),

-- The person behind the order.
order_customer as (

    select
        order_id,
        customer_unique_id
    from {{ ref('link_order_customer') }}

),

final as (

    select
        {{ hash_key(['o.order_id']) }}          as order_hk,
        o.order_id,
        oc.customer_unique_id,
        o.order_status,
        o.order_purchase_timestamp,
        {{ to_month('o.order_purchase_timestamp') }} as order_purchase_month,

        -- money
        coalesce(ia.item_value, 0)              as item_value,
        coalesce(ia.freight, 0)                 as freight,
        coalesce(ia.item_value, 0) + coalesce(ia.freight, 0) as total_item_value,
        pa.payment_value,

        -- reconciliation of payments vs item+freight
        case
            when coalesce(ia.item_count, 0) = 0 then 'no_items'
            when coalesce(pa.payment_count, 0) = 0 then 'no_payment'
            when abs(pa.payment_value - (coalesce(ia.item_value, 0) + coalesce(ia.freight, 0)))
                 <= {{ var('recon_tolerance', 1.0) }} then 'match'
            when pa.payment_value
                 > (coalesce(ia.item_value, 0) + coalesce(ia.freight, 0)) + {{ var('recon_tolerance', 1.0) }}
                 then 'overpaid'
            else 'underpaid'
        end                                     as reconciliation_status,

        -- delivery performance (only meaningful once delivered)
        case
            when o.order_delivered_customer_date is not null
                then {{ days_between('o.order_purchase_timestamp', 'o.order_delivered_customer_date') }}
        end                                     as delivery_days,
        -- "late" = delivered AFTER the promised calendar day; compare at date
        -- level so delivery any time on the promised day is on-time (the estimate
        -- is stored at midnight).
        case
            when o.order_delivered_customer_date is not null
                then (cast(o.order_delivered_customer_date as date)
                      > cast(o.order_estimated_delivery_date as date))
        end                                     as is_late,

        lr.review_score                         as latest_review_score
    from orders as o
    left join items_agg as ia
        on o.order_id = ia.order_id
    left join payments_agg as pa
        on o.order_id = pa.order_id
    left join latest_review as lr
        on o.order_id = lr.order_id
    left join order_customer as oc
        on o.order_id = oc.order_id

)

select * from final
