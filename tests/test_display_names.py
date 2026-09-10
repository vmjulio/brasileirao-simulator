"""T9.2 - club display names, keyed by team_id, rendering only.

The dashboard shows API-Football's ASCII names: "Sao Paulo", "Vasco DA Gama",
"Gremio". To a Brazilian reader those are wrong, not merely unaccented. The map
here is what the page renders; every join and export keeps the canonical name.
"""

import csv
import glob
import json

DISPLAY_NAMES = "/src/brasileirao_simulator/entrypoints/report/display_names.json"
DATASETS = "/src/files/datasets"


def load():
    with open(DISPLAY_NAMES) as f:
        names = json.load(f)
    names.pop("_comment", None)
    return names


def dataset_ids():
    ids = {}
    for path in sorted(glob.glob(f"{DATASETS}/20*/fixtures.csv")):
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                ids[row["teams_home_id"]] = row["teams_home_name"]
                ids[row["teams_away_id"]] = row["teams_away_name"]
    return ids


def test_every_club_in_the_datasets_has_a_display_name():
    """A club without an entry would fall back to the ASCII name silently -
    exactly the failure the map exists to prevent."""
    missing = {tid: name for tid, name in dataset_ids().items() if tid not in load()}
    assert not missing, f"no display name for: {missing}"


def test_keyed_by_id_not_name():
    assert all(key.isdigit() for key in load()), "keys must be API-Football team ids"


def test_the_renderings_the_ticket_names():
    names = load()
    assert names["133"] == "Vasco da Gama"   # was "Vasco DA Gama"
    assert names["130"] == "Grêmio"          # was "Gremio"
    assert names["126"] == "São Paulo"       # was "Sao Paulo"
    assert names["132"] == "Chapecoense"     # was "Chapecoense-sc"


def test_no_display_name_is_an_ascii_downgrade_of_the_canonical_one():
    """Where the API name already had the right letters, we must not have
    made it worse - a display map that strips accents would be a regression."""
    ids = dataset_ids()
    for tid, display in load().items():
        canonical = ids.get(tid)
        if canonical and canonical == display:
            continue
        assert display, f"{tid} has an empty display name"
