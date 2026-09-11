"""Build docs/reports/model_ledger.html from the comparison exports.

The page is a static template with one `/*__DATA__*/` token; this script
assembles the JSON the page's charts read (constants sweeps, Dixon-Coles
league-only and all-competitions backtests, the chancedegol benchmark) from
`src/files/exports/` and splices it in. Re-run after any of those exports
change; the page carries no numbers of its own except the pooled figures
quoted in prose, which come from FINDINGS.md.

    python docs/reports/build_model_ledger.py
"""

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXPORTS = ROOT / "src" / "files" / "exports"
HERE = Path(__file__).resolve().parent

SWEEPS = {
    "lookback": ("lookback", "lookback"),
    "blend": ("blend", "adjustment_weight"),
    "recency": ("recency_weights", "weights"),
    "prior": ("prior_weight", "prior_weight"),
}


def rows(name):
    with open(EXPORTS / name, newline="") as handle:
        return list(csv.DictReader(handle))


def num(value):
    try:
        return float(value)
    except ValueError:
        return value


def numeric(row):
    return {key: num(value) for key, value in row.items()}


def sweep(stem, column):
    per_season = rows(f"{stem}_sweep_multiseason.csv")
    levels = []
    for pooled in rows(f"{stem}_sweep_pooled.csv"):
        level = pooled[column]
        seasons = [r for r in per_season if r[column] == level]
        levels.append(
            {
                "level": level,
                "n": int(float(pooled["n_scored"])),
                "brier": num(pooled["mean_brier"]),
                "ref": num(pooled["mean_reference_brier"]),
                "skill": num(pooled["skill"]),
                "diff": num(pooled["diff_vs_baseline"]),
                "lo": num(pooled["diff_ci_low"]),
                "hi": num(pooled["diff_ci_high"]),
                "wins": sum(1 for r in seasons if r["beats_baseline"] == "True"),
                "seasons": len(seasons),
                "per_season": [
                    {
                        "season": int(r["season"]),
                        "diff": num(r["diff_vs_baseline"]),
                        "lo": num(r["diff_ci_low"]),
                        "hi": num(r["diff_ci_high"]),
                        "brier": num(r["mean_brier"]),
                        "skill": num(r["skill"]),
                    }
                    for r in seasons
                ],
            }
        )
    return levels


def assemble():
    with open(EXPORTS / "benchmark.json") as handle:
        chancedegol = json.load(handle)
    with open(EXPORTS / "benchmark_pooled.json") as handle:
        chancedegol_pooled = json.load(handle)
    with open(EXPORTS / "benchmark_elo.json") as handle:
        chancedegol_elo = json.load(handle)
    with open(EXPORTS / "benchmark_elo_pooled.json") as handle:
        chancedegol_elo_pooled = json.load(handle)
    return {
        "sweeps": {key: sweep(stem, column) for key, (stem, column) in SWEEPS.items()},
        "dc_league": [numeric(r) for r in rows("dixon_coles_multiseason.csv")],
        "dc_all": [numeric(r) for r in rows("dixon_coles_all_vs_league.csv")],
        "dc_all_pooled": numeric(rows("dixon_coles_all_vs_league_pooled.csv")[0]),
        "four_arms": [numeric(r) for r in rows("four_arms.csv")],
        "four_arms_pooled": numeric(rows("four_arms_pooled.csv")[0]),
        "chancedegol": chancedegol,
        "chancedegol_pooled": chancedegol_pooled,
        "chancedegol_elo": chancedegol_elo,
        "chancedegol_elo_pooled": chancedegol_elo_pooled,
    }


def main():
    template = (HERE / "model_ledger.template.html").read_text()
    assert template.count("/*__DATA__*/") == 1, "template must carry exactly one data token"
    page = template.replace("/*__DATA__*/", json.dumps(assemble()))
    (HERE / "model_ledger.html").write_text(page)
    print(f"wrote {HERE / 'model_ledger.html'} ({len(page):,} bytes)")


if __name__ == "__main__":
    main()
