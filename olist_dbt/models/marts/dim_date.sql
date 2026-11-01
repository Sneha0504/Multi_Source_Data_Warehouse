-- Dimension: date. Grain: one row per calendar day.
--
-- Business logic:
--   * Spine spans date_spine_start .. date_spine_end (exclusive end) from the
--     project vars, built with dbt_utils.date_spine (portable across adapters).
--   * month_start uses the portable to_month macro; the remaining calendar parts
--     use ANSI extract(), which is not a dialect-specific function.
--   * day_of_week uses extract(dow ...): 0 = Sunday .. 6 = Saturday. day_name is
--     derived from that number with a CASE (no dialect dayname() function) and the
--     weekend flag covers Saturday (6) and Sunday (0).

with spine as (

    {{ dbt_utils.date_spine(
        datepart="day",
        start_date="cast('" ~ var('date_spine_start') ~ "' as date)",
        end_date="cast('" ~ var('date_spine_end') ~ "' as date)"
    ) }}

),

final as (

    select
        cast(date_day as date)                      as date_day,
        extract(year from date_day)                 as year,
        extract(month from date_day)                as month_number,
        cast({{ to_month('date_day') }} as date)    as month_start,
        extract(dow from date_day)                  as day_of_week,
        case extract(dow from date_day)
            when 0 then 'Sunday'
            when 1 then 'Monday'
            when 2 then 'Tuesday'
            when 3 then 'Wednesday'
            when 4 then 'Thursday'
            when 5 then 'Friday'
            when 6 then 'Saturday'
        end                                         as day_name,
        (extract(dow from date_day) in (0, 6))      as is_weekend
    from spine

)

select * from final
