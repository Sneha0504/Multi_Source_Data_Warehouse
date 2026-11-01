-- Dimension: product. Grain: one row per product_id.
--
-- Business logic:
--   * hub_product holds one row per product_id with its stable hash.
--   * stg_products supplies the English category name and the translation status
--     ('missing' / 'translated' / 'untranslated') plus the physical dimensions.
--   * Products present in the hub but absent from stg_products (should not happen,
--     but kept safe) keep NULL attributes rather than being dropped.

with hub as (

    select
        product_hk,
        product_id
    from {{ ref('hub_product') }}

),

products as (

    select
        product_id,
        product_category_name_english,
        category_translation_status,
        product_weight_g,
        product_length_cm,
        product_height_cm,
        product_width_cm
    from {{ ref('stg_products') }}

),

final as (

    select
        h.product_hk,
        h.product_id,
        p.product_category_name_english     as product_category,
        p.category_translation_status,
        p.product_weight_g,
        p.product_length_cm,
        p.product_height_cm,
        p.product_width_cm
    from hub as h
    left join products as p
        on h.product_id = p.product_id

)

select * from final
