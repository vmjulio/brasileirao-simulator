"""Libertadores and Sudamericana seasons API-Football does not have (it lists
2019 onward), rebuilt from English Wikipedia's season pages into the same
shard format.

Output: `files/datasets/wikipedia/{league}/{season}.csv` (13 Libertadores,
11 Sudamericana), one row per match - Libertadores from the group stage on,
Sudamericana every round - with the columns `MatchStore` reads. The directory is kept
apart from `files/datasets/competitions/` on purpose: the Elo replay reads
every shard under that tree, so admitting these seasons changes every Elo
rating from 2015 on (ratings carry forward) and with it every committed Elo
export and the explorer's Elo archive. That is a separate decision; until it is
taken, only code that asks for these shards reads them.

Source: each match on those pages is a `{{Football box}}` with the date, the
local kick-off and its UTC offset (`{{UTZ|21:00|-3}}`), both teams, the score,
the goals with their minutes and the stadium. The raw wikitext used is copied
to `files/datasets/wikipedia/raw/` (Wikipedia text is CC BY-SA 4.0).

Club ids. Brazilian clubs are named explicitly (`BRAZILIAN_NAMES`) and must
hit the id the rest of the data uses. Foreign clubs are *not* matched by name -
names alone put the Chilean Universidad Católica on the Ecuadorian one. Their
ids are learned by pairing Wikipedia's matches with the API's on the exact UTC
kick-off minute, across every continental season both hold (LEARN_PAGES:
Libertadores and Sudamericana 2020-2025, qualifying rounds included); a
club in none of them gets a synthetic id.

Checks, run every build (`--check`, results in `build_report.json`):
  - 2019 of both cups, held out of the id learning: every match
    must have an API partner at its minute, with the same 90-minute score and
    round label, and every learned id must equal the API's 2019 id;
  - 2014-2018 internally: Libertadores 96 group matches (8 groups of 12, each
    team six); every knockout tie two legs; a kick-off time on every match;
  - no Brazilian club has a continental kick-off within 48 hours of one of its
    own Série A kick-offs (we hold those exactly).

Scores are the 90-minute result (`MatchStore` scores every match at 90'): a
match that went to extra time is re-scored from the goal minutes. Awarded and
suspended matches get status `AWD` and cancelled ones `CANC`; `MatchStore`
drops both, as it does for the API's.

    PYTHONPATH=. python -m brasileirao_simulator.entrypoints.build_wikipedia_continental --check
"""

import argparse
import collections
import csv
import datetime as dt
import difflib
import glob
import json
import os
import re
import shutil
import unicodedata

from brasileirao_simulator.config.settings import DATASETS_PATH

OUT_DIR = f"{DATASETS_PATH}/wikipedia"
LIBERTADORES, SUDAMERICANA = 13, 11
BUILD = tuple((league, season) for league in (LIBERTADORES, SUDAMERICANA) for season in range(2014, 2019))
VALIDATION = ((LIBERTADORES, 2019), (SUDAMERICANA, 2019))
# Pages covering seasons API-Football also holds, used only to learn each
# foreign club's API id by pairing matches on their exact kick-off: every
# season of both cups but 2019 (held out as the check), qualifying rounds
# included - a club knocked out early appears nowhere else.
_SUD_EARLY = ["first stage", "second stage", "final stages", "final"]
_SUD_GROUPS = ["first stage", "group stage", "final stages", "final"]
_LIB = ["qualifying stages", "group stage", "final stages", "final"]
LEARN_PAGES = {
    **{(LIBERTADORES, y): [f"{y} Copa Libertadores {p}" for p in _LIB] for y in (2020, 2021, 2022, 2023, 2024, 2025)},
    (SUDAMERICANA, 2020): [f"2020 Copa Sudamericana {p}" for p in _SUD_EARLY],
    **{(SUDAMERICANA, y): [f"{y} Copa Sudamericana {p}" for p in _SUD_GROUPS] for y in (2021, 2022, 2023, 2024, 2025)},
}
# Synthetic fixture ids never collide with an API id, nor across the two cups.
SYNTHETIC_FIXTURE_BASE = {LIBERTADORES: 9_000_000_000, SUDAMERICANA: 9_500_000_000}
SYNTHETIC_TEAM_BASE = 8_000_000

