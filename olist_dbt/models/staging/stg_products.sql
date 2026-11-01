-- Staging: products, joined to the category-name translation lookup.
-- Products load full (one version), but we dedupe on product_id (latest by
-- _loaded_at) to be safe. category_translation_status classifies each product:
--   'missing'      -> no/blank Portuguese category
--   'translated'   -> category present AND an English translation exists
--   'untranslated' -> category present but no translation row/value
-- Category names are lower-cased for consistent joins/grouping.

with products_source as (

    select * from {{ source('raw', 'products') }}

),

translation_source as (

    select
        lower(trim(product_category_name))         as product_category_name,
        nullif(lower(trim(product_category_name_english)), '') as product_category_name_english
    from {{ source('raw', 'product_category_name_translation') }}

),

products_ranked as (

    select
        *,
        row_number() over (
            partition by product_id
            order by _loaded_at desc
        ) as _version_rank
    from products_source

),

products_latest as (

    select * from products_ranked where _version_rank = 1

),

renamed as (

    select
        trim(product_id)                                        as product_id,
        nullif(lower(trim(product_category_name)), '')          as product_category_name,
        cast(nullif(trim(product_name_lenght), '') as integer)       as product_name_length,
        cast(nullif(trim(product_description_lenght), '') as integer) as product_description_length,
        cast(nullif(trim(product_photos_qty), '') as integer)        as product_photos_qty,
        cast(nullif(trim(product_weight_g), '') as decimal(12, 2))    as product_weight_g,
        cast(nullif(trim(product_length_cm), '') as decimal(12, 2))   as product_length_cm,
        cast(nullif(trim(product_height_cm), '') as decimal(12, 2))   as product_height_cm,
        cast(nullif(trim(product_width_cm), '') as decimal(12, 2))    as product_width_cm
    from products_latest

),

joined as (

    select
        r.product_id,
        r.product_category_name,
        t.product_category_name_english,
        -- translation status (see header)
        case
            when r.product_category_name is null then 'missing'
            when t.product_category_name_english is not null then 'translated'
            else 'untranslated'
        end as category_translation_status,
        r.product_name_length,
        r.product_description_length,
        r.product_photos_qty,
        r.product_weight_g,
        r.product_length_cm,
        r.product_height_cm,
        r.product_width_cm
    from renamed as r
    left join translation_source as t
        on r.product_category_name = t.product_category_name

)

select * from joined
