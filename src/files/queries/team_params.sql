select team_name,
       venue,
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
       p::float/(g*3) as point_ratio,
       gf::float/g as goals_for_average,
       ga::float/g as goals_against_average
from new_fixtures
where goals_for is not null
group by 1,2
order by p desc, w desc, gf desc, ga desc