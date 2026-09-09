-- $lookback drives two jobs that must move together: the window size below
-- (rn <= $lookback) and the shrinkage-blend denominator in `backfill`
-- ($lookback as data_points). A full-window team's data_points always equals
-- $lookback, so it stays fully self-determined - (r.data_points -
-- t.data_points) is 0 - at any window size; only the amount of history
-- changes. Moving one without the other would confound window size with
-- shrinkage strength (see docs/superpowers/specs/2026-09-08-lookback-sweep-design.md).
-- Defaults to FULL_WINDOW_MATCHES (domain/batch_simulation.py), so today's
-- callers, who never pass a lookback, are unaffected.
-- $weight_recent/$weight_mid/$weight_base are the same recency weights that
-- used to be the literals 4/3/1: most recent match, next four, remaining
-- window. Defaults reproduce today's 53% last-5 share exactly (see
-- entrypoints/variant_sweep.py's recency-weight sweep for the swept range).
with base as (
    select team_name,
           venue,
           fixture_date,
           goals_for,
           goals_against,
           row_number() over (partition by team_name, venue order by fixture_date desc) as rn,
           case when row_number() over (partition by team_name, venue order by fixture_date desc) <= 1 then $weight_recent
                when row_number() over (partition by team_name, venue order by fixture_date desc) <= 5 then $weight_mid
                else $weight_base
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

-- $prior_weight scales how hard the backfill prior pulls a thin-window team's
-- average toward it. 1.0 reproduces today's behaviour exactly: the prior gets
-- full weight on the (r.data_points - t.data_points) games "missing" from the
-- team's own window, and the denominator collapses to r.data_points, same as
-- the un-parameterised formula this replaced. 0 ignores the prior entirely -
-- a team's own average stands however thin; 2 pulls twice as hard as today.
-- Guard: if prior_weight=0 AND a team has zero games of its own, the blend
-- weight is 0/0 - that limit falls back to the team's own (possibly already
-- coalesced-to-default, see teams_coalesce two CTEs up) average rather than
-- erroring. At the default prior_weight=1 this guard never fires (r.data_points
-- is $lookback, never 0), so it changes nothing about today's behaviour.
teams_w_avg as (
    select t.team_name,
           t.venue,
           case when (t.data_points + $prior_weight * (r.data_points - t.data_points)) = 0
                then t.goals_for_average
                else (t.data_points * t.goals_for_average + $prior_weight * (r.data_points - t.data_points) * r.goals_for_average)::float
                     / (t.data_points + $prior_weight * (r.data_points - t.data_points))
           end as goals_for_average,
           case when (t.data_points + $prior_weight * (r.data_points - t.data_points)) = 0
                then t.goals_against_average
                else (t.data_points * t.goals_against_average + $prior_weight * (r.data_points - t.data_points) * r.goals_against_average)::float
                     / (t.data_points + $prior_weight * (r.data_points - t.data_points))
           end as goals_against_average,
           greatest(t.data_points, r.data_points) as data_points
    from teams_coalesce as t
        left join backfill as r on r.venue = t.venue
)

select * from teams_w_avg union all
select * from championship_
order by goals_for_average, venue
