WITH concat_previous_year as (
    select fixture_id,
           fixture_date,
           league_name,
           league_id,
           league_round,
           league_season,
           teams_home_name,
           teams_home_id,
           teams_away_name,
           teams_away_id,
           goals_home,
           goals_away,
           fixture_status_long,
           fixture_status_elapsed
    from previous_year
    where league_season = '$previous_season' and league_id = 71
    union all
    select fixture_id,
           fixture_date,
           league_name,
           league_id,
           league_round,
           league_season,
           teams_home_name,
           teams_home_id,
           teams_away_name,
           teams_away_id,
           goals_home,
           goals_away,
           fixture_status_long,
           fixture_status_elapsed
    from fixtures
    where league_season = '$season' and league_id = 71
),

base AS (
    SELECT fixtures.fixture_id,
        fixtures.fixture_date::timestamp - INTERVAL '3 hours' AS fixture_date,
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
        SPLIT_PART(fixtures.league_round, ' - ', 2)::INTEGER AS round_,
        (SPLIT_PART(fixtures.league_round, ' - ', 2)::INTEGER-1)/19 + 1 AS turn_
    FROM concat_previous_year as fixtures
    WHERE fixtures.league_season in ('$season', '$previous_season')
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
        ongoing
    FROM base
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
        ongoing
    FROM base
)

SELECT *
FROM tidy
