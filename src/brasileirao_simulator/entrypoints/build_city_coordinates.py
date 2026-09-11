"""Build `files/datasets/geo/city_coordinates.json`: every venue city string that
appears in the domestic competitions (Série A, Série B, Copa do Brasil), keyed
exactly as it appears in the fixtures, mapped to its IBGE municipality and
coordinates.

Source: the IBGE municipality and state tables as published by
github.com/kelvins/municipios-brasileiros (`csv/municipios.csv`,
`csv/estados.csv`), copied into `files/datasets/geo/`. City-level precision is
the point: travel between Belém and Porto Alegre is ~3,200 km, and where a
stadium sits inside its city does not matter at that scale.

The fixtures spell one city several ways ("Sao Paulo", "São Paulo",
"São Paulo, São Paulo"), so matching strips accents and case. A string that
names its state is matched on name and state. A bare name is matched when it
is unique in Brazil; when it is not (Belém exists in PA, PB and AL), the state
is taken from where the home clubs playing there otherwise play, and
`OVERRIDES` settles what that cannot. Strings that name only a state stay
unmapped rather than get an invented point.

    PYTHONPATH=. python -m brasileirao_simulator.entrypoints.build_city_coordinates
"""

import collections
import csv
import glob
import json
import re
import unicodedata

from brasileirao_simulator.config.settings import DATASETS_PATH

GEO_DIR = f"{DATASETS_PATH}/geo"
OUT = f"{GEO_DIR}/city_coordinates.json"
SOURCE = "https://github.com/kelvins/municipios-brasileiros (IBGE municipality and state tables)"

# (normalised name, UF) for strings the rules above cannot resolve.
OVERRIDES = {
    # Administrative regions of Brasília, not municipalities in IBGE's table.
    "ceilandia, distrito federal": ("brasilia", "DF"),
    "gama": ("brasilia", "DF"),
    "gama, distrito federal": ("brasilia", "DF"),
    "paranoa, distrito federal": ("brasilia", "DF"),
    "taguatinga, distrito federal": ("brasilia", "DF"),
    "vila planalto, distrito federal": ("brasilia", "DF"),
    # Spellings the API gets wrong.
    "chrissyuma": ("criciuma", "SC"),
    "salvador de bahia, bahia": ("salvador", "BA"),
    "sao mateus, maranhao": ("sao mateus do maranhao", "MA"),
    # Bare names found in several states, settled by the club that plays there.
    "belem": ("belem", "PA"),            # Remo, Paysandu (PA; also a Belém in PB and AL)
    "boa vista": ("boa vista", "RR"),    # Baré (RR; also PB)
    "rio claro": ("rio claro", "SP"),    # Velo Clube (SP; also RJ)
    "bonito": ("bonito", "PE"),          # Maguary (PE; also BA, PA, MS)
}
# A state with no city: no point to give it.
STATE_ONLY = {"maranhao", "minas gerais"}


def norm(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", ascii_text.lower().replace("-", " ")).strip()


def load_ibge(geo_dir: str = GEO_DIR):
    """(municipalities by normalised name -> [row], state full name -> UF, UF -> region)."""
    with open(f"{geo_dir}/estados.csv", encoding="utf-8-sig") as f:
        states = list(csv.DictReader(f))
    uf_by_code = {s["codigo_uf"]: s["uf"] for s in states}
    uf_by_name = {norm(s["nome"]): s["uf"] for s in states}
    region_by_uf = {s["uf"]: s["regiao"] for s in states}
    by_name = collections.defaultdict(list)
    with open(f"{geo_dir}/municipios.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            row["uf"] = uf_by_code[row["codigo_uf"]]
            by_name[norm(row["nome"])].append(row)
    return by_name, uf_by_name, region_by_uf


def venue_rows(datasets_path: str = DATASETS_PATH) -> list:
    paths = glob.glob(f"{datasets_path}/competitions/7[123]/*.csv") + glob.glob(f"{datasets_path}/20*/fixtures.csv")
    rows = []
    for path in paths:
        with open(path, encoding="utf-8") as f:
            rows += [r for r in csv.DictReader(f) if (r.get("fixture_venue_city") or "").strip()]
    return rows


def build(datasets_path: str = DATASETS_PATH, geo_dir: str = GEO_DIR) -> dict:
    by_name, uf_by_name, region_by_uf = load_ibge(geo_dir)
    rows = venue_rows(datasets_path)
    cities = sorted({r["fixture_venue_city"].strip() for r in rows})

    def pick(name: str, uf: str):
        found = [m for m in by_name.get(name, []) if m["uf"] == uf]
        return found[0] if found else None

    # First pass: every string that names a state, or a name unique in Brazil.
    resolved, pending = {}, []
    for city in cities:
        key = norm(city)
        name, _, state = (part.strip() for part in key.partition(","))
        if key in OVERRIDES:
            resolved[city] = pick(*OVERRIDES[key])
        elif key in STATE_ONLY:
            continue
        elif state and state in uf_by_name:
            resolved[city] = pick(name, uf_by_name[state])
        elif len(by_name.get(name, [])) == 1:
            resolved[city] = by_name[name][0]
        else:
            pending.append(city)

    # Second pass: an ambiguous bare name takes the state its home clubs
    # otherwise play in.
    home_ufs = collections.defaultdict(collections.Counter)
    for r in rows:
        place = resolved.get(r["fixture_venue_city"].strip())
        if place:
            home_ufs[r["teams_home_name"]][place["uf"]] += 1
    unresolved = []
    for city in pending:
        name = norm(city).partition(",")[0].strip()
        candidates = {m["uf"] for m in by_name.get(name, [])}
        votes = collections.Counter()
        for r in rows:
            if r["fixture_venue_city"].strip() == city:
                for uf, n in home_ufs[r["teams_home_name"]].items():
                    if uf in candidates:
                        votes[uf] += n
        if votes:
            resolved[city] = pick(name, votes.most_common(1)[0][0])
        else:
            unresolved.append(city)

    mapped = {
        city: {
            "municipality": m["nome"],
            "uf": m["uf"],
            "region": region_by_uf[m["uf"]],
            "lat": float(m["latitude"]),
            "lon": float(m["longitude"]),
            "ibge": int(m["codigo_ibge"]),
        }
        for city, m in resolved.items()
        if m is not None
    }
    unmapped = sorted(set(cities) - set(mapped))
    return {"source": SOURCE, "cities": dict(sorted(mapped.items())), "unmapped": unmapped}


if __name__ == "__main__":
    result = build()
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print(f"mapped {len(result['cities'])} city strings, unmapped {len(result['unmapped'])}: {result['unmapped']}")
    print(f"wrote {OUT}")
