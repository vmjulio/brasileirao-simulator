with base_home as (
    select fixture_date,
           round_,
           team_name as home_team,
           opponent_name as away_team,
           goals_for as home_goals,
           goals_against as away_goals 
    from df
    where goals_for is not null
      and venue = 'home'
)

select *
from base_home
