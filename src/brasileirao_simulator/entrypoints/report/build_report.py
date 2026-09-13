"""Splice the forecast dataset into the report template.

Run either from the repo, no Docker needed - the script is stdlib plus json:

    PYTHONPATH=src python3 -m brasileirao_simulator.entrypoints.report.build_report --lang en

or the way every other entrypoint runs, inside the container:

    docker-compose run --rm app python3 brasileirao_simulator/entrypoints/report/build_report.py --lang pt

Every input path is resolved from this file's own location rather than the
working directory, so both invocations read the same files regardless of
where they are launched from. `--lang` (default `en`) selects which
`strings.{lang}.json` supplies the page's user-facing text and, absent
`--out`, names the output `forecasts.{lang}.html`.

Every prose string in template.html was pulled out into strings.en.json,
keyed by id, and referenced back as a `{{key}}` token. A token with no `#i`
substitutes its string verbatim (a JSON array, e.g. the month-abbreviation
table, renders as a compact JS array literal). A `{{key#i}}` token substitutes
the i-th static segment of a value that carries runtime placeholders - the
value is split on its `{name}` / `{}` markers, and segment i is dropped in at
that exact spot in the template, while the surrounding JS (unchanged from the
original hand-written concatenation) supplies the runtime expression between
segments. This is what lets one strings.json entry hold a whole, coherently
phrased sentence - e.g. "In <strong>{season}</strong>, judged from the
standings on {date} ... went down in <strong>{probability}%</strong> ..." -
that a translator can reorder freely, while the `en` build still reproduces
the original page's bytes exactly: see render_strings() below.
"""

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Optional

from brasileirao_simulator.config.explorer_models import DEFAULT_EXPLORER_MODEL, EXPLORER_MODELS
from brasileirao_simulator.config.settings import EXPORTS_PATH

REPORT_DIR = Path(__file__).resolve().parent
SRC_DIR = REPORT_DIR.parents[2]
EXPORTS_DIR = SRC_DIR / EXPORTS_PATH

DEFAULT_LANG = "en"
# What a published page carries. The older model stays in `EXPLORER_MODELS`
# (and in the backtest, where it is the reference every test is gated on), but
# it is no longer simulated for the running season, so shipping it beside Elo
# would put a frozen archive next to a live one. A caller can still ask for it
# explicitly with `models=[...]`.
PUBLISHED_MODELS = ["elo"]
# A published page carries the running season only: the archive's eleven
# seasons are 2.2 MB of finished history that never changes, and the page is
# read for what is happening now. The whole archive is still exported and
# still available - `--seasons all` bundles it - and the page keeps a summary
# of it (`archive`) so the header can say how much history stands behind the
# numbers. Loading past seasons on demand is the next step; see the board.
CURRENT_SEASON_ONLY = "current"
# Page designs, by version: v1 is the original explorer, v2 the newsroom
# redesign (a forecast table first, then the same charts restyled), v3 the
# same structure in the Brazilian-modernist identity - concrete and ink,
# probabilities drawn as bars. All read the same payload and strings tables.
TEMPLATES = {"v1": "template.html", "v2": "template_v2.html", "v3": "template_v3.html",
             "v4": "template_v4.html", "v5": "template_v5.html", "v6": "template_v6.html"}

_TOKEN_RE = re.compile(r"\{\{([\w.]+)(?:#(\d+))?\}\}")
_SEGMENT_RE = re.compile(r"\{[a-zA-Z_]*\}")


