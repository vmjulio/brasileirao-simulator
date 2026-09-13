"""Chances of each player winning the bolão.

THE GAME, as the app implements it (`bolao/assets/index-*.js`): the season is
split in halves - rounds 1-19 and 20-38 - and in each half every one of the
twenty clubs belongs to one of the five players, four clubs each. A player
scores the match points its clubs earn (3 for a win, 1 for a draw). A
"dobra" nominates one club in one round, and that club's points count double
in that round; every player gets four per half (the pattern in 2021-2023 and
in this season's first half), and an unused one does not carry over.

THE SOURCE OF TRUTH is the bundle the app itself reads
(`app/football/consumer_bundle.json`): it carries, per club-side of every
fixture, who owns it (`punter`), whether that round was doubled
(`is_double`) and the points it earned. Standings and ownership are taken
from there rather than recomputed, so this cannot drift from the app - and
it is how the round-20 exception was found: nobody owns a club in round 20,
the round the second-half draft happens in, so it scores for no one.

WHY A FRESH SIMULATION. The published archive stores aggregates - how often
each club finished in each place, how often each fixture ended home/draw/away
- and those cannot be recombined into a player's total, because a player's
four clubs play each other and their results are correlated. This runs the
remaining fixtures keeping each simulated season whole, so every player's
total comes from one coherent set of results.

The forecast itself is unchanged: the same Elo lambdas the explorer uses,
read as of the last played date.

    PYTHONPATH=. python -m brasileirao_simulator.entrypoints.bolao_odds --iterations 20000
"""

import argparse
import collections
import csv
import json

import math

import numpy as np
import pandas as pd

from brasileirao_simulator.adapters.elo_adapter import EloAdapter
from brasileirao_simulator.config.settings import DATASETS_PATH, EXPORTS_PATH
from brasileirao_simulator.domain.season_data import SeasonData
from brasileirao_simulator.domain.tables import Tables

BOLAO_FILES = "/Users/vmjulio/Documents/GitHub/lean-pype/app/files/"
BUNDLE_URL = "http://vmj-lake.s3-website-us-east-1.amazonaws.com/app/football/consumer_bundle.json"
FIRST_HALF_LAST_ROUND = 19
DOUBLES_PER_HALF = 4


def _round_of(label: str):
    return int(float(label.split(" - ")[-1])) if " - " in label else None


