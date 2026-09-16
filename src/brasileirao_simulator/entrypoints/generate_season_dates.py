"""Write files/datasets/{season}/dates.json from that season's fixtures.

The date list is committed rather than derived at runtime so a season's dates
are inspectable and stable, but generating it avoids transcribing them by hand.
"""

import argparse
import json

import pandas as pd

from brasileirao_simulator.config.settings import DATASETS_PATH
from brasileirao_simulator.domain.season_dates import dates_from_fixtures


def generate_season_dates(season: int) -> list[str]:
    season_dir = f"{DATASETS_PATH}/{season}"
    fixtures = pd.read_csv(f"{season_dir}/fixtures.csv")
    dates = dates_from_fixtures(fixtures)

    with open(f"{season_dir}/dates.json", "w") as f:
        json.dump({"dates": dates}, f, indent=2)

    return dates


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int, required=True)
    args = parser.parse_args()

    written = generate_season_dates(args.season)
    print(f"wrote {len(written)} dates for season {args.season}")
