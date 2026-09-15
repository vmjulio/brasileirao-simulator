"""What every link-preview card shares: size, palette, the site's percentage
and date formats, club display names, and SVG-to-PNG rasterising.

`build_og_card.py` draws the forecast card; `build_analyses.py` draws one card
per analysis piece. Standard library only, host-runnable.
"""

import csv
import json
import subprocess
from pathlib import Path

REPORT_DIR = Path(__file__).resolve().parent
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


def _title_lines(title: str, width: int = 30, most: int = 3) -> list[str]:
    """Greedy word wrap; a title longer than `most` lines ends in an ellipsis."""
    lines, line = [], ""
    for word in title.split():
        if line and len(line) + 1 + len(word) > width:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    lines.append(line)
    if len(lines) > most:
        lines = lines[:most]
        lines[-1] = lines[-1].rstrip(".,:;") + "…"
    return lines


def analysis_card_svg(title: str, stamp: str, rows: list, crests: list) -> str:
    """The card for one analysis piece: brand and date, the crests of the clubs
    it is about, the headline, and up to four labelled percentages."""
    crest_svg = "".join(
        f'\n  <image x="{1120 - 110 * (len(crests) - k)}" y="40" width="96" height="96" href="{uri}"/>'
        for k, uri in enumerate(crests[:2])
    )
    title_svg = "".join(
        f'\n  <text x="80" y="{230 + 62 * k}" font-family="Helvetica,Arial,sans-serif" font-size="52"'
        f' font-weight="700" fill="{INK}">{esc(line)}</text>'
        for k, line in enumerate(_title_lines(title))
    )
    row_svg = "".join(
        f'\n  <text x="{80 + 265 * k}" y="480" font-family="Helvetica,Arial,sans-serif" font-size="24"'
        f' fill="{MUTED}">{esc(label)}</text>'
        f'\n  <text x="{80 + 265 * k}" y="540" font-family="Helvetica,Arial,sans-serif" font-size="56"'
        f' font-weight="700" fill="{Z4 if k else CAMPO}">{esc(value)}</text>'
        for k, (label, value) in enumerate(rows[:4])
    )
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">
  <rect width="{W}" height="{H}" fill="{PAPER}"/>
  <rect x="0" y="0" width="{W}" height="14" fill="{CAMPO}"/>
  <text x="80" y="90" font-family="Helvetica,Arial,sans-serif" font-size="30"
        font-weight="700" fill="{CAMPO}" letter-spacing="3">DATA BRASILEIRÃO · ANÁLISE</text>
  <text x="80" y="130" font-family="Helvetica,Arial,sans-serif" font-size="24" fill="{MUTED}">{esc(stamp)}</text>{crest_svg}{title_svg}{row_svg}
  <text x="80" y="600" font-family="Helvetica,Arial,sans-serif" font-size="22"
        fill="{MUTED}">databrasileirao.com.br/analise</text>
</svg>"""
