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