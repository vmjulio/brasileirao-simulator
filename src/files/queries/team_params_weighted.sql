with base as (
    select team_name,
           venue,
           fixture_date,
           goals_for,
           goals_against,
           row_number() over (partition by team_name, venue order by fixture_date desc) as rn,
           case when row_number() over (partition by team_name, venue order by fixture_date desc) <= 1 then 3
                when row_number() over (partition by team_name, venue order by fixture_date desc) <= 5 then 2
                else 1
           end as weight
    from new_fixtures
    where goals_for is not null
    --group by 1,2,3
    --order by round_, gf desc, ga desc
),

teams_ as (
    select team_name,
           venue,
           sum(goals_for * weight)::float/sum(weight) as goals_for_average,
           sum(goals_against * weight)::float/sum(weight) as goals_against_average
    from base
    where rn <= 10
    group by 1,2
),

championship_ as (
    select 'championship' as team_name,
           'both' as venue,
           sum(goals_for)/count(*) as goals_for_average,
           sum(goals_against)/count(*) as goals_against_average
    from base
    group by 1,2
)

select * from teams_
union all
select * from championship_