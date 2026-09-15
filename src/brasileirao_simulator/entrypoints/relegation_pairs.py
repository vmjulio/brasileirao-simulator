"""How often two clubs share a fate: neither, one, or both relegated.

    docker-compose run --rm --entrypoint "" app python \\
      brasileirao_simulator/entrypoints/relegation_pairs.py \\
      --season 2026 --date 2026-09-14 --clubs Gremio Internacional

WHY A SEPARATE RUN. The stored results keep per-club totals only - how many
seasons each club went down in - so a joint question ("neither of these two")
cannot be read back from them. Every simulated batch does carry each season's
full final table (`BatchOutcome.rank`); the counters behind the stored results
sum it and discard the rows. This script keeps the rows for one question.

READ-ONLY BY CONSTRUCTION. It builds the same inputs `SimulationService` does
and calls the model's `simulate_batch` directly, never `SimulationRunner`, the
only thing that loads or writes the stored pickles. The page's numbers and the
archive are untouched. Its seasons are a fresh draw, statistically equivalent
to the stored ones but not the same seasons; the per-club rates it prints are
there to check that equivalence.
"""

import argparse
import datetime
import json
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from brasileirao_simulator.config.explorer_models import explorer_model
from brasileirao_simulator.domain.season_data import SeasonData
from brasileirao_simulator.domain.season_dates import utc_cutoff
from brasileirao_simulator.domain.simulation_params import SimulationParams
from brasileirao_simulator.domain.tables import Tables
from brasileirao_simulator.entrypoints.simulators import simulator_for

RELEGATION_PLACES = 4
BATCH_SIZE = 100


def round_as_of(fixtures: pd.DataFrame, date: str) -> Optional[int]:
    """The furthest round with a result by the end of local `date` - the round
    a piece dated `date` is "after"."""
    next_day = str((pd.Timestamp(date) + pd.Timedelta(days=1)).date())
    kickoff = pd.to_datetime(fixtures["fixture_date"], utc=True, format="mixed")
    played = fixtures[fixtures["goals_home"].notnull() & (kickoff < utc_cutoff(next_day))]
    if played.empty:
        return None
    return int(played["league_round"].map(lambda label: int("".join(c for c in str(label) if c.isdigit()) or 0)).max())


def data_document(season: int, date: str, clubs: tuple, counts: dict, round_: Optional[int], generated_at: str) -> dict:
    """The `data.json` an analysis piece reads (see docs/superpowers/specs/2026-09-15-analise-pages-design.md)."""
    a, b = clubs
    return {
        "question": "relegation_pairs", "season": season, "as_of": date, "round": round_,
        "clubs": [a, b],
        "counts": {"neither": counts["neither"], "only_a": counts["only_" + a],
                   "only_b": counts["only_" + b], "both": counts["both"]},
        "generated_by": "relegation_pairs.py", "generated_at": generated_at,
    }


def relegation_pairs(season: int, date: str, clubs: tuple[str, str], iterations: int, model: str = "elo") -> dict:
    """Counts of simulated seasons, as of `date`, by which of the two `clubs`
    finished in the bottom four: neither, only the first, only the second, both."""
    tables = Tables(SeasonData(season))
    fixtures = tables.enriched_tidy_fixtures(blank_from_date=date)
    remaining = tables.remaining_games(blank_from_date=date)
    simulator = simulator_for(explorer_model(model).simulator, SimulationParams(season=season).strategy, season)

    a_down = b_down = None
    counts = {"neither": 0, "only_" + clubs[0]: 0, "only_" + clubs[1]: 0, "both": 0}
    done = 0
    while done < iterations:
        outcome = simulator.simulate_batch(fixtures, remaining, min(BATCH_SIZE, iterations - done))
        teams = outcome.baseline.teams
        missing = [c for c in clubs if c not in teams]
        if missing:
            raise ValueError(f"not in the {season} table: {missing}; clubs are {teams}")
        last_safe = len(teams) - RELEGATION_PLACES
        a_down = outcome.rank[:, teams.index(clubs[0])] > last_safe
        b_down = outcome.rank[:, teams.index(clubs[1])] > last_safe
        counts["neither"] += int(np.sum(~a_down & ~b_down))
        counts["only_" + clubs[0]] += int(np.sum(a_down & ~b_down))
        counts["only_" + clubs[1]] += int(np.sum(~a_down & b_down))
        counts["both"] += int(np.sum(a_down & b_down))
        done += len(a_down)
    return counts


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--date", required=True, help="as-of date, local, as in the forecast archive")
    parser.add_argument("--clubs", nargs=2, required=True, help="canonical names, e.g. Gremio Internacional")
    parser.add_argument("--iterations", type=int, default=20000)
    parser.add_argument("--model", default="elo")
    parser.add_argument("--out", default=None, help="write the analysis data.json here")
    args = parser.parse_args()

    counts = relegation_pairs(args.season, args.date, tuple(args.clubs), args.iterations, args.model)
    total = sum(counts.values())
    a, b = args.clubs
    print(f"{total:,} simulated seasons as of {args.date}")
    for key, value in counts.items():
        print(f"  {key:<24} {value:>7,}  {100 * value / total:5.1f}%")
    print(f"  {a} relegated: {100 * (counts['only_' + a] + counts['both']) / total:.1f}%   "
          f"{b} relegated: {100 * (counts['only_' + b] + counts['both']) / total:.1f}%")

    if args.out:
        doc = data_document(args.season, args.date, tuple(args.clubs), counts,
                            round_as_of(SeasonData(args.season).fixtures, args.date),
                            datetime.date.today().isoformat())
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {args.out}")
