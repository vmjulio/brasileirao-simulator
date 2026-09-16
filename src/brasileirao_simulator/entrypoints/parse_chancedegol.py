"""Parse a chancedegol season page into the CSV benchmark_chancedegol.py reads."""
import collections, csv, html, re, sys, unicodedata

# Our canonical names, from the datasets. Anything not here is matched by
# stripping accents and case; these are the ones where the two sources
# genuinely disagree on the name rather than just on diacritics.
OVERRIDES = {
    "athletico pr": "Atletico Paranaense",
    "atletico pr": "Atletico Paranaense",
    "atletico go": "Atletico Goianiense",
    "atletico mg": "Atletico-MG",
    "america mg": "America Mineiro",
    "red bull bragantino": "RB Bragantino",
    "bragantino": "RB Bragantino",
    "vasco": "Vasco DA Gama",
    "fortaleza": "Fortaleza EC",
    "sport": "Sport Recife",
    "chapecoense": "Chapecoense-sc",
    "sao paulo": "Sao Paulo",
    "gremio": "Gremio",
    "goias": "Goias",
    "ceara": "Ceara",
    "cuiaba": "Cuiaba",
    "criciuma": "Criciuma",
    "vitoria": "Vitoria",
    "avai": "Avai",
    "parana": "Parana",
}

def plain(name):
    n = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z ]", "", n.lower()).strip()

def canonical(name, known):
    key = plain(name)
    if key in OVERRIDES:
        return OVERRIDES[key]
    for team in known:
        if plain(team) == key:
            return team
    return name

def parse(path, known):
    raw = open(path, "rb").read()
    # Archived copies are served latin-1 despite the meta tag; decoding them as
    # utf-8 turns every accented club name into replacement characters, which
    # then fail to match our canonical names.
    try:
        text = raw.decode("utf-8")
        if "\ufffd" in text:
            raise UnicodeDecodeError("utf-8", b"", 0, 1, "replacement chars")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    records, unmatched = [], set()
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", text, re.S | re.I):
        cells = [html.unescape(re.sub(r"<[^>]+>", "", c)).replace("\xa0", " ").strip()
                 for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S | re.I)]
        if len(cells) != 7 or not re.match(r"^\d+x\d+$", cells[2]) or not cells[4].endswith("%"):
            continue
        gh, ga = map(int, cells[2].split("x"))
        home, away = canonical(cells[1], known), canonical(cells[3], known)
        for original, mapped in ((cells[1], home), (cells[3], away)):
            if mapped not in known:
                unmatched.add(original)
        probs = [float(c.replace("%", "").replace(",", ".").strip()) / 100 for c in cells[4:7]]
        records.append({
            "date": cells[0], "home": home, "away": away,
            "goals_home": gh, "goals_away": ga,
            "outcome": "home" if gh > ga else ("draw" if gh == ga else "away"),
            "p_home": probs[0], "p_draw": probs[1], "p_away": probs[2],
        })
    # Their pages occasionally list the same fixture twice with different
    # probabilities (2022 Santos x Coritiba). There is no principled way to pick
    # one, and the scorer keys on home-x-away so it would silently take the last.
    # Drop both copies and report it rather than choose arbitrarily.
    seen = collections.Counter((r["home"], r["away"]) for r in records)
    duplicated = {k for k, n in seen.items() if n > 1}
    records = [r for r in records if (r["home"], r["away"]) not in duplicated]
    return records, unmatched, duplicated

if __name__ == "__main__":
    season, path, out = int(sys.argv[1]), sys.argv[2], sys.argv[3]
    known = set()
    with open(f"/Users/vmjulio/Documents/GitHub/brasileirao-simulator/src/files/datasets/{season}/fixtures.csv") as f:
        for row in csv.DictReader(f):
            known |= {row["teams_home_name"], row["teams_away_name"]}
    records, unmatched, duplicated = parse(path, known)
    with open(out, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(records[0].keys()))
        writer.writeheader()
        writer.writerows(records)
    sums = [abs(r["p_home"] + r["p_draw"] + r["p_away"] - 1) for r in records]
    print(f"{season}: {len(records)} matches -> {out}; "
          f"max |prob sum - 1| {max(sums):.4f}; unmatched teams: {sorted(unmatched) or 'none'}; "
          f"dropped duplicates: {sorted(duplicated) or 'none'}")
