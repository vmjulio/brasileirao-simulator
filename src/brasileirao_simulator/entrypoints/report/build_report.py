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

from brasileirao_simulator.config.settings import EXPORTS_PATH

REPORT_DIR = Path(__file__).resolve().parent
SRC_DIR = REPORT_DIR.parents[2]
EXPORTS_DIR = SRC_DIR / EXPORTS_PATH

DEFAULT_LANG = "en"

_TOKEN_RE = re.compile(r"\{\{([\w.]+)(?:#(\d+))?\}\}")
_SEGMENT_RE = re.compile(r"\{[a-zA-Z_]*\}")


def load_data(
    exports_dir: Path = EXPORTS_DIR,
    report_dir: Path = REPORT_DIR,
    benchmark_path: Optional[Path] = None,
    display_names: Optional[dict] = None,
) -> dict:
    """Assemble the page's data payload.

    `benchmark_path` overrides where `benchmark.json` is read from, independent
    of `exports_dir` - the equivalence test pins it to a frozen snapshot so the
    reproducibility check stays deterministic while the live export keeps
    growing new seasons underneath it.
    """
    with open(exports_dir / "forecast_dataset.json") as f:
        data = json.load(f)

    # Pooled match-Brier skill at the default constants, from the recency sweep's
    # baseline row - the same number every sweep reported for (4,3,1).
    with open(exports_dir / "recency_weights_sweep_pooled.csv") as f:
        for row in csv.DictReader(f):
            if row["weights"].replace(" ", "") == "(4,3,1)":
                data["meta"] = {"skill": 100 * float(row["skill"])}
                break

    with open(report_dir / "logos.json") as f:
        data["logos"] = json.load(f)

    with open(benchmark_path or exports_dir / "benchmark.json") as f:
        data["benchmark"] = json.load(f)

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


def build(
    out_path: Path,
    lang: str = DEFAULT_LANG,
    exports_dir: Path = EXPORTS_DIR,
    report_dir: Path = REPORT_DIR,
    benchmark_path: Optional[Path] = None,
    display_names: Optional[dict] = None,
) -> Path:
    data = load_data(exports_dir, report_dir, benchmark_path, display_names)
    strings = load_strings(lang, report_dir)

    with open(report_dir / "template.html") as f:
        template = f.read()

    template = render_strings(template, strings)

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

    print(f"lang: {lang}")
    print(f"seasons: {len(data['seasons'])}")
    print(f"calibration bins: {len(data['calibration'])}")
    print(f"crests: {len(data['logos'])}")
    print(f"benchmark seasons: {[b['season'] for b in data['benchmark']]}")
    print(f"skill: {data.get('meta', {}).get('skill')}")
    print(f"wrote {out_path} ({out_path.stat().st_size / 1e6:.2f} MB)")
    return out_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lang", default=DEFAULT_LANG)
    parser.add_argument("--out", default=None, help="default: forecasts.{lang}.html next to this script")
    args = parser.parse_args()

    out = Path(args.out) if args.out else REPORT_DIR / f"forecasts.{args.lang}.html"
    build(out, lang=args.lang)
