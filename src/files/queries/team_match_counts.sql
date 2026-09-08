-- How many real matches back each team's per-venue parameters.
--
-- team_params_same_venue_average.sql reports `greatest(count, 19)`, which is
-- always 19 and therefore cannot distinguish a promoted side's 12 matches from
-- an established side's 19. Parameter uncertainty needs that number, so this
-- query reproduces the same window without the shrinkage blend. The literal
-- 19 here is this query's twin of domain/batch_simulation.py's
-- FULL_WINDOW_MATCHES - both must move together if the lookback window ever
-- changes.
-- new_fixtures carries the previous season too (the lookback window needs it
-- to reach 19 matches), so team scope is restricted to this season's teams -
-- otherwise relegated sides from $previous_season would show up alongside
-- this season's promoted ones.
with current_season_teams as (
    select distinct team_name
    from new_fixtures
    where season = $season
),

base as (
    select team_name,
           venue,
           row_number() over (partition by team_name, venue order by fixture_date desc) as rn
    from new_fixtures
    where goals_for is not null
      and team_name in (select team_name from current_season_teams)
)

select team_name,
       venue,
       count(*) as match_count
from base
where rn <= 19
group by 1, 2
