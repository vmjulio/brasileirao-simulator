"""DixonColesAdapter's structure, fed from every admitted competition instead
of Série A fixtures alone.

WHY THIS EXISTS. dixon_coles_backtest.py measured arm A - Dixon-Coles fitted
on league-only data - at 3-10 against the incumbent, and the chancedegol
gap analysis (docs/superpowers/specs/2026-09-10-multi-competition-and-elo-design.md)
argues the missing ingredient is not the estimator but the DATA: twelve
months across eight competitions versus one. This adapter is arm B: the
identical Dixon-Coles fit (domain/dixon_coles.py, untouched), fed from
`MatchStore` - Série A, Série B, Copa do Brasil from the round of 16, and the
group stages of Libertadores and Sudamericana (domain/competitions.py) -
instead of `enriched_tidy_fixtures`. Whether B actually beats A is measured
by dixon-coles-all-vs-league (the next ticket), not here.

IDENTITY - FIT ON IDS, NOT NAMES. `MatchStore` rows carry each club's name as
the API spelled it IN THAT COMPETITION's export, and those spellings are not
guaranteed consistent across competitions or seasons - the same club that is
"Atletico-MG" in one Série A export can be "Atletico Mineiro" in a Libertadores
one, and `entrypoints/import_seasons.py`'s own docstring names two Série A
examples across seasons (Bragantino -> RB Bragantino, Chapecoense-SC ->
Chapecoense-sc). `team_id` is the one thing stable across every competition
and every season, so `_all_competitions_rows` below builds the per-team-per-
match frame `dixon_coles.fit` expects with `team_name`/`opponent_name` set to
the STRING team_id, not any display name. One id is one entity in `Ratings`
regardless of how many ways its name has been spelled - which is exactly the
"a club is one entity across every competition" requirement this ticket is
built to satisfy. A club outside Série A (a Série B side, a foreign
Libertadores opponent) gets an entry in `Ratings.attack`/`Ratings.defence`
keyed by its id the same way any Série A club does; it simply never has a
canonical name to translate back to, because it never needs one.

MAPPING IDS BACK TO CANONICAL NAMES. `build_baseline`'s `home_name`/
`away_name` arrays - what `simulate_batch`, the loggers and every existing
lambda consumer are keyed by - are canonical Série A club names: the strings
`entrypoints/import_seasons.py` already canonicalised into
`files/datasets/{season}/fixtures.csv` (one name per id, the most recent
one), which is exactly what `fixtures.team_name`/`fixtures.team_id` (the
frame `build_baseline` itself is called with, built by
`Tables.enriched_tidy_fixtures` from `tidy_fixtures.sql`) already carries.
So the id -> canonical-name map used here is read straight off that same
`fixtures` argument - `dict(zip(fixtures.team_id, fixtures.team_name))` - not
re-derived from `MatchStore`'s own (uncanonicalised, "no transformation" per
T1.1) Série A shard, and not read from a CSV path this adapter has to know
about. `fixtures` is the frame `build_baseline` consumes, so the map is
built from the thing being mapped INTO, which is the one place safe from a
stale spelling.

NEUTRAL MATCHES. `dixon_coles.fit` has no neutral-venue notion at all - its
one home-advantage parameter is fit from whichever side `_match_rows` finds
labelled `venue == 'home'`, with no third case. `MatchStore.matches` carries
`is_neutral`, but there is nowhere in `fit`'s signature to hand it a match
that is home for neither side without changing `fit` itself, which this
ticket forbids. So a neutral match (the clearest cases are Libertadores/
Sudamericana finals played at a drawn venue) is fitted exactly as `MatchStore`
labelled its `home_id`/`away_id` - contributing to `home_advantage` as an
ordinary home match would. This very slightly overstates `home_advantage`
(a handful of matches out of several thousand) and is documented rather than
worked around, per the ticket's explicit instruction.

WHAT STAYS THE SAME AS DixonColesAdapter. Same port, same
`simulate_batch`/`build_baseline` contract, same reuse of the shipped
`build_baseline` for everything except `lam_home`/`lam_away`, same
MEASUREMENT CANDIDATE status - `batch` remains the default and nothing here
changes that.
"""

from dataclasses import replace
from typing import Optional

import duckdb
import numpy as np
import pandas as pd

from brasileirao_simulator.domain import dixon_coles
from brasileirao_simulator.domain.batch_simulation import (
    BatchOutcome,
    SeasonBaseline,
    build_baseline,
    simulate_batch,
)
from brasileirao_simulator.domain.match_store import MatchStore
from brasileirao_simulator.domain.queries import Queries
from brasileirao_simulator.ports.batch_simulator_port import BatchSimulatorPort


