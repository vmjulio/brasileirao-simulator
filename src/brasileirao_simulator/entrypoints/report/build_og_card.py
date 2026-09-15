"""Draw the 1200x630 card that WhatsApp, X and Slack show when the link is pasted.

    python3 build_og_card.py --out site/og.png

The card carries the current numbers rather than a logo, because the link is
usually pasted to make a point - "Flamengo 64%" is the point, and a reader
decides whether to click on the strength of it.

SVG, then rasterised. No fonts are embedded: the card is rendered here and
shipped as a PNG, so whatever the viewer has installed never matters.
"""

import argparse
import json
from pathlib import Path

from brasileirao_simulator.entrypoints.report.og_card import (  # noqa: F401 - re-used names
    CAMPO, DATASETS, H, INK, MUTED, PAPER, REPORT_DIR, W, Z4, br_date, display_names, esc, pct, rasterise,
)

EXPORTS = REPORT_DIR.parents[2] / "files" / "exports"


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
        fill="{MUTED}">databrasileirao.com.br</text>
</svg>"""


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
