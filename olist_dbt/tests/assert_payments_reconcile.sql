-- Singular test (WARN): orders whose payments do not reconcile to the order's
-- item + freight total beyond the configured tolerance.
--
-- An order reconciles when |payment_value - total_item_value| <= tolerance.
-- Orders with no items (no_items) or no payment rows (no_payment) are excluded:
-- there is nothing to reconcile, not a mismatch. This is a WARNING, not an error
-- -- real marketplace data legitimately carries over/under payments (vouchers,
-- installment rounding, partial refunds), so we surface the count rather than
-- failing the build.

{{ config(severity='warn') }}

select
    order_id,
    total_item_value,
    payment_value,
    reconciliation_status,
    abs(payment_value - total_item_value) as recon_diff
from {{ ref('fct_orders') }}
where reconciliation_status not in ('no_items', 'no_payment')
  and payment_value is not null
  and abs(payment_value - total_item_value) > {{ var('recon_tolerance', 1.0) }}
