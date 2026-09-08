with base as (
    select team_name,
           sum(case when goals_for > goals_against then 3
                    when goals_for = goals_against then 1
                    else 0 end) as p,
           count(*) as g,
           sum(case when goals_for > goals_against then 1 else 0 end) as w,
           sum(case when goals_for = goals_against then 1 else 0 end) as d,
           sum(case when goals_for < goals_against then 1 else 0 end) as l,
           sum(goals_for) as gf,
           sum(goals_against) as ga,
           coalesce(gf,0)-coalesce(ga,0) as gd,
           p::float/(g*3) as point_ratio
    from enriched_tidy_fixtures
    where goals_for is not null
      and season = '$season'
    group by 1
)

-- ga is omitted deliberately: gd = gf - ga, so a tie on gf and gd forces a tie
-- on ga and it can never break one. See tests/test_standings_ranking.py.
select row_number() over (order by p desc, w desc, gd desc, gf desc) as rank_, *
from base
