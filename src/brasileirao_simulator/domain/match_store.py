"""Every admitted match of every competition, one chronological stream.

WHY THIS EXISTS. A cross-season quantity like Elo (T4.x) - a club's rating in
March 2025 depends on its Libertadores run in 2024 - cannot live inside the
per-season, Série-A-only `SeasonData`. `MatchStore` is the one place that
reads every shard under `src/files/datasets/competitions/{league_id}/{season}.csv`,
decides which rows count (`domain.competitions.COMPETITIONS`), and hands back
one frame the rest of the estimation pipeline replays in order.

WHAT THIS DOES NOT DO. It never touches `remaining_games`, `tidy_fixtures.sql`
or `standings.sql` - "other competitions never enter the SQL" is decision 1 of
docs/superpowers/specs/2026-09-10-multi-competition-and-elo-design.md. This
ticket (T2.1) produces the store; nothing consumes it yet.

SCORING IS ALWAYS 90 MINUTES. `goals_home`/`goals_away` include extra time;
`score_fulltime_home`/`score_fulltime_away` do not. A cup tie that went to
extra time or penalties should contribute the same 90-minute result a league
match would, so every admitted match is scored from `score_fulltime_*` -
never `goals_*`. See tests/test_match_store.py for the Libertadores tie this
changes the recorded score of (Palmeiras 1-1 Flamengo, not 2-1).

One narrow exception, in `_resolve_fulltime_score`: 3,126 admitted rows -
every one a plain `FT` Série A match, mostly 2008 and earlier - are missing
`score_fulltime_*` in the export entirely. An `FT` match had no extra time,
so `goals_*` and the absent fulltime score are the same number by
definition; filling the gap from `goals_*` there is not the substitution
this rule warns against. An `AET`/`PEN` row with no fulltime score would be
the real problem - that case raises rather than guessing.

IS_NEUTRAL IS A HEURISTIC, DOCUMENTED RATHER THAN HIDDEN. API-Football has no
"is this venue neutral" column. What exists is `fixture_venue_id`, and each
club's own admitted home matches, whose most common venue stands in for "the
club's home ground". A match is flagged neutral when its venue matches
neither side's usual ground. Two known limitations:

  1. `fixture_venue_id` is missing on roughly 60% of rows in this data (most
     of the older Série A seasons, and in-progress ones) - a missing venue
     can never be shown to differ from a usual ground, so it defaults to
     NOT neutral, which is the common case and the safer default for a
     signal that only nudges home advantage.
  2. A club that never hosts an admitted match with a known venue (a foreign
     Libertadores/Sudamericana side that only ever travels to Brazil in this
     data) has no usual ground to compare against, so any known venue counts
     as "not this club's" - which can tip a match to neutral on weak grounds.

Both directions were chosen to bias toward under- rather than over-flagging a
genuine neutral-site final.
"""

import glob
import os

import pandas as pd

from brasileirao_simulator.config.settings import DATASETS_PATH
from brasileirao_simulator.domain.competitions import COMPETITIONS
from brasileirao_simulator.domain.season_dates import utc_cutoff

_ADMITTED_STATUSES = {"FT", "AET", "PEN"}

# Columns MatchStore reads off every shard. A shard missing any of these
# fails loudly (a KeyError from pandas) rather than silently dropping a
# competition - the 41-column API-Football schema is assumed, per T1.1.
_RAW_COLUMNS = [
    "fixture_id",
    "fixture_date",
    "fixture_venue_id",
    "fixture_status_short",
    "league_id",
    "league_season",
    "league_round",
    "teams_home_id",
    "teams_home_name",
    "teams_away_id",
    "teams_away_name",
    "score_fulltime_home",
    "score_fulltime_away",
    "goals_home",
    "goals_away",
]

_ID_COLUMNS = ("fixture_id", "league_id", "season", "home_id", "away_id", "home_goals", "away_goals")

_OUTPUT_COLUMNS = [
    "fixture_id",
    "fixture_date",
    "league_id",
    "season",
    "round",
    "home_id",
    "away_id",
    "home_name",
    "away_name",
    "home_goals",
    "away_goals",
    "is_neutral",
    "admitted_by",
    "status",
]