def season_rows(season: int) -> list:
    with open(f"{DATASETS_PATH}/{season}/fixtures.csv", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def bundle(path_or_url: str = BUNDLE_URL) -> dict:
    """The app's own data. A local path is read directly; anything else is fetched."""
    if not path_or_url.startswith("http"):
        with open(path_or_url, encoding="utf-8") as f:
            return json.load(f)
    import urllib.request

    with urllib.request.urlopen(path_or_url, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def official_state(data: dict) -> tuple:
    """(points by player, matches counted, points from doubles, {(round, team_id): player})
    exactly as the app computes them."""
    points, counted, extra, owner = (collections.Counter(), collections.Counter(),
                                     collections.Counter(), {})
    for row in data["enriched_tidy_fixtures"]:
        player = row.get("punter")
        if not player:
            continue                      # round 20: the second-half draft round, nobody scores
        owner[(row["round_"], int(row["team_id"]))] = player
        if row.get("goals_for") is None:
            continue
        points[player] += row["points"]
        extra[player] += row["points"] - row["league_points"]
        counted[player] += 1
    return points, counted, extra, owner


def squads_and_doubles(season: int) -> tuple:
    """({(half, team_id): player}, {player: {(round, team_id)}}) for the season."""
    owner, doubles = {}, collections.defaultdict(set)
    with open(BOLAO_FILES + f"processed_punters_{season}_71.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            owner[(int(row["turn"]), int(row["team_id"]))] = row["name"]
    with open(BOLAO_FILES + f"processed_doubles_{season}_71.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if int(row["season"]) == season:
                doubles[row["name"]].add((int(row["round"]), int(row["team_id"])))
    return owner, doubles


def standings_so_far(rows: list, owner: dict, doubles: dict) -> tuple:
    """(points by player, matches counted, points from doubles) over played matches."""
    points, counted, extra = collections.Counter(), collections.Counter(), collections.Counter()
    for row in rows:
        if row["fixture_status_short"] not in ("FT", "AET", "PEN"):
            continue
        rnd = _round_of(row["league_round"])
        half = 1 if rnd <= FIRST_HALF_LAST_ROUND else 2
        home, away = int(float(row["teams_home_id"])), int(float(row["teams_away_id"]))
        goals_home, goals_away = int(float(row["score_fulltime_home"])), int(float(row["score_fulltime_away"]))
        for team, scored in ((home, goals_home - goals_away), (away, goals_away - goals_home)):
            player = owner.get((half, team))
            if player is None:
                continue
            earned = 3 if scored > 0 else 1 if scored == 0 else 0
            doubled = (rnd, team) in doubles.get(player, set())
            points[player] += earned * (2 if doubled else 1)
            extra[player] += earned if doubled else 0
            counted[player] += 1
    return points, counted, extra


def remaining_with_lambdas(season: int) -> list:
    """[(round, home_id, away_id, lambda_home, lambda_away)] for every unplayed match."""
    tables = Tables(SeasonData(season))
    played = [r for r in season_rows(season) if r["fixture_status_short"] in ("FT", "AET", "PEN")]
    as_of = str(pd.to_datetime(max(r["fixture_date"] for r in played), format="mixed").date())
    adapter = EloAdapter("average", season)
    fixtures = tables.enriched_tidy_fixtures(blank_from_date=as_of)
    remaining = tables.remaining_games(blank_from_date=as_of)
    baseline = adapter.build_baseline(fixtures.copy(), remaining.copy())

    by_name = {}
    for row in season_rows(season):
        by_name[(row["teams_home_name"], row["teams_away_name"])] = row
    out = []
    for home_name, away_name, lam_home, lam_away in zip(
        baseline.home_name, baseline.away_name, baseline.lam_home, baseline.lam_away
    ):
        row = by_name[(home_name, away_name)]
        if row["fixture_status_short"] in ("FT", "AET", "PEN"):
            continue
        out.append((_round_of(row["league_round"]), int(float(row["teams_home_id"])),
                    int(float(row["teams_away_id"])), float(lam_home), float(lam_away)))
    return out


def planned_doubles(fixtures: list, owner: dict, doubles: dict, players: list) -> dict:
    """{player: {(round, team)}} for the doubles still to be spent this half.

    A player cannot know results in advance, but does know the fixtures, so
    the assumption here is that the remaining doubles go on the matches where
    that player's clubs are likeliest to take points - expected points from
    the same Poisson rates the simulation uses. It is an assumption, and the
    caller reports the figures with and without it.
    """
    spent = {p: sum(1 for rnd, _ in doubles.get(p, set()) if rnd > FIRST_HALF_LAST_ROUND) for p in players}
    candidates = collections.defaultdict(list)
    for rnd, home, away, lam_home, lam_away in fixtures:
        half = 1 if rnd <= FIRST_HALF_LAST_ROUND else 2
        for team, lam_for, lam_against in ((home, lam_home, lam_away), (away, lam_away, lam_home)):
            player = owner.get((rnd, team))
            if player is None:
                continue
            # P(win) and P(draw) under two independent Poissons, summed over
            # scores up to a generous ceiling.
            win = draw = 0.0
            for mine in range(9):
                p_mine = math.exp(-lam_for) * lam_for ** mine / math.factorial(mine)
                for theirs in range(9):
                    p_theirs = math.exp(-lam_against) * lam_against ** theirs / math.factorial(theirs)
                    if mine > theirs:
                        win += p_mine * p_theirs
                    elif mine == theirs:
                        draw += p_mine * p_theirs
            candidates[player].append((3 * win + draw, rnd, team))
    planned = {}
    for player in players:
        left = max(0, DOUBLES_PER_HALF - spent.get(player, 0))
        best = sorted(candidates[player], reverse=True)[:left]
        planned[player] = {(rnd, team) for _, rnd, team in best}
    return planned


def points_by_round(data: dict, players: list) -> dict:
    """{player: [points after each round]} over the rounds already played, so
    the page can draw the race as it happened."""
    per_round = collections.defaultdict(collections.Counter)
    last = 0
    for row in data["enriched_tidy_fixtures"]:
        player = row.get("punter")
        if not player or row.get("goals_for") is None:
            continue
        per_round[row["round_"]][player] += row["points"]
        last = max(last, row["round_"])
    running, out = collections.Counter(), {p: [] for p in players}
    for rnd in range(1, last + 1):
        for player in players:
            running[player] += per_round.get(rnd, {}).get(player, 0)
            out[player].append(int(running[player]))
    return {"rounds": list(range(1, last + 1)), "points": out}


def doubles_detail(data: dict) -> list:
    """Every double already spent, with what it was worth."""
    out = []
    for row in data["enriched_tidy_fixtures"]:
        if row.get("is_double") and row.get("goals_for") is not None:
            out.append({"player": row["punter"], "round": row["round_"], "team": row["team_name"],
                        "opponent": row["opponent_name"], "gained": row["points"] - row["league_points"],
                        "score": f"{int(row['goals_for'])}-{int(row['goals_against'])}"})
    return sorted(out, key=lambda r: r["round"])


def simulate(season: int, iterations: int, seed: int = 7, use_planned_doubles: bool = True,
             source: str = BUNDLE_URL) -> dict:
    data = bundle(source)
    points, counted, extra, owner_by_round = official_state(data)
    _, doubles = squads_and_doubles(season)
    players = sorted(points, key=lambda p: -points[p])

    fixtures = remaining_with_lambdas(season)
    # Ownership is per round in the bundle, which is what the round-20 gap needs.
    owner = {(1 if rnd <= FIRST_HALF_LAST_ROUND else 2, team): who
             for (rnd, team), who in owner_by_round.items()}
    planned = (planned_doubles(fixtures, owner_by_round, doubles, players) if use_planned_doubles
               else {p: set() for p in players})
    rng = np.random.default_rng(seed)
    totals = np.array([[points[p]] * iterations for p in players], dtype=float)

    for rnd, home, away, lam_home, lam_away in fixtures:
        goals_home = rng.poisson(lam_home, iterations)
        goals_away = rng.poisson(lam_away, iterations)
        half = 1 if rnd <= FIRST_HALF_LAST_ROUND else 2
        for team, mine, theirs in ((home, goals_home, goals_away), (away, goals_away, goals_home)):
            player = owner_by_round.get((rnd, team))
            if player is None:
                continue
            earned = np.where(mine > theirs, 3.0, np.where(mine == theirs, 1.0, 0.0))
            if (rnd, team) in doubles.get(player, set()) or (rnd, team) in planned[player]:
                earned *= 2
            totals[players.index(player)] += earned

    history = points_by_round(data, players)
    doubles_used = doubles_detail(data)
    best = totals.max(axis=0)
    winners = (totals == best)
    tied = winners.sum(axis=0) > 1
    result = {
        "season": season, "iterations": iterations,
        "remaining_matches": len(fixtures),
        "tie_at_the_top": float(tied.mean() * 100),
        "planned_doubles": {p: sorted(planned[p]) for p in players},
        "doubles_left": {p: max(0, DOUBLES_PER_HALF - sum(1 for rnd, _ in doubles.get(p, set())
                                                          if rnd > FIRST_HALF_LAST_ROUND)) for p in players},
        "history": history,
        "doubles_used": doubles_used,
        "players": [],
    }
    for i, player in enumerate(players):
        outright = float(((totals[i] == best) & ~tied).mean() * 100)
        shared = float(((totals[i] == best) & tied).mean() * 100)
        counts, edges = np.histogram(totals[i], bins=range(int(totals.min()) - 1, int(totals.max()) + 3, 3))
        result["players"].append({
            "histogram": {"from": int(edges[0]), "width": 3, "counts": [int(c) for c in counts]},
            "player": player,
            "points_now": int(points[player]),
            "matches_counted": int(counted[player]),
            "points_from_doubles": int(extra[player]),
            "mean_final": float(totals[i].mean()),
            "p5": float(np.percentile(totals[i], 5)),
            "p95": float(np.percentile(totals[i], 95)),
            "win": outright + shared,
            "win_outright": outright,
            "win_shared": shared,
        })
    result["players"].sort(key=lambda r: -r["win"])
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--season", type=int, default=2026)
    parser.add_argument("--iterations", type=int, default=20000)
    parser.add_argument("--out", default=f"{EXPORTS_PATH}/bolao_odds.json")
    parser.add_argument("--source", default=BUNDLE_URL, help="the app's bundle: a URL or a local path")
    parser.add_argument("--no-planned-doubles", action="store_true",
                        help="ignore the doubles each player still has to spend")
    args = parser.parse_args()

    result = simulate(args.season, args.iterations, use_planned_doubles=not args.no_planned_doubles, source=args.source)
    with open(args.out, "w") as f:
        json.dump(result, f, indent=1)

    print(f"{result['remaining_matches']} partidas restantes, {result['iterations']:,} temporadas simuladas\n")
    header = f"{'jogador':<10}{'hoje':>6}{'media':>8}{'faixa 90%':>14}{'titulo':>9}"
    print(header)
    for r in result["players"]:
        span = f"{r['p5']:.0f}-{r['p95']:.0f}"
        print(f"{r['player']:<10}{r['points_now']:>6}{r['mean_final']:>8.1f}{span:>14}{r['win']:>8.1f}%")
    print(f"\nempate na lideranca: {result['tie_at_the_top']:.1f}%")
