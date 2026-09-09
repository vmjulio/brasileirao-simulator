from string import Template

from brasileirao_simulator.config.settings import QUERIES_PATH
from brasileirao_simulator.domain.batch_simulation import FULL_WINDOW_MATCHES


# Recency weights team_params_same_venue_average.sql applies inside the
# lookback window: most recent match, next four, remaining window. These were
# the hardcoded literals 4/3/1 (53% of the estimate on the last 5 matches)
# before entrypoints/variant_sweep.py's recency-weight sweep needed to move
# them.
DEFAULT_WEIGHT_RECENT = 4
DEFAULT_WEIGHT_MID = 3
DEFAULT_WEIGHT_BASE = 1

# How hard team_params_same_venue_average.sql's newcomer-prior backfill pulls
# a thin-window team's average toward it. 1.0 is today's full-strength pull;
# see the query's own $prior_weight comment for the exact blend.
DEFAULT_PRIOR_WEIGHT = 1.0


class Queries:
    """SQL text with the season (and, for one query, the lookback window,
    recency weights and prior strength) bound in.

    Uses string.Template rather than str.format so a literal brace in SQL can
    never be mistaken for a placeholder.

    lookback, weight_recent/weight_mid/weight_base and prior_weight only
    substitute into team_params_same_venue_average.sql - lookback drives two
    jobs that must move together, the window size (`rn <= $lookback`) and the
    shrinkage-blend denominator; weight_recent/mid/base are the per-match
    recency weights inside that window; prior_weight scales how hard the
    newcomer backfill prior pulls a thin-window team's average (see that
    query's own comments for both). Every other query ignores all of them
    (safe_substitute silently skips unused keys). Every default matches what
    that query hardcoded before these parameters existed - lookback is
    FULL_WINDOW_MATCHES (domain/batch_simulation.py), the weights are 4/3/1,
    prior_weight is 1.0 - so Queries(season) with no overrides renders every
    query, including that one, exactly as it did before any of these
    parameters existed.
    """

    def __init__(
        self,
        season: int,
        lookback: int = FULL_WINDOW_MATCHES,
        weight_recent: int = DEFAULT_WEIGHT_RECENT,
        weight_mid: int = DEFAULT_WEIGHT_MID,
        weight_base: int = DEFAULT_WEIGHT_BASE,
        prior_weight: float = DEFAULT_PRIOR_WEIGHT,
    ) -> None:
        self.season: int = season
        self.lookback: int = lookback
        self.weight_recent: int = weight_recent
        self.weight_mid: int = weight_mid
        self.weight_base: int = weight_base
        self.prior_weight: float = prior_weight

    def read_sql(self, file_name: str) -> str:
        with open(f"{QUERIES_PATH}/{file_name}", "r") as f:
            template = Template(f.read())
        return template.safe_substitute(
            season=self.season,
            previous_season=self.season - 1,
            lookback=self.lookback,
            weight_recent=self.weight_recent,
            weight_mid=self.weight_mid,
            weight_base=self.weight_base,
            prior_weight=self.prior_weight,
        )

    def tidy_fixtures(self) -> str:
        return self.read_sql("tidy_fixtures.sql")

    def enriched_tidy_fixtures(self) -> str:
        return self.read_sql("enriched_tidy_fixtures.sql")

    def bolao_standings(self) -> str:
        return self.read_sql("bolao_standings.sql")

    def standings(self) -> str:
        return self.read_sql("standings.sql")

    def match_results(self) -> str:
        return self.read_sql("match_results.sql")

    def team_params(self) -> str:
        return self.read_sql("team_params.sql")

    def team_params_same_venue_average(self) -> str:
        return self.read_sql("team_params_same_venue_average.sql")

    def team_params_weighted_venue_average(self) -> str:
        return self.read_sql("team_params_weighted_venue_average.sql")

    def team_match_counts(self) -> str:
        return self.read_sql("team_match_counts.sql")
