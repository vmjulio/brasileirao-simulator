"""Build a team-name -> data URI map of club crests for the report.

The Artifact CSP blocks external images, so every crest has to travel inside
the page. Crests come from the bolaondroid project, joined on API-Football's
team id where its team_logos.json knows one - ids are stable across seasons
while names are not, which is the same reason import_seasons.py canonicalises
by id. Names only fill the gap for clubs that file has never seen.

PNGs are downsampled to 48px (they ship up to 336KB, which no page needs for a
16px legend mark); SVGs travel as-is.

Run from the repo, no Docker needed:

    PYTHONPATH=src python3 -m brasileirao_simulator.entrypoints.report.build_logos

`~/py/bolaondroid` is a sibling project on the host, not part of this repo, so
this script only runs where that checkout exists - unlike build_report.py, it
is a data-refresh tool rather than part of the reproducible build.
"""

import base64
import csv
import json
import os
import subprocess
import sys
from pathlib import Path

REPORT_DIR = Path(__file__).resolve().parent
SRC_DIR = REPORT_DIR.parents[2]

LOGO_DIR = os.path.expanduser("~/py/bolaondroid/dist/logos")
DATASETS = SRC_DIR / "files" / "datasets"
OUT = REPORT_DIR / "logos.json"
SIZE = 48

# Clubs whose id team_logos.json does not carry. An empty value means the
# project has no crest for them and the report falls back to a coloured dot.
BY_NAME = {
    "Ceara": "ceara.png",
    "Sport Recife": "sport.png",
    "Fortaleza EC": "fortaleza.png",
    "Goias": "goias.png",
    "Atletico Goianiense": "atlgo.png",
    "America Mineiro": "america-mg.png",
    "Cuiaba": "cuiaba.png",
    "Juventude": "juventude.png",
    "Criciuma": "criciuma.png",
    "Avai": "",
    "CSA": "",
    "Figueirense": "",
    "Parana": "",
    "Ponte Preta": "",
    "Santa Cruz": "",
}


def teams_by_id() -> dict:
    """Every (id, name) pair across all eleven seasons, newest name winning."""
    names = {}
    for season in range(2016, 2027):
        path = DATASETS / str(season) / "fixtures.csv"
        if not path.is_file():
            continue
        with open(path) as f:
            for row in csv.DictReader(f):
                names[row["teams_home_id"]] = row["teams_home_name"]
                names[row["teams_away_id"]] = row["teams_away_name"]
    return names


def data_uri(file_name: str) -> str:
    path = f"{LOGO_DIR}/{file_name}"
    if file_name.endswith(".svg"):
        with open(path, "rb") as f:
            return "data:image/svg+xml;base64," + base64.b64encode(f.read()).decode()

    resized = f"/tmp/logo_{SIZE}_{file_name}"
    subprocess.run(
        ["sips", "-Z", str(SIZE), path, "--out", resized],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    with open(resized, "rb") as f:
        return "data:image/png;base64," + base64.b64encode(f.read()).decode()


def main():
    with open(f"{LOGO_DIR}/team_logos.json") as f:
        by_id = json.load(f)

    logos, missing = {}, []
    for team_id, name in teams_by_id().items():
        file_name = by_id.get(str(team_id)) or BY_NAME.get(name, "")
        if not file_name or not os.path.isfile(f"{LOGO_DIR}/{file_name}"):
            missing.append(name)
            continue
        logos[name] = data_uri(file_name)

    with open(OUT, "w") as f:
        json.dump(logos, f, separators=(",", ":"))

    total = OUT.stat().st_size
    print(f"{len(logos)} crests, {len(missing)} without one: {sorted(missing)}")
    print(f"wrote {OUT} ({total/1e3:.0f} KB, mean {total/max(1,len(logos))/1e3:.1f} KB each)")


if __name__ == "__main__":
    sys.exit(main())
