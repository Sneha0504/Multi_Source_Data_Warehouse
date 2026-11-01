-- Singular test (ERROR): fct_orders.item_value must equal the sum of the order's
-- line-item prices in fct_order_items. The two facts are built from the same
-- staged order lines, so the header roll-up and the line grain must agree. A tiny
-- epsilon absorbs decimal-accumulation noise. Orders with no line items have
-- item_value = 0 and no fct_order_items rows (coalesced sum = 0), which agrees.

{{ config(severity='error') }}

with line_totals as (

    select
        order_id,
        sum(price) as line_item_value
    from {{ ref('fct_order_items') }}
    group by order_id

)

select
    o.order_id,
    o.item_value                        as header_item_value,
    coalesce(l.line_item_value, 0)      as line_item_value,
    abs(o.item_value - coalesce(l.line_item_value, 0)) as diff
from {{ ref('fct_orders') }} as o
left join line_totals as l
    on o.order_id = l.order_id
where abs(o.item_value - coalesce(l.line_item_value, 0)) > 0.01