# Page titles differ by format: until 2016 the Libertadores group stage was
# the "second stage", and the Sudamericana's early rounds shared one page.
_SUD_ELIMINATION = {2014: "elimination phase", 2015: "elimination stages", 2016: "elimination stages"}
PAGES = {
    (LIBERTADORES, 2014): ["2014 Copa Libertadores second stage", "2014 Copa Libertadores knockout stage",
                           "2014 Copa Libertadores finals"],
    (LIBERTADORES, 2015): ["2015 Copa Libertadores second stage", "2015 Copa Libertadores final stages"],
    (LIBERTADORES, 2016): ["2016 Copa Libertadores second stage", "2016 Copa Libertadores final stages"],
    **{(LIBERTADORES, y): [f"{y} Copa Libertadores {p}" for p in ("group stage", "final stages", "finals")] for y in (2017, 2018)},
    (LIBERTADORES, 2019): ["2019 Copa Libertadores group stage", "2019 Copa Libertadores final stages", "2019 Copa Libertadores final"],
    **{(SUDAMERICANA, y): [f"{y} Copa Sudamericana {p}" for p in (stage, "final stages", "finals")]
       for y, stage in _SUD_ELIMINATION.items()},
    **{(SUDAMERICANA, y): [f"{y} Copa Sudamericana {p}" for p in ("first stage", "second stage", "final stages", "finals")]
       for y in (2017, 2018)},
    (SUDAMERICANA, 2019): [f"2019 Copa Sudamericana {p}" for p in _SUD_EARLY],
}
# Brazilian clubs must land on exactly the id the rest of the data uses, so
# they are named, not guessed: Wikipedia's shown name (normalised) -> the
# dataset's name. Fuzzy matching alone once preferred América Mineiro for
# Atlético Mineiro.
BRAZILIAN_NAMES = {
    "atletico mineiro": "Atletico-MG",
    "atletico paranaense": "Atletico Paranaense",
    "vasco gama": "Vasco DA Gama",
    "chapecoense": "Chapecoense-sc",
    "sao paulo": "Sao Paulo",
    "gremio": "Gremio",
}
# Names the data gives two ids: the second is an older API id, used only in
# Série A 2003-2008 shards.
BRAZILIAN_IDS = {"figueirense": 137}

KNOCKOUT_LABELS = [
    (re.compile(r"^first stage$", re.I), "1st Round"),
    (re.compile(r"^second stage$", re.I), "2nd Round"),
    (re.compile(r"round of 16", re.I), "8th Finals"),
    (re.compile(r"quarter[- ]?finals?", re.I), "Quarter-finals"),
    (re.compile(r"semi[- ]?finals?", re.I), "Semi-finals"),
    (re.compile(r"^finals?$", re.I), "Finals"),
]
# The API calls the last round "Finals" in the Libertadores, "Final" in the Sudamericana.
FINAL_LABEL = {LIBERTADORES: "Finals", SUDAMERICANA: "Final"}


def _round_label(heading: str, title: str, league: int):
    """The API's round label for a knockout match, from the section heading it
    sits under or, when that says nothing ('Matches', 'First leg'), from the
    page's stage ('2017 Copa Sudamericana first stage' -> 'first stage')."""
    stage = re.sub(r"^\d{4} Copa \w+ ", "", title)
    for text in (heading, stage):
        label = next((lab for rx, lab in KNOCKOUT_LABELS if rx.search(text.strip())), None)
        if label:
            return FINAL_LABEL[league] if label == "Finals" else label
    return None


def norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    text = text.replace("athletico", "atletico")
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    text = re.sub(r"\b(club|de|del|cf|fc|sc|ca|cd|sd|sa|c|s|a|f|futbol|football|futebol|regatas|e)\b", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def fetch_raw(title: str, raw_dir: str) -> str:
    """The page's wikitext from `raw_dir`, fetching it from Wikipedia once."""
    path = os.path.join(raw_dir, title.replace(" ", "_") + ".wiki")
    if not os.path.exists(path):
        import urllib.parse
        import urllib.request

        url = "https://en.wikipedia.org/w/index.php?title=" + urllib.parse.quote(title.replace(" ", "_")) + "&action=raw"
        request = urllib.request.Request(url, headers={"User-Agent": "brasileirao-simulator research"})
        with urllib.request.urlopen(request, timeout=60) as response:
            text = response.read().decode("utf-8")
        if not BOX.search(text):
            raise ValueError(f"{title!r} has no football boxes - wrong title?")
        os.makedirs(raw_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    with open(path, encoding="utf-8") as f:
        return f.read()


# A match is `{{Football box ...}}`, or from about 2021 the same template via
# `{{#invoke:football box|main ...}}`.
BOX = re.compile(r"\{\{(?:Football box|#invoke:football box\|main)", re.I)


def _field(block: str, name: str) -> str:
    m = re.search(r"^\|\s*" + name + r"\s*=(.*?)(?=^\||\Z)", block, re.M | re.S)
    return m.group(1).strip() if m else ""


def _team(text: str) -> dict:
    # Flag templates seen across these pages: flagicon, fbaicon, Fba,
    # Fbaicon and the module form #invoke:flag|fbaicon.
    flag = re.search(r"\{\{(?:flagicon|fbaicon|fba|#invoke:flag\|fbaicon)\|([A-Z]{3})", text, re.I)
    link = re.search(r"\[\[([^|\]]+)(?:\|([^\]]+))?\]\]", text)
    article = link.group(1).strip() if link else re.sub(r"\{\{[^}]*\}\}", "", text).strip()
    shown = (link.group(2) or link.group(1)).strip() if link else article
    return {"article": article, "name": shown, "country": flag.group(1) if flag else None}


def _goals_by_90(text: str) -> int:
    """Goals in a goals list scored by the 90th minute (stoppage time counts)."""
    count = 0
    for template in re.findall(r"\{\{goal\|([^}]*)\}\}", text):
        for token in template.split("|"):
            m = re.fullmatch(r"\s*(\d+)(?:\+\d+)?\s*", token)
            if m and int(m.group(1)) <= 90:
                count += 1
    return count


MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], start=1)}


