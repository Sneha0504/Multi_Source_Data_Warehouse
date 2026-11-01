-- Fact: order items. Grain: one row per order line (order_id, order_item_id).
--
-- Business logic:
--   * Line economics come from stg_order_items (price, freight_value); the hub
--     foreign keys come from link_order_item so the fact joins cleanly to
--     dim_product / dim_seller (and dim_customer via fct_orders).
--   * item_value = price + freight_value (the full amount charged for the line).
--   * seller_id / product_id are carried through for direct analytical joins.
-- Expected grain: 112,650 rows (one per staged order line).

with items as (

    select
        order_id,
        order_item_id,
        price,
        freight_value
    from {{ ref('stg_order_items') }}

),

link as (

    select
        link_order_item_hk,
        order_hk,
        product_hk,
        seller_hk,
        order_id,
        order_item_id,
        product_id,
        seller_id
    from {{ ref('link_order_item') }}

),

final as (

    select
        l.order_hk,
        l.order_id,
        l.order_item_id,
        l.product_hk,
        l.seller_hk,
        l.product_id,
        l.seller_id,
        i.price,
        i.freight_value,
        (coalesce(i.price, 0) + coalesce(i.freight_value, 0)) as item_value
    from items as i
    inner join link as l
        on i.order_id = l.order_id
        and i.order_item_id = l.order_item_id

)

select * from final
