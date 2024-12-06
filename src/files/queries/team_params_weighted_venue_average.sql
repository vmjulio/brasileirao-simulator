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
),

teams_by_venue as (
    select team_name,
           venue,
           sum(goals_for * weight)::float/sum(weight) as goals_for_average,
           sum(goals_against * weight)::float/sum(weight) as goals_against_average
    from base
    where rn <= 12
    group by 1,2
),

teams_weighted_venue as (
    select team_name,
           sum(case when venue = 'home' then goals_for_average else 0 end)*0.8
         + sum(case when venue = 'away' then goals_for_average else 0 end)*0.2 as home_80_away_20_goals_for_average,
           sum(case when venue = 'home' then goals_against_average else 0 end)*0.8
         + sum(case when venue = 'away' then goals_against_average else 0 end)*0.2 as home_80_away_20_goals_against_average,
           sum(case when venue = 'away' then goals_for_average else 0 end)*0.8
         + sum(case when venue = 'home' then goals_for_average else 0 end)*0.2 as away_80_home_20_goals_for_average,
           sum(case when venue = 'away' then goals_against_average else 0 end)*0.8
         + sum(case when venue = 'home' then goals_against_average else 0 end)*0.2 as away_80_home_20_goals_against_average,
    from teams_by_venue
    group by 1
)

select v.team_name,
       v.venue,
       max(case when v.venue = 'home' then w.home_80_away_20_goals_for_average
            else w.away_80_home_20_goals_for_average
       end) as goals_for_average,
       max(case when v.venue = 'away' then w.away_80_home_20_goals_against_average
            else w.home_80_away_20_goals_against_average
       end) as goals_against_average
from teams_by_venue as v
    left join teams_weighted_venue as w on v.team_name = w.team_name
group by 1,2
