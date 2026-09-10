"""Splice the forecast dataset into the report template.

Run either from the repo, no Docker needed - the script is stdlib plus json:

    PYTHONPATH=src python3 -m brasileirao_simulator.entrypoints.report.build_report --out <path>

or the way every other entrypoint runs, inside the container:

    docker-compose run --rm app python3 brasileirao_simulator/entrypoints/report/build_report.py --out <path>

Every input path is resolved from this file's own location rather than the
working directory, so both invocations read the same files regardless of
where they are launched from.
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

DEFAULT_OUT = REPORT_DIR / "forecasts.html"


def load_data(
    exports_dir: Path = EXPORTS_DIR,
    report_dir: Path = REPORT_DIR,
    benchmark_path: Optional[Path] = None,
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

    return data


def build(
    out_path: Path,
    exports_dir: Path = EXPORTS_DIR,
    report_dir: Path = REPORT_DIR,
    benchmark_path: Optional[Path] = None,
) -> Path:
    data = load_data(exports_dir, report_dir, benchmark_path)
    with open(report_dir / "template.html") as f:
        template = f.read()

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

    print(f"seasons: {len(data['seasons'])}")
    print(f"calibration bins: {len(data['calibration'])}")
    print(f"crests: {len(data['logos'])}")
    print(f"benchmark seasons: {[b['season'] for b in data['benchmark']]}")
    print(f"skill: {data.get('meta', {}).get('skill')}")
    print(f"wrote {out_path} ({out_path.stat().st_size / 1e6:.2f} MB)")
    return out_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args()

    build(Path(args.out))
