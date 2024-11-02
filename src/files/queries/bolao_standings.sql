with base_home as (
    select home_punter as punter,
           case when round_ > 19 then 2 else 1 end as turn,
           goals_for,
           goals_against,
           case when round_ > 19 then 2 else 1 end as turn,
           is_double
    from df
    where goals_for is not null
      and venue = 'home'
),

base_away as (
    select away_punter as punter,
           case when round_ > 19 then 2 else 1 end as turn,
           goals_for,
           goals_against,
           case when round_ > 19 then 2 else 1 end as turn,
           is_double
    from df
    where goals_for is not null
      and venue = 'away'
),

base as (
    select *
    from base_home
    union all
    select *
    from base_away
),

base_calc as (
    select punter,
           sum(case when goals_for > goals_against then 3
                    when goals_for = goals_against then 1
                    else 0
               end * case when is_double then 2 else 1 end) as p,
           count(*) as g,
           sum(case when goals_for > goals_against then 1 else 0 end) as w,
           sum(case when goals_for = goals_against then 1 else 0 end) as d,
           sum(case when goals_for < goals_against then 1 else 0 end) as l,
           sum(goals_for) as gf,
           sum(goals_against) as ga,
           sum(case when goals_for > goals_against then 3
                    when goals_for = goals_against then 1
                    else 0
               end * case when is_double then 1 else 0 end) as p_doubles,
           coalesce(sum(goals_for),0)-coalesce(sum(goals_against),0) as gd,
           p::float/(g*3) as point_ratio
    from base
    group by 1
)

select row_number() over (order by p desc, w desc, gf desc, ga desc) as rank_, *
from base_calc