class DixonColesAllAdapter(BatchSimulatorPort):
    """DixonColesAdapter's structure; the fit reads `MatchStore` (every
    admitted competition) instead of Série A fixtures alone. See the module
    docstring for identity, the id->name mapping, and neutral-match handling.
    """

    vectorise_fixtures = False

    def __init__(
        self,
        strategy: Optional[str],
        season: int,
        rng: Optional[np.random.Generator] = None,
        xi: float = dixon_coles.DEFAULT_XI,
        match_store: Optional[MatchStore] = None,
    ) -> None:
        self.con = duckdb.connect()
        self.strategy: Optional[str] = strategy
        self.season: int = season
        self.rng: Optional[np.random.Generator] = rng
        self.xi: float = xi
        self.queries: Queries = Queries(season)
        self.match_store: MatchStore = match_store if match_store is not None else MatchStore()
        self.ratings: Optional[dixon_coles.Ratings] = None

    def fit_ratings(self, fixtures: pd.DataFrame) -> dixon_coles.Ratings:
        """Fit on every `MatchStore` match strictly before the same frontier
        DixonColesAdapter.fit_ratings uses: the latest played fixture in
        `fixtures`, which is already blanked from the as-of date onwards by
        Tables.enriched_tidy_fixtures. Using the same frontier for both arms
        is what makes arm A and arm B comparable at the same as-of date.
        """
        played = fixtures[fixtures["goals_for"].notnull()]
        as_of_date = str(pd.to_datetime(played["fixture_date"], format="mixed").max().date())

        # `MatchStore.before` is strictly "<" its cutoff; the frontier date's
        # own matches must still count (DixonColesAdapter's fixtures frame
        # keeps them - blank_from_date only blanks strictly AFTER 23:59:59 on
        # the as-of date), so the cutoff passed to MatchStore is the next
        # calendar day.
        cutoff = str((pd.Timestamp(as_of_date) + pd.Timedelta(days=1)).date())
        visible = self.match_store.before(cutoff)

        rows = _all_competitions_rows(visible)
        self.ratings = dixon_coles.fit(rows, as_of_date, xi=self.xi)
        return self.ratings

    def build_baseline(
        self, fixtures: pd.DataFrame, remaining_games: pd.DataFrame
    ) -> SeasonBaseline:
        """The shipped baseline with its two lambda arrays swapped out - see
        DixonColesAdapter.build_baseline, which this mirrors exactly except
        for how the ratings that produce lam_home/lam_away are fitted and
        looked up.
        """
        self.con.register("new_fixtures", fixtures)
        team_params = self.con.sql(self.queries.team_params_same_venue_average()).df()
        baseline = build_baseline(fixtures, remaining_games, team_params, self.season)

        ratings = self.fit_ratings(fixtures)
        id_by_name = _id_by_canonical_name(fixtures)
        pairs = [
            dixon_coles.lambdas(ratings, _team_id_key(id_by_name, home), _team_id_key(id_by_name, away))
            for home, away in zip(baseline.home_name, baseline.away_name)
        ]
        lam_home = np.array([p[0] for p in pairs], dtype=float)
        lam_away = np.array([p[1] for p in pairs], dtype=float)

        return replace(baseline, lam_home=lam_home, lam_away=lam_away)

    def simulate_batch(
        self,
        fixtures: pd.DataFrame,
        remaining_games: pd.DataFrame,
        iterations: int,
    ) -> BatchOutcome:
        baseline = self.build_baseline(fixtures, remaining_games)
        return simulate_batch(
            baseline,
            iterations,
            self.rng if self.rng is not None else np.random.default_rng(),
            vectorise_fixtures=self.vectorise_fixtures,
        )


def _all_competitions_rows(matches: pd.DataFrame) -> pd.DataFrame:
    """`MatchStore.matches` (one row per match) reshaped into the
    per-team-per-match frame `dixon_coles.fit` expects (one row per team's
    perspective, `team_name`/`opponent_name`/`venue`/`goals_for`/
    `goals_against`/`fixture_date`) - `_match_rows` in domain/dixon_coles.py
    for the exact contract. `team_name`/`opponent_name` are the STRING
    team_id, not a display name - see the module docstring's IDENTITY
    section for why.
    """
    home = pd.DataFrame(
        {
            "team_name": matches["home_id"].astype(str),
            "opponent_name": matches["away_id"].astype(str),
            "venue": "home",
            "goals_for": matches["home_goals"].astype(float),
            "goals_against": matches["away_goals"].astype(float),
            "fixture_date": matches["fixture_date"],
        }
    )
    away = pd.DataFrame(
        {
            "team_name": matches["away_id"].astype(str),
            "opponent_name": matches["home_id"].astype(str),
            "venue": "away",
            "goals_for": matches["away_goals"].astype(float),
            "goals_against": matches["home_goals"].astype(float),
            "fixture_date": matches["fixture_date"],
        }
    )
    return pd.concat([home, away], ignore_index=True)


def _id_by_canonical_name(fixtures: pd.DataFrame) -> dict:
    """Canonical Série A name -> team_id, read off the same `fixtures` frame
    `build_baseline` is called with - see the module docstring's MAPPING
    section for why this frame, and not `MatchStore`, is the source."""
    pairs = fixtures[["team_name", "team_id"]].drop_duplicates(subset="team_name")
    return dict(zip(pairs["team_name"], pairs["team_id"]))


def _team_id_key(id_by_name: dict, canonical_name: str) -> str:
    """The string key `Ratings.attack`/`Ratings.defence` use for a Série A
    club, given its canonical name. A club with no id in `id_by_name` (there
    is none in this codebase's data - every Série A fixture's team_name comes
    from the same frame this map is built from) falls back to the name
    itself, which simply misses the ratings dict and lets
    dixon_coles.lambdas' own missing-team average apply, the same fallback
    DixonColesAdapter relies on."""
    team_id = id_by_name.get(canonical_name)
    return str(team_id) if team_id is not None else canonical_name
