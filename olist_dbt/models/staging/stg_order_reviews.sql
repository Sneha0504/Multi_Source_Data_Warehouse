-- Staging: order reviews (DEDUPED to one row per (review_id, order_id)).
-- Raw may hold multiple versions of the same review across batches. Keep the
-- latest, ranked by _loaded_at desc then review_answer_timestamp desc (newest
-- answer wins ties). Timestamps parsed via portable to_timestamp macro.
-- Comment title/message are trimmed and blanks normalized to NULL.

with source as (

    select * from {{ source('raw', 'order_reviews') }}

),

ranked as (

    select
        *,
        row_number() over (
            partition by review_id, order_id
            order by _loaded_at desc, {{ to_timestamp('review_answer_timestamp') }} desc
        ) as _version_rank
    from source

),

latest as (

    select * from ranked where _version_rank = 1

),

renamed as (

    select
        trim(review_id)                                     as review_id,
        trim(order_id)                                      as order_id,
        cast(nullif(trim(review_score), '') as integer)     as review_score,
        nullif(trim(review_comment_title), '')              as review_comment_title,
        nullif(trim(review_comment_message), '')            as review_comment_message,
        {{ to_timestamp('review_creation_date') }}          as review_creation_date,
        {{ to_timestamp('review_answer_timestamp') }}       as review_answer_timestamp
    from latest

)

select * from renamed
