WITH base AS (
    SELECT f.*,
           CASE WHEN goals_for > goals_against THEN 'win'
                WHEN goals_for = goals_against THEN 'draw'
                WHEN goals_for < goals_against THEN 'lose'
           END AS result
    FROM tidy_fixtures AS f
),

streaks AS (
    SELECT *,
           SUM(points) OVER (PARTITION BY season, team_id ORDER BY fixture_date) AS cum_sum_points,
           SUM(goals_for) OVER (PARTITION BY season, team_id ORDER BY fixture_date) AS cum_sum_goals_for,
           SUM(goals_against) OVER (PARTITION BY season, team_id ORDER BY fixture_date) AS cum_sum_goals_against,
           SUM(CASE WHEN result = 'win' THEN 1 ELSE 0 END) OVER (PARTITION BY season, team_id ORDER BY fixture_date) AS cum_sum_wins,
           SUM(CASE WHEN result = 'draw' THEN 1 ELSE 0 END) OVER (PARTITION BY season, team_id ORDER BY fixture_date) AS cum_sum_draws,
           SUM(CASE WHEN result = 'lose' THEN 1 ELSE 0 END) OVER (PARTITION BY season, team_id ORDER BY fixture_date) AS cum_sum_loses,
           CASE WHEN round_ > 5 THEN SUM(points) OVER (PARTITION BY season, team_id ORDER BY fixture_date ROWS BETWEEN 5 PRECEDING AND 1 PRECEDING) END AS last_5_matches,
           CASE WHEN round_ > 10 THEN SUM(points) OVER (PARTITION BY season, team_id ORDER BY fixture_date ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING) END AS last_10_matches,
           CASE WHEN round_ > 3 THEN SUM(points) OVER (PARTITION BY season, team_id ORDER BY fixture_date ROWS BETWEEN 3 PRECEDING AND 1 PRECEDING) END AS last_3_matches,
           LAG(points) OVER (PARTITION BY season, team_id ORDER BY fixture_date) AS last_match,
           LEAD(points) OVER (PARTITION BY season, team_id ORDER BY fixture_date) AS next_match,
           LAG(venue) OVER (PARTITION BY season, team_id ORDER BY fixture_date) AS last_venue,
           LEAD(venue) OVER (PARTITION BY season, team_id ORDER BY fixture_date) AS next_venue,
           LAG(goals_for) OVER (PARTITION BY season, team_id ORDER BY fixture_date) AS last_match_goals_for,
           LEAD(goals_for) OVER (PARTITION BY season, team_id ORDER BY fixture_date) AS next_match_goals_for,
           LAG(goals_against) OVER (PARTITION BY season, team_id ORDER BY fixture_date) AS last_match_goals_against,
           LEAD(goals_against) OVER (PARTITION BY season, team_id ORDER BY fixture_date) AS next_match_goals_against
    FROM base
),

rank_base AS (
    SELECT *,
           ROW_NUMBER() over (PARTITION BY league_name, season, round_ ORDER BY cum_sum_points DESC, cum_sum_wins DESC, cum_sum_goals_for-cum_sum_goals_against DESC, cum_sum_goals_for DESC) AS rank_
    FROM streaks
)

SELECT *,
       MAX(CASE WHEN round_ = 38 OR (round_ = 34 AND league_name = 'Bundesliga 1') THEN rank_ END) OVER (PARTITION BY league_name, season, team_id) AS final_rank_
FROM rank_base