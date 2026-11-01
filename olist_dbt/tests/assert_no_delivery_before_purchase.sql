-- Singular test (ERROR): no order may be delivered to the customer before it was
-- purchased. A delivered_customer_date earlier than the purchase timestamp is a
-- causal impossibility and would corrupt delivery-time metrics, so this fails the
-- build.

{{ config(severity='error') }}

select
    order_id,
    order_purchase_timestamp,
    order_delivered_customer_date
from {{ ref('stg_orders') }}
where order_delivered_customer_date is not null
  and order_delivered_customer_date < order_purchase_timestamp