def load_data(
    exports_dir: Path = EXPORTS_DIR,
    report_dir: Path = REPORT_DIR,
    benchmark_path: Optional[Path] = None,
    display_names: Optional[dict] = None,
    models: Optional[list] = None,
    seasons: Optional[list] = None,
) -> dict:
    """Assemble the page's data payload: one entry per explorer model that has
    an exported dataset, plus what every model shares (crests, display names,
    the historical relegation cut-offs).

    `models` restricts which explorer models are bundled (default: every model
    in `EXPLORER_MODELS` whose dataset file exists), so a test can pin its
    inputs while new model datasets appear in the live exports.

    `benchmark_path` overrides where the incumbent's `benchmark.json` is read
    from, independent of `exports_dir` - the equivalence test pins it to a
    frozen snapshot so the reproducibility check stays deterministic while
    the live export keeps growing new seasons underneath it.
    """
    keys = models if models is not None else list(PUBLISHED_MODELS)
    bundled = {}
    for key in keys:
        model = EXPLORER_MODELS[key]
        dataset_path = exports_dir / model.dataset_file
        if not dataset_path.exists():
            continue
        with open(dataset_path) as f:
            dataset = json.load(f)
        bench_path = benchmark_path if (benchmark_path and key == "incumbent") else exports_dir / model.benchmark_file
        with open(bench_path) as f:
            benchmark = json.load(f)
        every_season = dataset["seasons"]
        kept = every_season if seasons is None else {s: every_season[s] for s in seasons if s in every_season}
        archive = {
            "seasons": len(every_season),
            "first": min(every_season), "last": max(every_season),
            "dates": sum(len(v["dates"]) for v in every_season.values()),
            "bundled": sorted(kept),
        }
        bundled[key] = {
            "archive": archive,
            "seasons": kept,
            "calibration": dataset["calibration"],
            "benchmark": benchmark,
            # An overridden (frozen) benchmark has no matching pooled figure, so
            # none is shown rather than one that describes different seasons.
            "benchmark_pooled": None if (benchmark_path and key == "incumbent")
            else _benchmark_pooled(exports_dir / model.benchmark_pooled_file),
            "meta": {"skill": model_skill(key, exports_dir)},
            "historical_cutoffs": dataset["historical_cutoffs"],
        }
    if not bundled:
        raise FileNotFoundError(f"no explorer model has a dataset in {exports_dir}")

    order = list(bundled)
    data = {
        "models": bundled,
        "model_order": order,
        "default_model": DEFAULT_EXPLORER_MODEL if DEFAULT_EXPLORER_MODEL in bundled else order[0],
        # Built from the fixtures, not the simulations: identical across models.
        "historical_cutoffs": bundled[order[0]].pop("historical_cutoffs"),
    }
    for model in bundled.values():
        model.pop("historical_cutoffs", None)
    data["archive"] = bundled[data["default_model"]]["archive"]

    with open(report_dir / "logos.json") as f:
        data["logos"] = json.load(f)

    # {canonical name -> display name}. The page's data is keyed by the API's
    # canonical name ("Vasco DA Gama"); the display map is keyed by team id, so
    # the two are joined here, once, at build time. An empty map is the off
    # state and reproduces the pre-display-name page byte for byte.
    data["display_names"] = (
        display_names_by_canonical(report_dir, exports_dir.parent / "datasets")
        if display_names is None
        else display_names
    )

    return data


def model_skill(key: str, exports_dir: Path = EXPORTS_DIR) -> Optional[float]:
    """Match-outcome Brier skill over the base-rate reference, in percent,
    pooled over 2016-2025 - the page's skill tile. The incumbent's comes from
    the recency sweep's default-constants row, as it always has; Elo's from the
    ten-season Elo backtest, scored the same way on the same matches."""
    if key == "incumbent":
        with open(exports_dir / "recency_weights_sweep_pooled.csv") as f:
            for row in csv.DictReader(f):
                if row["weights"].replace(" ", "") == "(4,3,1)":
                    return 100 * float(row["skill"])
        return None
    path = exports_dir / "elo_ten_seasons_pooled.json"
    if key == "elo" and path.exists():
        with open(path) as f:
            skill = json.load(f).get("brier_skill", {}).get("elo")
        return None if skill is None else 100 * skill
    return None