_EMPTY_MATCHES = pd.DataFrame(columns=_OUTPUT_COLUMNS)


class MatchStore:
    """Every match admitted by `rules`, sorted by `(fixture_date,
    fixture_id)` and deduped on `fixture_id`.

    `matches` is fixed at construction: loading is not a stream that could
    answer differently on a second read, so there is one pass over the
    shards in `__init__` and every accessor reads the frame it produced.
    """

    def __init__(self, root: str = f"{DATASETS_PATH}/competitions", rules: dict = COMPETITIONS):
        self._root = root
        self._rules = rules
        self._matches = self._load()

    @property
    def matches(self) -> pd.DataFrame:
        """One row per admitted match. See module docstring for scoring and
        `is_neutral` rules, and `domain.competitions.COMPETITIONS` for which
        leagues and rounds are admitted."""
        return self._matches

    def coverage(self, season: int) -> dict:
        """`{"competitions": {league_id: matches}, "clubs": {team_id:
        matches}}` for one season - the manifest that stops a later
        comparison silently mixing a league-only year with a full one."""
        season_matches = self._matches[self._matches["season"] == season]

        competitions = {
            int(league_id): int(count)
            for league_id, count in season_matches["league_id"].value_counts().items()
        }

        club_appearances = pd.concat(
            [season_matches["home_id"], season_matches["away_id"]], ignore_index=True
        )
        clubs = {int(team_id): int(count) for team_id, count in club_appearances.value_counts().items()}

        return {"competitions": competitions, "clubs": clubs}

    def before(self, as_of_date: str) -> pd.DataFrame:
        """Matches kicked off before `as_of_date` began in Brazil - the view
        Elo's replay and `team_strength` use so a forecast never sees its own
        future.

        `as_of_date` is a local date, like every other as-of date in the
        project; kickoffs are stored in UTC. `season_dates.utc_cutoff` is
        where the two are reconciled, and reconciling them anywhere else is
        how night games got dropped (see the `local-day-cutoff` ticket)."""
        cutoff = utc_cutoff(as_of_date)
        kickoff = pd.to_datetime(self._matches["fixture_date"], utc=True, format="mixed")
        return self._matches[kickoff < cutoff].reset_index(drop=True)

    def _load(self) -> pd.DataFrame:
        raw = self._read_shards()
        admitted = self._apply_rules(raw)
        if admitted.empty:
            return _EMPTY_MATCHES.copy()
        return self._finalise(admitted)

    def _read_shards(self) -> pd.DataFrame:
        paths = sorted(glob.glob(os.path.join(self._root, "*", "*.csv")))
        if not paths:
            return pd.DataFrame(columns=_RAW_COLUMNS)
        return pd.concat(
            (pd.read_csv(path, usecols=_RAW_COLUMNS) for path in paths), ignore_index=True
        )

    def _apply_rules(self, raw: pd.DataFrame) -> pd.DataFrame:
        """Status, round and league membership, all in one pass over
        `COMPETITIONS` (or the `rules` a caller supplied). A league id
        absent from `rules` is never looked at, even if its shard sits right
        there under `root` - the gate this ticket names explicitly."""
        admitted_frames = []
        for league_id, rule in self._rules.items():
            subset = raw[raw["league_id"] == league_id]
            subset = subset[subset["fixture_status_short"].isin(_ADMITTED_STATUSES)]
            if rule.from_round is not None:
                subset = subset[subset["league_round"].map(rule.from_round.admits)]
            if subset.empty:
                continue
            admitted_frames.append(subset.assign(admitted_by=rule.name))

        if not admitted_frames:
            return raw.iloc[0:0].assign(admitted_by=pd.Series(dtype="object"))
        return pd.concat(admitted_frames, ignore_index=True)

    def _finalise(self, admitted: pd.DataFrame) -> pd.DataFrame:
        admitted = admitted.drop_duplicates(subset="fixture_id", keep="first")
        admitted = self._resolve_fulltime_score(admitted)
        admitted = admitted.rename(
            columns={
                "league_season": "season",
                "league_round": "round",
                "teams_home_id": "home_id",
                "teams_away_id": "away_id",
                "teams_home_name": "home_name",
                "teams_away_name": "away_name",
                "score_fulltime_home": "home_goals",
                "score_fulltime_away": "away_goals",
                "fixture_status_short": "status",
            }
        )
        for column in _ID_COLUMNS:
            admitted[column] = admitted[column].astype("int64")

        admitted = self._add_is_neutral(admitted)
        admitted = admitted.sort_values(["fixture_date", "fixture_id"], kind="mergesort")
        return admitted[_OUTPUT_COLUMNS].reset_index(drop=True)

    def _resolve_fulltime_score(self, admitted: pd.DataFrame) -> pd.DataFrame:
        """`score_fulltime_*` is missing on 3,126 rows in this data - every
        one of them a plain `FT` Série A match from the older seasons (2008
        and earlier are the bulk), never an `AET`/`PEN` tie. `goals_*`
        includes extra time, which is exactly why the module docstring says
        never read it - except an `FT` match had no extra time to include,
        so for that status only, `goals_*` and the missing `score_fulltime_*`
        are the same number by definition, and filling the gap from it is
        not the thing the rule warns against. An `AET`/`PEN` row missing its
        fulltime score would be the real problem this can't safely paper
        over, so that case raises instead of guessing.
        """
        missing = admitted["score_fulltime_home"].isna() | admitted["score_fulltime_away"].isna()
        if not missing.any():
            return admitted

        plain_ft = admitted["fixture_status_short"] == "FT"
        unsafe = missing & ~plain_ft
        if unsafe.any():
            bad_ids = admitted.loc[unsafe, "fixture_id"].tolist()
            raise ValueError(
                f"fixture(s) {bad_ids} are AET/PEN with no fulltime score - "
                "goals_* would include extra time and cannot stand in for it"
            )

        fallback = missing & plain_ft
        admitted = admitted.copy()
        admitted.loc[fallback, "score_fulltime_home"] = admitted.loc[fallback, "goals_home"]
        admitted.loc[fallback, "score_fulltime_away"] = admitted.loc[fallback, "goals_away"]
        return admitted

    def _add_is_neutral(self, admitted: pd.DataFrame) -> pd.DataFrame:
        """See the module docstring's IS_NEUTRAL section for the rule and
        its two documented limitations."""
        # The usual ground is read off the matches the DEFAULT rules admit, so
        # a store that admits more (state championships, every Copa round)
        # does not move a club's ground - and with it the neutral flag of
        # matches years earlier. A club with no such match falls back to all
        # of its admitted home matches. For the default store both are the
        # same set.
        with_venue = admitted.dropna(subset=["fixture_venue_id"])
        by_default = [
            league in COMPETITIONS
            and (COMPETITIONS[league].from_round is None or COMPETITIONS[league].from_round.admits(rnd))
            for league, rnd in zip(with_venue["league_id"], with_venue["round"])
        ]
        usual_venue = (
            with_venue[by_default].groupby("home_id")["fixture_venue_id"].apply(_smallest_mode)
            .combine_first(with_venue.groupby("home_id")["fixture_venue_id"].apply(_smallest_mode))
        )

        known_venue = admitted["fixture_venue_id"].notna()
        is_home_ground = known_venue & (
            admitted["fixture_venue_id"] == admitted["home_id"].map(usual_venue)
        )
        is_away_ground = known_venue & (
            admitted["fixture_venue_id"] == admitted["away_id"].map(usual_venue)
        )
        return admitted.assign(is_neutral=known_venue & ~is_home_ground & ~is_away_ground)


def _smallest_mode(venues: pd.Series):
    """The most frequent value in `venues`, ties broken by the smallest id -
    a deterministic stand-in for `Series.mode()`, whose own tie order is not
    documented behaviour to depend on."""
    counts = venues.value_counts()
    return counts[counts == counts.max()].index.min()
