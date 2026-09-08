-- $lookback drives two jobs that must move together: the window size below
-- (rn <= $lookback) and the shrinkage-blend denominator in `backfill`
-- ($lookback as data_points). A full-window team's data_points always equals
-- $lookback, so it stays fully self-determined - (r.data_points -
-- t.data_points) is 0 - at any window size; only the amount of history
-- changes. Moving one without the other would confound window size with
-- shrinkage strength (see docs/superpowers/specs/2026-09-08-lookback-sweep-design.md).
-- Defaults to FULL_WINDOW_MATCHES (domain/batch_simulation.py), so today's
-- callers, who never pass a lookback, are unaffected.
with base as (
    select team_name,
           venue,
           fixture_date,
           goals_for,
           goals_against,
           row_number() over (partition by team_name, venue order by fixture_date desc) as rn,
           case when row_number() over (partition by team_name, venue order by fixture_date desc) <= 1 then 4
                when row_number() over (partition by team_name, venue order by fixture_date desc) <= 5 then 3
                else 1
           end as weight
    from new_fixtures
    where goals_for is not null
),

teams_ as (
    select team_name,
           venue,
           sum(goals_for * weight)::float/sum(weight) as goals_for_average,
           sum(goals_against * weight)::float/sum(weight) as goals_against_average,
           count(*) as data_points
    from base
    where rn <= $lookback
    group by 1,2
),

championship_ as (
    select 'championship' as team_name,
           'both' as venue,
           sum(goals_for::float)/count(*) as goals_for_average,
           sum(goals_against::float)/count(*) as goals_against_average,
           count(*) as data_points
    from base
    group by 1,2
),

-- this can have the actual data of the championship, not some hard coded valued
backfill as (
    select 'not_enough_games' as team_name,
           'home' as venue,
           1.026 goals_for_average,
           1.25 goals_against_average,
           $lookback as data_points
    union all
    select 'not_enough_games' as team_name,
           'away' as venue,
           0.815 goals_for_average,
           1.565 goals_against_average,
           $lookback as data_points
),

team_venue_ as (
    select distinct team_name, venue as venue from teams_
),

teams_coalesce as (
    select t1.team_name,
           t1.venue,
           coalesce(t2.goals_for_average, 0.92) as goals_for_average,
           coalesce(t2.goals_against_average, 1.41) as goals_against_average,
           coalesce(t2.data_points, 0) as data_points
    from team_venue_ as t1
        left join teams_ as t2 on t1.team_name = t2.team_name and t1.venue = t2.venue
),

teams_w_avg as (
    select t.team_name,
           t.venue,
           (t.data_points * t.goals_for_average + (r.data_points - t.data_points)*r.goals_for_average)::float/r.data_points as goals_for_average,
           (t.data_points * t.goals_against_average + (r.data_points - t.data_points)*r.goals_against_average)::float/r.data_points as goals_against_average,
           greatest(t.data_points, r.data_points) as data_points
    from teams_coalesce as t
        left join backfill as r on r.venue = t.venue
)

select * from teams_w_avg union all
select * from championship_
order by goals_for_average, venue
