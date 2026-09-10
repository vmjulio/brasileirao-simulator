"""Which matches count toward a club's cross-competition strength estimate.

`MatchStore` (domain/match_store.py) loads every league id API-Football has
sharded under `src/files/datasets/competitions/{league_id}/{season}.csv`.
This module is the one place that decides which of those leagues, and which
rounds within them, actually inform a rating - a committed config, not
something buried in a query. See
docs/superpowers/specs/2026-09-10-multi-competition-and-elo-design.md,
"The inclusion rules are a committed config".

STAGE SCALE. Every cup round label API-Football has used across seasons
2016-2025 (as shipped in src/files/datasets/competitions/{73,13,11}/*.csv)
falls into one of a small number of stages:

    preliminary   "1st Round" .. "4th Round"        qualifying, before the
                  "Qualification Round N"            group stage forms;
                  "1/256-finals", "1/128-finals",    2026 added continental
                  "Round of 128", "Round of 64"      qualifiers and Copa do
                                                      Brasil's expanded early
                                                      rounds - all still before
                                                      any admission cutoff
    group         "Group A - N" .. "Group H - N",    Libertadores and
                  "Group Stage - N"                  Sudamericana have used
                                                      both label styles
                                                      across seasons
    round_of_32   "Round of 32"                      Sudamericana only
    round_of_16   "Round of 16", "8th Finals"        Copa do Brasil labels
                                                      the same stage both ways
    quarter       "Quarter-finals" / "Quarterfinals"
    semi          "Semi-finals" / "Semifinals"
    final         "Final" / "Finals"

`_stage_rank` maps a label onto this scale; a `RoundFilter` is just the
minimum rank it admits. `GROUP_STAGE` and `ROUND_OF_16` are two named cutoffs
on the same scale - chancedegol's rule is "cups count from the group stage,
or the round of 16 where there is none" (Copa do Brasil never has a group
stage, so its filter cuts at round_of_16 instead).

A round label this scale has never seen raises `ValueError`, on purpose: an
unrecognised stage name from a competition already in `COMPETITIONS` is a
data change worth looking at, not a match that should be silently included
or silently dropped.
"""

import re
from dataclasses import dataclass
from typing import Optional

_PRELIMINARY_ROUND = re.compile(r"^\d+(?:st|nd|rd|th) Round$")
_QUALIFICATION_ROUND = re.compile(r"^Qualification Round \d+$")
# Copa do Brasil's 2026 format labels its early rounds by fraction and by
# field size. All sit before the round of 16, so all are preliminary. Listed
# literally rather than pattern-matched: "1/8-finals" in the same notation
# would be the round of 16, and a blanket rule would misfile it.
_EXPANDED_EARLY_ROUNDS = frozenset({"1/256-finals", "1/128-finals", "Round of 128", "Round of 64"})

_STAGE_RANK_GROUP = 100
_STAGE_RANK_ROUND_OF_32 = 150
_STAGE_RANK_ROUND_OF_16 = 200
_STAGE_RANK_QUARTER = 300
_STAGE_RANK_SEMI = 400
_STAGE_RANK_FINAL = 500


def _stage_rank(round_label: str) -> int:
    """Where one cup round label sits on the stage scale documented above."""
    if _PRELIMINARY_ROUND.match(round_label) or _QUALIFICATION_ROUND.match(round_label):
        return 0
    if round_label in _EXPANDED_EARLY_ROUNDS:
        return 0
    if round_label.startswith("Group"):
        return _STAGE_RANK_GROUP
    if round_label == "Round of 32":
        return _STAGE_RANK_ROUND_OF_32
    if round_label in ("Round of 16", "8th Finals"):
        return _STAGE_RANK_ROUND_OF_16
    if round_label in ("Quarter-finals", "Quarterfinals"):
        return _STAGE_RANK_QUARTER
    if round_label in ("Semi-finals", "Semifinals"):
        return _STAGE_RANK_SEMI
    if round_label in ("Final", "Finals"):
        return _STAGE_RANK_FINAL
    raise ValueError(f"unrecognised league_round label: {round_label!r}")


@dataclass(frozen=True)
class RoundFilter:
    """Admits a match once its `league_round` reaches `minimum_rank` on the
    stage scale `_stage_rank` defines.

    `GROUP_STAGE` and `ROUND_OF_16` below cover the two cutoffs this project
    uses; nothing about the type is specific to either one.
    """

    name: str
    minimum_rank: int

    def admits(self, round_label: str) -> bool:
        return _stage_rank(round_label) >= self.minimum_rank


GROUP_STAGE = RoundFilter("group stage or later", _STAGE_RANK_GROUP)
ROUND_OF_16 = RoundFilter("round of 16 or later", _STAGE_RANK_ROUND_OF_16)


@dataclass(frozen=True)
class Rule:
    """One competition's inclusion rule.

    `name` is stamped into `MatchStore`'s `admitted_by` column, so "why is
    this match in the estimate?" always has an answer.
    """

    name: str
    from_round: Optional[RoundFilter]  # None = every round is admitted
    division: Optional[int]  # 1 Série A, 2 Série B, None for cups
    weight: float = 1.0


COMPETITIONS: dict[int, Rule] = {
    71: Rule("Série A", from_round=None, division=1),
    72: Rule("Série B", from_round=None, division=2),
    73: Rule("Copa do Brasil", from_round=ROUND_OF_16, division=None),
    13: Rule("Libertadores", from_round=GROUP_STAGE, division=None),
    11: Rule("Sudamericana", from_round=GROUP_STAGE, division=None),
}
