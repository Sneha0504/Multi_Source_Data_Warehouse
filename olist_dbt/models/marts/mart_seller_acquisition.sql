-- Mart: seller acquisition funnel by marketing channel (Key Question #3).
-- Grain: one row per lead origin (channel), including 'unknown'.
--
-- Business logic:
--   * leads         = marketing qualified leads with that origin.
--   * closed_deals  = closed deals whose lead has that origin (deal keyed by mql_id).
--   * conversion_rate = closed_deals / leads.
--   * sellers_with_sales = distinct sellers from that origin's deals that have at
--     least one line in fct_order_items (i.e. actually sold something).
--   * deals_won_after_order_data_ends = closed deals won after the last order in the
--     ecommerce data (won_date > max(order_purchase_timestamp)); these sellers
--     cannot show sales simply because the order window ended.
--   * gross_sales   = total item_value (price + freight) across that origin's sellers.
--   * sales_per_deal = gross_sales / closed_deals.
--
-- Note: a closed deal whose mql_id is missing from the MQL table (should not occur)
-- is attributed to 'unknown' so no deal is silently dropped.

with mql as (

    select
        mql_id,
        origin
    from {{ ref('stg_marketing_qualified_leads') }}

),

leads_per_origin as (

    select
        origin,
        count(distinct mql_id) as leads
    from mql
    group by origin

),

-- Closed deals tagged with their lead's origin.
deals as (

    select
        d.mql_id,
        d.seller_id,
        d.won_date,
        coalesce(m.origin, 'unknown') as origin
    from {{ ref('stg_closed_deals') }} as d
    left join mql as m
        on d.mql_id = m.mql_id

),

max_purchase as (

    select max(order_purchase_timestamp) as max_order_ts
    from {{ ref('fct_orders') }}

),

deals_agg as (

    select
        origin,
        count(distinct mql_id) as closed_deals,
        count(distinct case
            when won_date > (select max_order_ts from max_purchase) then mql_id
        end) as deals_won_after_order_data_ends
    from deals
    group by origin

),

-- Sales realized per seller across all their order lines.
seller_sales as (

    select
        seller_id,
        sum(item_value) as gross_sales,
        count(*)        as item_rows
    from {{ ref('fct_order_items') }}
    group by seller_id

),

-- One row per (origin, seller) so a seller is counted once within an origin.
deal_sellers as (

    select distinct
        origin,
        seller_id
    from deals
    where seller_id is not null
      and seller_id <> ''

),

origin_sales as (

    select
        ds.origin,
        count(distinct case when ss.item_rows > 0 then ds.seller_id end) as sellers_with_sales,
        sum(coalesce(ss.gross_sales, 0))                                 as gross_sales
    from deal_sellers as ds
    left join seller_sales as ss
        on ds.seller_id = ss.seller_id
    group by ds.origin

),

final as (

    select
        l.origin,
        l.leads,
        coalesce(da.closed_deals, 0)                            as closed_deals,
        cast(coalesce(da.closed_deals, 0) as decimal(18, 6))
            / nullif(l.leads, 0)                                as conversion_rate,
        coalesce(os.sellers_with_sales, 0)                      as sellers_with_sales,
        coalesce(da.deals_won_after_order_data_ends, 0)         as deals_won_after_order_data_ends,
        coalesce(os.gross_sales, 0)                             as gross_sales,
        coalesce(os.gross_sales, 0)
            / nullif(da.closed_deals, 0)                        as sales_per_deal
    from leads_per_origin as l
    left join deals_agg as da
        on l.origin = da.origin
    left join origin_sales as os
        on l.origin = os.origin

)

select * from final