def _date(text: str):
    """`{{Start date|2017|3|14}}`, or the older plain 'February 17, 2015' /
    '17 February 2015'."""
    m = re.search(r"Start date\|(\d{4})\|(\d{1,2})\|(\d{1,2})", text)
    if m:
        return dt.date(int(m[1]), int(m[2]), int(m[3]))
    m = re.search(r"([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})", text) or re.search(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", text)
    if not m:
        return None
    a, b, year = m.groups()
    month, day = (a, b) if a.isalpha() else (b, a)
    if month.lower() not in MONTHS:
        return None
    return dt.date(int(year), MONTHS[month.lower()], int(day))


def _clock(text: str):
    """(hour, minute, UTC offset in hours) from `{{UTZ|21:00|-3}}` or the older
    `20:45 [[UTC−06:00|UTC−6]]` (a typographic minus), or None."""
    m = re.search(r"UTZ\|(\d{1,2}):(\d{2})\|([+-]?\d+(?:\.\d+)?)", text)
    if m:
        return int(m[1]), int(m[2]), float(m[3])
    m = re.search(r"(\d{1,2}):(\d{2}).*?UTC\s*([−+-])\s*(\d{1,2})(?::(\d{2}))?", text)
    if m:
        sign = -1 if m[3] in "−-" else 1
        return int(m[1]), int(m[2]), sign * (int(m[4]) + (int(m[5]) / 60 if m[5] else 0))
    return None


def parse_page(text: str, title: str) -> list:
    """Every Football box on the page, with the round it sits under."""
    rows = []
    heading = ""
    group = None
    for part in re.split(r"(^={2,4}[^=\n]+={2,4}\s*$|\{\{(?:Football box|#invoke:football box\|main))", text, flags=re.M | re.I):
        if part is None:
            continue
        h = re.match(r"^(={2,4})([^=\n]+)\1\s*$", part.strip())
        if h:
            level, name = len(h.group(1)), h.group(2).strip()
            g = re.match(r"Group\s+([A-H1-8])$", name)
            if g:
                group = g.group(1)
            elif level == 2:
                group = None
                heading = name
            continue
        if not re.match(r"\s*\|", part):   # 2015 writes "{{Football box |id=..."
            continue
        block = part
        day = _date(_field(block, "date"))
        if day is None:
            continue
        clock = _clock(_field(block, "time"))
        kickoff = None
        if clock:
            local = dt.datetime(day.year, day.month, day.day, clock[0], clock[1])
            kickoff = local - dt.timedelta(hours=clock[2])
        score_text = _field(block, "score")
        # The dash is an en dash, a hyphen or, on some pages, a minus sign.
        score = re.search(r"(\d+)\s*[–−-]\s*(\d+)", re.sub(r"<[^>]*>|\{\{[^}]*\}\}", " ", score_text))
        status = "FT"
        if re.search(r"awarded|suspended|abandoned", score_text, re.I):
            status = "AWD"
        elif re.search(r"cancel", score_text, re.I):   # the 2016 Sudamericana final
            status = "CANC"
        aet = bool(re.search(r"^\|\s*aet\s*=\s*yes", block, re.M | re.I)) or "aet" in score_text.lower()
        penalties = bool(re.search(r"^\|\s*penaltyscore\s*=\s*\S", block, re.M))
        full = (int(score[1]), int(score[2])) if score else None
        ninety = full
        if aet and full:
            ninety = (_goals_by_90(_field(block, "goals1")), _goals_by_90(_field(block, "goals2")))
            status = "AET"
        if penalties and status == "FT":
            status = "PEN"
        stadium = _field(block, "stadium")
        links = re.findall(r"\[\[([^|\]]+)(?:\|([^\]]+))?\]\]", stadium)
        rows.append({
            "title": title, "heading": heading, "group": group, "kickoff_utc": kickoff,
            "date": day,
            "team1": _team(_field(block, "team1")), "team2": _team(_field(block, "team2")),
            "full": full, "ninety": ninety, "status": status,
            "venue_name": (links[0][1] or links[0][0]) if links else re.sub(r"\[\[|\]\]", "", stadium),
            "venue_city": (links[-1][1] or links[-1][0]) if len(links) > 1 else "",
        })
    return rows


def label_rounds(rows: list, league: int) -> list:
    """API-style round labels: 'Group Stage - N' by matchday within each group,
    knockout rounds by `_round_label`."""
    by_group = collections.defaultdict(list)
    kept = []
    for r in rows:
        if r["group"]:
            by_group[r["group"]].append(r)
            continue
        label = _round_label(r["heading"], r["title"], league)
        if label:
            r["round"] = label
            kept.append(r)
    for group, matches in by_group.items():
        matches.sort(key=lambda r: (r["kickoff_utc"] or dt.datetime.combine(r["date"], dt.time()), r["team1"]["name"]))
        for i, r in enumerate(matches):
            r["round"] = f"Group Stage - {i // 2 + 1}"
            kept.append(r)
    # A final appears on both the final-stages page and its own page some years.
    seen, unique = set(), []
    for r in sorted(kept, key=lambda r: (r["date"], r["team1"]["article"])):
        key = (r["date"], norm(r["team1"]["article"]), norm(r["team2"]["article"]))
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique


def known_teams(datasets_path: str = DATASETS_PATH) -> tuple:
    """({id: name} for clubs in Brazilian competitions, {id: name} for clubs
    seen only in the continental competitions, {id: name} for Série A clubs)
    from the existing shards."""
    brazilian, continental, serie_a = {}, {}, {}
    for path in glob.glob(f"{datasets_path}/competitions/*/*.csv"):
        league = int(path.split("/")[-2])
        with open(path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                for side in ("home", "away"):
                    tid = r.get(f"teams_{side}_id")
                    if not tid:
                        continue
                    tid, name = int(float(tid)), r[f"teams_{side}_name"]
                    (brazilian if league in (71, 72, 73) else continental)[tid] = name
                    if league == 71:
                        serie_a[tid] = name
    continental = {tid: name for tid, name in continental.items() if tid not in brazilian}
    return brazilian, continental, serie_a


def _similar(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()


# A club is its Wikipedia article *and* its country: some links are shared by
# different clubs ("Nacional" is Uruguay's and Paraguay's, "River Plate"
# Argentina's and Uruguay's), and only the flag tells them apart.
def _key(team: dict) -> tuple:
    return team["article"], team["country"]


# A learned id must carry at least this share of a club's pairings; the rest
# are pairing noise (two matches at one minute with similar names).
LEARN_AGREEMENT = 0.8


def learn_foreign_ids(raw_dir: str, datasets_path: str = DATASETS_PATH) -> tuple:
    """({wikipedia article: API team id}, conflicts, {article: (name, country)},
    skipped pages) learned by pairing each Wikipedia match in LEARN_PAGES with
    the API match kicking off at the same UTC minute (several can share a
    minute; names only choose among them). The kick-off does the identifying,
    so a club spelled differently by the two sources still lands on its id."""
    votes = collections.defaultdict(collections.Counter)
    names, skipped = {}, []
    for (league, season), titles in LEARN_PAGES.items():
        api = collections.defaultdict(list)
        with open(f"{datasets_path}/competitions/{league}/{season}.csv", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                api[dt.datetime.fromisoformat(r["fixture_date"]).replace(tzinfo=None)].append(r)
        for title in titles:
            try:
                rows = parse_page(fetch_raw(title, raw_dir), title)
            except (ValueError, OSError) as error:
                skipped.append(f"{title}: {error}")
                continue
            for w in rows:
                for team in (w["team1"], w["team2"]):
                    names.setdefault(_key(team), (team["name"], team["country"]))
                candidates = api.get(w["kickoff_utc"], []) if w["kickoff_utc"] else []
                if not candidates:
                    continue
                scored = sorted(((_similar(w["team1"]["name"], c["teams_home_name"]) + _similar(w["team2"]["name"], c["teams_away_name"]), i)
                                 for i, c in enumerate(candidates)), reverse=True)
                best, i = scored[0]
                runner_up = scored[1][0] if len(scored) > 1 else 0.0
                if best < 0.8 or best - runner_up < 0.2:
                    continue
                c = candidates[i]
                votes[_key(w["team1"])][int(float(c["teams_home_id"]))] += 1
                votes[_key(w["team2"])][int(float(c["teams_away_id"]))] += 1
    learned, conflicts = {}, {}
    for key, counter in votes.items():
        (tid, n), *rest = counter.most_common()
        if n >= LEARN_AGREEMENT * sum(counter.values()):
            learned[key] = tid
        if rest:
            conflicts[" / ".join(str(k) for k in key)] = dict(counter)
    return learned, conflicts, names, skipped


def map_teams(rows: list, brazilian: dict, serie_a: dict, learned: dict, learned_names: dict) -> dict:
    """{wikipedia article: (team_id, name, method)}.

    Brazilian clubs: by name (BRAZILIAN_NAMES, then the exact spelling, then
    Série A clubs only) - they must hit the id the rest of the data uses.
    Foreign clubs: the id learned from kick-off pairing; failing that, a
    learned club with the same shown name *and* country (Wikipedia renames
    articles between seasons); failing that, a synthetic id - such a club
    never appears in the API data, so a synthetic id is the right answer."""
    teams = {}
    for r in rows:
        for t in (r["team1"], r["team2"]):
            teams[_key(t)] = t
    by_name_country = {}
    for key, tid in learned.items():
        name, country = learned_names[key]
        by_name_country.setdefault((norm(name), country), set()).add(tid)
    mapping, synthetic = {}, SYNTHETIC_TEAM_BASE
    for key in sorted(teams, key=lambda k: (k[0], k[1] or "")):
        t = teams[key]
        if t["country"] == "BRA":
            wanted = BRAZILIAN_NAMES.get(norm(t["name"]), t["name"])
            pinned = BRAZILIAN_IDS.get(norm(t["name"]))
            for pool in ({pinned: brazilian[pinned]} if pinned else {},
                         {i: n for i, n in brazilian.items() if n == wanted},
                         {i: n for i, n in serie_a.items() if norm(n) == norm(wanted)}):
                if len(pool) == 1:
                    tid = next(iter(pool))
                    mapping[key] = (tid, brazilian[tid], "Brazilian name")
                    break
            if key not in mapping:
                raise ValueError(f"Brazilian club {key!r} ({t['name']}) has no unambiguous id")
            continue
        if key in learned:
            mapping[key] = (learned[key], t["name"], "kick-off pairing")
            continue
        same = by_name_country.get((norm(t["name"]), t["country"]), set())
        if len(same) == 1:
            mapping[key] = (next(iter(same)), t["name"], "same name and country as a paired club")
            continue
        synthetic += 1
        mapping[key] = (synthetic, t["name"], "synthetic (not in any API season paired)")
    return mapping


def to_csv_rows(rows: list, mapping: dict, league: int, season: int) -> list:
    out = []
    for n, r in enumerate(sorted(rows, key=lambda r: (r["kickoff_utc"] or dt.datetime.combine(r["date"], dt.time()))), start=1):
        h, a = mapping[_key(r["team1"])], mapping[_key(r["team2"])]
        kick = r["kickoff_utc"] or dt.datetime.combine(r["date"], dt.time(22, 0))
        out.append({
            "fixture_id": SYNTHETIC_FIXTURE_BASE[league] + season * 10_000 + n,
            "fixture_date": kick.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
            "fixture_venue_id": "", "fixture_venue_name": r["venue_name"], "fixture_venue_city": r["venue_city"],
            "fixture_status_short": r["status"], "league_id": league, "league_season": season,
            "league_round": r["round"],
            "teams_home_id": h[0], "teams_home_name": h[1], "teams_away_id": a[0], "teams_away_name": a[1],
            "goals_home": r["full"][0] if r["full"] else "", "goals_away": r["full"][1] if r["full"] else "",
            "score_fulltime_home": r["ninety"][0] if r["ninety"] else "",
            "score_fulltime_away": r["ninety"][1] if r["ninety"] else "",
            "kickoff_time_known": r["kickoff_utc"] is not None, "source": r["title"],
        })
    return out


def season_rows(league: int, season: int, raw_dir: str) -> list:
    rows = []
    for title in PAGES[(league, season)]:
        rows += parse_page(fetch_raw(title, raw_dir), title)
    return label_rounds(rows, league)


def validate(rows: list, mapping: dict, league: int, season: int, datasets_path: str = DATASETS_PATH) -> dict:
    """Two held-out checks on a season both sources cover.

    Parser: every Wikipedia match is paired with the API match at the same
    UTC minute (names only choose among simultaneous matches); every match
    must find a partner at its minute, and the 90-minute score and the round
    label must agree.

    Ids: for each paired match, the id this build gives each club (Brazilian
    names, or ids learned from other seasons only) must equal the id the API
    uses that season. Clubs in no other paired season get synthetic ids and are
    counted separately - their matches still carry the right date and time.
    The Libertadores' qualifying rounds are not built, so they are not expected.
    """
    api = collections.defaultdict(list)
    with open(f"{datasets_path}/competitions/{league}/{season}.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if not (league == LIBERTADORES and r["league_round"].startswith(("1st", "2nd", "3rd"))):
                api[dt.datetime.fromisoformat(r["fixture_date"]).replace(tzinfo=None)].append(r)
    result = collections.Counter()
    problems = []
    for w in rows:
        candidates = api.get(w["kickoff_utc"], [])
        if not candidates:
            result["no API match at that minute"] += 1
            problems.append(("no match at minute", w["kickoff_utc"], w["team1"]["name"], w["team2"]["name"]))
            continue
        c = max(candidates, key=lambda c: _similar(w["team1"]["name"], c["teams_home_name"]) + _similar(w["team2"]["name"], c["teams_away_name"]))
        result["paired at the same minute"] += 1
        api_score = (int(float(c["score_fulltime_home"])), int(float(c["score_fulltime_away"])))
        result["90-minute score agrees"] += api_score == w["ninety"]
        result["round label agrees"] += c["league_round"] == w["round"]
        if api_score != w["ninety"] or c["league_round"] != w["round"]:
            problems.append(("disagree", w["kickoff_utc"], w["team1"]["name"], w["ninety"], w["round"], "api", api_score, c["league_round"]))
        for side, team in (("home", w["team1"]), ("away", w["team2"])):
            tid, _, method = mapping[_key(team)]
            api_id = int(float(c[f"teams_{side}_id"]))
            if method.startswith("synthetic"):
                result["club sides with a synthetic id"] += 1
            elif tid == api_id:
                result["club sides whose id matches the API"] += 1
            else:
                result["club sides with a WRONG id"] += 1
                problems.append(("wrong id", _key(team), tid, method, "api", api_id, c[f"teams_{side}_name"]))
    return {"parsed": len(rows), **result, "problems": problems[:20]}


def internal_checks(rows: list) -> dict:
    groups = collections.defaultdict(list)
    for r in rows:
        if r["round"].startswith("Group"):
            groups[r["round"]].append(r)
    group_matches = [r for r in rows if r["round"].startswith("Group")]
    per_team = collections.Counter()
    for r in group_matches:
        per_team[r["team1"]["article"]] += 1
        per_team[r["team2"]["article"]] += 1
    rounds = collections.Counter(r["round"] for r in rows)
    # Before 2019 every knockout tie, the final included, was two legs.
    legs = collections.Counter((r["round"], frozenset((_key(r["team1"]), _key(r["team2"])))) for r in rows
                               if not r["round"].startswith("Group"))
    return {
        "group matches": len(group_matches),
        "teams in groups": len(per_team),
        "teams not playing six group matches": {k: v for k, v in per_team.items() if v != 6},
        "knockout ties not two legs": [f"{rnd}: {' v '.join(sorted(k[0] for k in pair))} ({n})"
                                       for (rnd, pair), n in legs.items() if n != 2],
        "rounds": dict(sorted(rounds.items())),
        "without a kick-off time": sum(1 for r in rows if r["kickoff_utc"] is None),
        "played but without a score": sum(1 for r in rows if r["full"] is None and r["status"] not in ("AWD", "CANC")),
        "status": dict(collections.Counter(r["status"] for r in rows)),
    }


def serie_a_clashes(csv_rows: list, season: int, datasets_path: str = DATASETS_PATH, hours: int = 48) -> list:
    """Continental kick-offs within `hours` of the same club's Série A kick-off."""
    kicks = collections.defaultdict(list)
    with open(f"{datasets_path}/competitions/71/{season}.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            when = dt.datetime.fromisoformat(r["fixture_date"]).replace(tzinfo=None)
            for side in ("home", "away"):
                kicks[int(float(r[f"teams_{side}_id"]))].append(when)
    clashes = []
    for r in csv_rows:
        when = dt.datetime.fromisoformat(r["fixture_date"]).replace(tzinfo=None)
        for side in ("home", "away"):
            for other in kicks.get(int(r[f"teams_{side}_id"]), []):
                if abs((other - when).total_seconds()) < hours * 3600:
                    clashes.append((r["fixture_date"], r["teams_home_name"], r["teams_away_name"], r[f"teams_{side}_name"], str(other)))
    return clashes


def main(check: bool = True, out_dir: str = OUT_DIR) -> dict:
    raw_dir = os.path.join(out_dir, "raw")
    brazilian, _, serie_a = known_teams()
    report = {"seasons": {}, "validation": {}}
    all_rows = {ls: season_rows(*ls, raw_dir) for ls in BUILD + VALIDATION}
    learned, conflicts, learned_names, skipped = learn_foreign_ids(raw_dir)
    mapping = map_teams([r for rows in all_rows.values() for r in rows], brazilian, serie_a, learned, learned_names)
    report["learned_foreign_ids"] = len(learned)
    report["learning_conflicts"] = conflicts
    report["learning_pages_skipped"] = skipped

    for league, season in VALIDATION:
        report["validation"][f"{league}/{season}"] = validate(all_rows[(league, season)], mapping, league, season)
    for league, season in BUILD:
        rows = all_rows[(league, season)]
        csv_rows = to_csv_rows(rows, mapping, league, season)
        os.makedirs(os.path.join(out_dir, str(league)), exist_ok=True)
        with open(os.path.join(out_dir, str(league), f"{season}.csv"), "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(csv_rows[0].keys()))
            writer.writeheader()
            writer.writerows(csv_rows)
        report["seasons"][f"{league}/{season}"] = {**internal_checks(rows), "serie_a_clashes_48h": serie_a_clashes(csv_rows, season)}
    with open(os.path.join(out_dir, "team_ids.csv"), "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["wikipedia_article", "country", "team_id", "name", "method"])
        for (article, country), (tid, name, method) in sorted(mapping.items(), key=lambda kv: (kv[0][0], kv[0][1] or "")):
            writer.writerow([article, country, tid, name, method])
    with open(os.path.join(out_dir, "build_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1, default=str, ensure_ascii=False)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="print the validation and internal checks")
    args = parser.parse_args()
    report = main()
    print(f"foreign ids learned by kick-off pairing: {report['learned_foreign_ids']}; conflicts: {report['learning_conflicts']}; "
          f"pages skipped: {report['learning_pages_skipped']}")
    for name, v in report["validation"].items():
        print(f"{name} held-out validation:", {k: val for k, val in v.items() if k != "problems"})
        for p in v["problems"]:
            print("   ", p)
    for name, s in report["seasons"].items():
        print(f"{name}: group matches {s['group matches']}, teams {s['teams in groups']}, off-six {s['teams not playing six group matches']}, "
              f"rounds {s['rounds']}, no time {s["without a kick-off time"]}, no score {s["played but without a score"]}, status {s['status']}, Série A clashes {len(s['serie_a_clashes_48h'])}")
        for t in s["knockout ties not two legs"]:
            print("    not two legs:", t)
        for c in s["serie_a_clashes_48h"][:5]:
            print("    clash:", c)
