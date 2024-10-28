TIDY_FIXTURES = """
    WITH base AS (
        SELECT fixtures.fixture_id,
            fixtures.fixture_date,
            fixtures.league_id,
            fixtures.league_season,
            fixtures.teams_home_name,
            fixtures.teams_home_id,
            fixtures.teams_away_name,
            fixtures.teams_away_id,
            fixtures.goals_home,
            fixtures.goals_away,
            fixtures.league_name,
            fixture_status_long <> 'Match Finished' AND fixture_status_elapsed IS NOT NULL AS ongoing,
            home.name AS home_punter,
            away.name AS away_punter,
            SPLIT_PART(fixtures.league_round, ' - ', 2)::INTEGER AS round_,
            (SPLIT_PART(fixtures.league_round, ' - ', 2)::INTEGER-1)/19 + 1 AS turn_
        FROM fixtures
            INNER JOIN punters AS home ON home.team_id = fixtures.teams_home_id
                                          AND home.league_id = fixtures.league_id
                                          AND home.season = fixtures.league_season
                                          AND home.turn = (trunc((SPLIT_PART(fixtures.league_round, ' - ', 2)::INTEGER-1)/19) + 1)::integer
            INNER JOIN punters AS away ON away.team_id = fixtures.teams_away_id
                                          AND away.league_id = fixtures.league_id
                                          AND away.season = fixtures.league_season
                                          AND away.turn = (trunc((SPLIT_PART(fixtures.league_round, ' - ', 2)::INTEGER-1)/19) + 1)::integer
        WHERE fixtures.league_season = '2024'
          AND fixtures.league_id = 71
    ),

    tidy AS (
        SELECT fixture_id,
            fixture_date,
            teams_home_name AS team_name,
            teams_home_id AS team_id,
            teams_away_name AS opponent_name,
            round_,
            league_season AS season,
            league_name,
            teams_away_id AS opponent_id,
            goals_home AS goals_for,
            goals_away AS goals_against,
            CASE WHEN goals_home > goals_away THEN 3
                    WHEN goals_home = goals_away THEN 1
                    ELSE 0
            END AS points,
            'home' AS venue,
            home_punter,
            away_punter,
            ongoing,
            doubles.name IS NOT NULL AS is_double
        FROM base
        LEFT JOIN doubles ON doubles.season = base.league_season
                                AND doubles.round = base.round_
                                AND doubles.team_id = base.teams_home_id
                                AND doubles.name = base.home_punter
        UNION
        SELECT fixture_id,
            fixture_date,
            teams_away_name AS team_name,
            teams_away_id AS team_id,
            teams_home_name AS opponent_name,
            round_,
            league_season AS season,
            league_name,
            teams_home_id AS opponent_id,
            goals_away AS goals_for,
            goals_home AS goals_against,
            CASE WHEN goals_away > goals_home THEN 3
                WHEN goals_away = goals_home THEN 1
                ELSE 0
            END AS points,
            'away' AS venue,
            home_punter,
            away_punter,
            ongoing,
            doubles.name IS NOT NULL AS is_double
        FROM base
        LEFT JOIN doubles ON doubles.season = base.league_season
                                AND doubles.round = base.round_
                                AND doubles.team_id = base.teams_away_id
                                AND doubles.name = base.away_punter
    )

    SELECT *
    FROM tidy
"""

ENRICHED_TIDY_FIXTURES = """
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
"""

STANDINGS = """
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
        group by 1
    )

    select row_number() over (order by p desc, w desc, gf desc, ga desc) as rank_, *
    from base
"""

TEAM_PARAMS = """
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
"""

BOLAO_STANDINGS = """
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
"""

TEAM_PARAMS_WEIGHTED = """
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
"""
