"""Draw the 1200x630 card that WhatsApp, X and Slack show when the link is pasted.

    python3 build_og_card.py --out site/og.png

The card carries the current numbers rather than a logo, because the link is
usually pasted to make a point - "Flamengo 64%" is the point, and a reader
decides whether to click on the strength of it.

SVG, then rasterised. No fonts are embedded: the card is rendered here and
shipped as a PNG, so whatever the viewer has installed never matters.
"""

import argparse
import csv
import json
import subprocess
from pathlib import Path

REPORT_DIR = Path(__file__).resolve().parent
EXPORTS = REPORT_DIR.parents[2] / "files" / "exports"
DATASETS = REPORT_DIR.parents[2] / "files" / "datasets"

W, H = 1200, 630
INK = "#111f18"
PAPER = "#f7faf7"
CAMPO = "#1b7a46"
Z4 = "#c0271c"
MUTED = "#5b6a61"


def display_names() -> dict:
    """`{canonical name: display name}`, the same join build_report does.

    Duplicated rather than imported: build_report pulls in the whole package,
    and this script is meant to run on the host with nothing but the standard
    library, beside the deploy.
    """
    with open(REPORT_DIR / "display_names.json", encoding="utf-8") as f:
        by_id = {k: v for k, v in json.load(f).items() if k.isdigit()}
    by_canonical = {}
    for path in sorted(DATASETS.glob("20*/fixtures.csv")):
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                for id_col, name_col in (("teams_home_id", "teams_home_name"),
                                         ("teams_away_id", "teams_away_name")):
                    name = by_id.get(row[id_col])
                    if name:
                        by_canonical[row[name_col]] = name
    return by_canonical


def latest(dataset: Path) -> dict:
    """Title and relegation leaders at the newest forecast date.

    Names go through the same display map the page uses, so the card says
    Chapecoense rather than the API's Chapecoense-sc. A card is the most
    public thing here - it should not be the one place the raw name leaks.
    """
    display = display_names()
    with open(dataset) as f:
        data = json.load(f)
    season_key = max(data["seasons"])
    season = data["seasons"][season_key]
    i = len(season["dates"]) - 1
    rows = [
        {"club": display.get(club, club), "title": t["title"][i], "releg": t["releg"][i]}
        for club, t in season["teams"].items()
    ]
    return {
        "season": season_key,
        "date": season["dates"][i],
        "rounds": season.get("rounds") or {},
        "title": sorted(rows, key=lambda r: -r["title"])[:2],
        "releg": sorted(rows, key=lambda r: -r["releg"])[:2],
    }


def esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def pct(value: float) -> str:
    if value >= 99.5:
        return ">99%"
    if value < 0.5:
        return "<1%"
    return f"{round(value)}%"


def br_date(iso: str) -> str:
    months = ["jan", "fev", "mar", "abr", "mai", "jun",
              "jul", "ago", "set", "out", "nov", "dez"]
    y, m, d = iso.split("-")
    return f"{int(d)} {months[int(m) - 1]} {y}"


def svg(state: dict) -> str:
    rnd = state["rounds"].get("current")
    stamp = (f"{rnd}ª rodada · {br_date(state['date'])}" if rnd else br_date(state["date"]))

    def row(entry, y, colour, label):
        return f"""
  <text x="80" y="{y}" font-family="Helvetica,Arial,sans-serif" font-size="26"
        fill="{MUTED}" letter-spacing="2">{label}</text>
  <text x="80" y="{y + 62}" font-family="Helvetica,Arial,sans-serif" font-size="54"
        font-weight="700" fill="{INK}">{esc(entry['club'])}</text>
  <text x="1120" y="{y + 62}" font-family="Helvetica,Arial,sans-serif" font-size="60"
        font-weight="700" fill="{colour}" text-anchor="end">{entry['pct']}</text>"""

    champion = dict(state["title"][0], pct=pct(state["title"][0]["title"]))
    runner = dict(state["title"][1], pct=pct(state["title"][1]["title"]))
    doomed = dict(state["releg"][0], pct=pct(state["releg"][0]["releg"]))

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">
  <rect width="{W}" height="{H}" fill="{PAPER}"/>
  <rect x="0" y="0" width="{W}" height="14" fill="{CAMPO}"/>
  <text x="80" y="110" font-family="Helvetica,Arial,sans-serif" font-size="34"
        font-weight="700" fill="{CAMPO}" letter-spacing="3">DATA BRASILEIRÃO</text>
  <text x="1120" y="110" font-family="Helvetica,Arial,sans-serif" font-size="26"
        fill="{MUTED}" text-anchor="end">{esc(stamp)}</text>
  <line x1="80" y1="140" x2="1120" y2="140" stroke="#d7dfd8" stroke-width="2"/>
{row(champion, 200, CAMPO, "FAVORITO AO TÍTULO")}
{row(runner, 330, MUTED, "SEGUNDO")}
{row(doomed, 460, Z4, "MAIS PERTO DO Z-4")}
  <text x="80" y="590" font-family="Helvetica,Arial,sans-serif" font-size="24"
        fill="{MUTED}">databrasileirao.com.br · 20.000 temporadas simuladas por rodada</text>
</svg>"""


def rasterise(source: Path, out: Path) -> None:
    """SVG to PNG with whatever this machine has. rsvg-convert and Inkscape are
    the good options; macOS ships `qlmanage`, which is the fallback."""
    for cmd in (["rsvg-convert", "-w", str(W), "-h", str(H), "-o", str(out), str(source)],
                ["inkscape", str(source), "--export-filename", str(out),
                 "-w", str(W), "-h", str(H)],
                ["convert", "-density", "144", str(source), "-resize", f"{W}x{H}", str(out)]):
        try:
            subprocess.run(cmd, check=True, capture_output=True)
            return
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue
    raise SystemExit(
        "no SVG rasteriser found. Install one:\n"
        "  brew install librsvg      # rsvg-convert, smallest\n"
        "  brew install imagemagick  # convert\n"
        f"The SVG is written at {source} either way."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="site/og.png")
    parser.add_argument("--dataset", default=str(EXPORTS / "forecast_dataset.elo.json"))
    args = parser.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    source = out.with_suffix(".svg")
    state = latest(Path(args.dataset))
    source.write_text(svg(state), encoding="utf-8")
    rasterise(source, out)
    source.unlink()
    print(f"wrote {out} ({out.stat().st_size / 1024:.0f} KB) - "
          f"{state['title'][0]['club']} {pct(state['title'][0]['title'])}, "
          f"{state['releg'][0]['club']} {pct(state['releg'][0]['releg'])}")
