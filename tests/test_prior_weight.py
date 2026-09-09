"""The $prior_weight parameter on team_params_same_venue_average.sql's
newcomer-prior blend.

teams_w_avg blends a team's own average toward a hardcoded backfill prior
(1.026/1.25 home, 0.815/1.565 away) whenever the team has fewer than a full
lookback window of real matches: weight t.data_points on the team's own
average, weight (r.data_points - t.data_points) on the prior, divided by
r.data_points (== lookback). $prior_weight scales the prior's weight in that
blend - 0 ignores it, 1 is today's behaviour, 2 pulls twice as hard.

UNLIKE the recency weights (test_recency_weights.py) this could not be
parameterised as a pure literal swap: scaling only the prior's share while
keeping a team's own weight fixed needs an independent multiplier in the
formula, and a multiplier that can be zero needs a divide-by-zero guard for
the (rare, zero-real-games) case the original formula never had to handle -
see the query's own $prior_weight comment. Both are structural changes, so
the rendered SQL TEXT cannot be byte-identical to the original formula at any
value, default included. The equivalence gate here is therefore at the value
level instead: test_default_prior_weight_reproduces_original_formula_exactly
runs the ORIGINAL (pre-parameterisation) formula, embedded below as
independent ground truth, side by side with Queries(season) at its default,
and requires the two DataFrames to match exactly - not approximately - for
every (team_name, venue) row, on real data including an early-season date
where partial-window teams are common.
"""

import duckdb

from brasileirao_simulator.domain.batch_simulation import FULL_WINDOW_MATCHES
from brasileirao_simulator.domain.queries import DEFAULT_PRIOR_WEIGHT, Queries
from brasileirao_simulator.domain.season_data import SeasonData
from brasileirao_simulator.domain.tables import Tables


# The query exactly as it read before $prior_weight existed (git blame
# src/files/queries/team_params_same_venue_average.sql for the commit that
# introduced it) - embedded rather than read from disk, so this stays
# independent ground truth even after the live file has moved on.
ORIGINAL_FORMULA_SQL = r"""
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
    where rn <= 19
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

backfill as (
    select 'not_enough_games' as team_name,
           'home' as venue,
           1.026 goals_for_average,
           1.25 goals_against_average,
           19 as data_points
    union all
    select 'not_enough_games' as team_name,
           'away' as venue,
           0.815 goals_for_average,
           1.565 goals_against_average,
           19 as data_points
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
"""


def _team_params(season: int, as_of_date: str, sql: str):
    tables = Tables(SeasonData(season))
    fixtures = tables.enriched_tidy_fixtures(blank_from_date=as_of_date)
    con = duckdb.connect()
    con.register("new_fixtures", fixtures)
    df = con.sql(sql).df()
    return df.sort_values(["team_name", "venue"]).reset_index(drop=True)


def test_default_prior_weight_is_one():
    assert DEFAULT_PRIOR_WEIGHT == 1.0


def test_default_prior_weight_reproduces_original_formula_exactly():
    """THE gate for this sweep. Exact (not approximate) equality against the
    embedded pre-parameterisation formula, on a mid-season date (established
    teams, mostly full windows) and an early-season one (freshly promoted
    teams, several partial windows) - the case the divide-by-zero guard
    exists for."""
    for season, as_of_date in [(2025, "2025-08-31"), (2016, "2016-06-01"), (2019, "2019-05-01")]:
        original = _team_params(season, as_of_date, ORIGINAL_FORMULA_SQL)
        default = _team_params(season, as_of_date, Queries(season).team_params_same_venue_average())

        assert original.equals(default), f"{season} {as_of_date}: default prior_weight diverged from the original formula"


def test_prior_weight_zero_does_not_crash_on_a_thin_window_date():
    """The edge case the guard exists for: an early-season date has teams
    with zero real matches in the window. prior_weight=0 must not divide by
    zero there."""
    df = _team_params(2016, "2016-06-01", Queries(2016, prior_weight=0.0).team_params_same_venue_average())

    assert not df["goals_for_average"].isna().any()
    assert not df["goals_against_average"].isna().any()


def test_prior_weight_changes_a_thin_window_team_but_not_a_full_window_one():
    """prior_weight only has anything to bite on when a team's own window is
    partial (t.data_points != r.data_points == FULL_WINDOW_MATCHES) - see the
    query's own $prior_weight comment. This pins that asymmetry rather than
    just checking *something* changed somewhere."""
    season, as_of_date = 2016, "2016-06-01"
    strong = _team_params(season, as_of_date, Queries(season, prior_weight=2.0).team_params_same_venue_average())
    default = _team_params(season, as_of_date, Queries(season).team_params_same_venue_average())

    merged = strong.set_index(["team_name", "venue"]).join(
        default.set_index(["team_name", "venue"]), lsuffix="_strong", rsuffix="_default"
    )

    tables = Tables(SeasonData(season))
    fixtures = tables.enriched_tidy_fixtures(blank_from_date=as_of_date)
    con = duckdb.connect()
    con.register("new_fixtures", fixtures)
    counts = con.sql(Queries(season).team_match_counts()).df().set_index(["team_name", "venue"])["match_count"]

    thin = counts[counts < FULL_WINDOW_MATCHES].index
    thin = merged.index.intersection(thin)
    full = merged.index.difference(thin).intersection(
        counts[counts == FULL_WINDOW_MATCHES].index
    )

    assert len(thin) > 0, "no thin-window (team, venue) rows on this date - test fixture assumption broke"
    assert len(full) > 0, "no full-window (team, venue) rows on this date - test fixture assumption broke"

    assert (merged.loc[thin, "goals_for_average_strong"] != merged.loc[thin, "goals_for_average_default"]).any(), (
        "prior_weight=2.0 left every thin-window team's average unchanged"
    )
    assert (merged.loc[full, "goals_for_average_strong"] == merged.loc[full, "goals_for_average_default"]).all(), (
        "prior_weight changed a full-window team's average - it should only move thin-window teams"
    )