def _benchmark_pooled(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def display_names_by_canonical(report_dir: Path, datasets_dir: Path) -> dict:
    """Join display_names.json (keyed by team id) to the datasets' id -> name
    pairs, giving the map the template can consult by the name it already has.

    Rendering only: nothing that joins, sorts or exports is touched. A club
    without an entry simply keeps its canonical name."""
    with open(report_dir / "display_names.json", encoding="utf-8") as f:
        by_id = {k: v for k, v in json.load(f).items() if k.isdigit()}

    by_canonical = {}
    for path in sorted(datasets_dir.glob("20*/fixtures.csv")):
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                for id_col, name_col in (("teams_home_id", "teams_home_name"), ("teams_away_id", "teams_away_name")):
                    display = by_id.get(row[id_col])
                    if display:
                        by_canonical[row[name_col]] = display
    return by_canonical


def load_strings(lang: str, report_dir: Path = REPORT_DIR) -> dict:
    with open(report_dir / f"strings.{lang}.json", encoding="utf-8") as f:
        return json.load(f)


def render_strings(template: str, strings: dict) -> str:
    """Replace every `{{key}}` / `{{key#i}}` token in `template` with text
    drawn from `strings`. See the module docstring for what `#i` means."""

    def repl(match: "re.Match[str]") -> str:
        key, index = match.group(1), match.group(2)
        if key not in strings:
            raise KeyError(f"template references undefined string key {key!r}")
        value = strings[key]
        if index is None:
            if isinstance(value, list):
                return json.dumps(value, separators=(",", ":"))
            return value
        segments = _SEGMENT_RE.split(value)
        return segments[int(index)]

    return _TOKEN_RE.sub(repl, template)


_GA4_ID_RE = re.compile(r"^G-[A-Z0-9]{4,20}$")


def _with_ga4(template: str, measurement_id: Optional[str]) -> str:
    """Insert the GA4 tag, or return the template untouched.

    Off unless a measurement id is passed, and deliberately so: the tag is a
    third-party script, which the artifact host's CSP blocks outright, and a
    page built for a test or for a local read has no business phoning home.
    Only the build that deploys to the public site asks for it.
    """
    if not measurement_id:
        return template
    if not _GA4_ID_RE.match(measurement_id):
        raise ValueError(f"not a GA4 measurement id: {measurement_id!r} (expected G-XXXXXXX)")
    tag = (
        f'<script async src="https://www.googletagmanager.com/gtag/js?id={measurement_id}"></script>\n'
        "<script>\n"
        "window.dataLayer = window.dataLayer || [];\n"
        "function gtag(){dataLayer.push(arguments);}\n"
        'gtag("js", new Date());\n'
        f'gtag("config", "{measurement_id}");\n'
        "</script>\n"
    )
    # Before the first tag in the file, so it loads whatever else the page does.
    return tag + template


def build(
    out_path: Path,
    lang: str = DEFAULT_LANG,
    exports_dir: Path = EXPORTS_DIR,
    report_dir: Path = REPORT_DIR,
    benchmark_path: Optional[Path] = None,
    display_names: Optional[dict] = None,
    models: Optional[list] = None,
    version: str = "v1",
    seasons: Optional[list] = None,
    ga4: Optional[str] = None,
) -> Path:
    data = load_data(exports_dir, report_dir, benchmark_path, display_names, models, seasons)
    strings = load_strings(lang, report_dir)

    with open(report_dir / TEMPLATES[version]) as f:
        template = f.read()

    template = render_strings(template, strings)
    template = _with_ga4(template, ga4)

    payload = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    html = template.replace("__DATA__", payload)

    # Make the file pure ASCII so it renders correctly whatever charset the host
    # declares. Markup takes HTML entities; JavaScript string literals take \u
    # escapes instead, because several of them reach the page through textContent,
    # which renders an entity as literal text rather than the character.
    head, marker, tail = html.partition('<script id="data"')
    head = head.encode("ascii", "xmlcharrefreplace").decode("ascii")
    # json.dumps already emitted the payload as pure ASCII, so everything non-ASCII
    # left in the tail sits in a JavaScript string literal.
    tail = re.sub(r"[^\x00-\x7f]", lambda m: "\\u%04x" % ord(m.group()), tail)
    html = head + marker + tail
    assert html.isascii(), "non-ASCII survived escaping"

    out_path = Path(out_path)
    with open(out_path, "w") as f:
        f.write(html)

    print(f"lang: {lang}, design: {version}, seasons bundled: {data['archive']['bundled']}"
          f" of {data['archive']['seasons']}")
    print(f"models: {data['model_order']} (opens on {data['default_model']})")
    for key, model in data["models"].items():
        print(f"  {key}: {len(model['seasons'])} seasons, {len(model['calibration'])} calibration bins, "
              f"benchmark {[b['season'] for b in model['benchmark']]}, skill {model['meta']['skill']}")
    print(f"crests: {len(data['logos'])}")
    print(f"wrote {out_path} ({out_path.stat().st_size / 1e6:.2f} MB)")
    return out_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lang", default=DEFAULT_LANG)
    parser.add_argument("--version", default="v1", choices=sorted(TEMPLATES), help="page design (default v1)")
    parser.add_argument("--seasons", default="all",
                        help="'all' (default), 'current', or a comma-separated list of seasons to bundle")
    parser.add_argument("--out", default=None,
                        help="default: forecasts.{lang}.html (v1) or forecasts.{version}.{lang}.html next to this script")
    parser.add_argument("--ga4", default=None, metavar="G-XXXXXXX",
                        help="GA4 measurement id; omitted by default, and omitted for artifact builds "
                             "(the artifact host blocks third-party scripts)")
    args = parser.parse_args()

    name = f"forecasts.{args.lang}.html" if args.version == "v1" else f"forecasts.{args.version}.{args.lang}.html"
    out = Path(args.out) if args.out else REPORT_DIR / name
    if args.seasons == "all":
        seasons = None
    elif args.seasons == CURRENT_SEASON_ONLY:
        with open(EXPORTS_DIR / EXPLORER_MODELS[DEFAULT_EXPLORER_MODEL].dataset_file) as f:
            seasons = [max(json.load(f)["seasons"])]
    else:
        seasons = args.seasons.split(",")
    build(out, lang=args.lang, version=args.version, seasons=seasons, ga4=args.ga4)
