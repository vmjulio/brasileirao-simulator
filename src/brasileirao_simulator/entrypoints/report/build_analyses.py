"""Build the Análise section: `/analise/` and one page per piece.

    PYTHONPATH=src python3 src/brasileirao_simulator/entrypoints/report/build_analyses.py --out site/analise [--ga4 G-…]

Pieces live in `analyses/<slug>/` at the repository root (see analysis_piece.py
for the format). Pages share the production page's header, tokens and theme
through report/partials. Portuguese only. Standard library only; the PNG card
needs `rsvg-convert` on the host (see og_card.rasterise).
"""

import argparse
import html as html_lib
import json
import re
from pathlib import Path
from typing import Optional

from brasileirao_simulator.entrypoints.report import charts, markdown_lite, og_card
from brasileirao_simulator.entrypoints.report.analysis_piece import fill, load_piece, placeholders, shares
from brasileirao_simulator.entrypoints.report.build_report import (
    REPORT_DIR, _with_ga4, expand_includes, header_params, load_strings, render_strings,
)

ANALYSES_DIR = REPORT_DIR.parents[3] / "analyses"
SITE = "https://databrasileirao.com.br"
LANG = "pt"


def build(analyses_dir: Path, out_dir: Path, ga4: Optional[str] = None, cards: bool = True,
          logos: Optional[dict] = None, display: Optional[dict] = None) -> list:
    analyses_dir, out_dir = Path(analyses_dir), Path(out_dir)
    strings = load_strings(LANG)
    logos = logos if logos is not None else json.loads((REPORT_DIR / "logos.json").read_text(encoding="utf-8"))
    display = display if display is not None else og_card.display_names()

    folders = sorted(p for p in analyses_dir.iterdir() if (p / "piece.pt.md").is_file()) if analyses_dir.is_dir() else []
    pieces = sorted((load_piece(f) for f in folders), key=lambda p: (p.date, p.slug), reverse=True)

    written = [_write(out_dir / "index.html", _index(pieces, strings, display), ga4)]
    for piece in pieces:
        folder = out_dir / piece.slug
        written.append(_write(folder / "index.html", _piece_page(piece, strings, logos, display), ga4))
        if cards:
            _card(piece, folder / "og.png", logos, display)
    return written


def _shell(template_name: str, strings: dict) -> str:
    template = (REPORT_DIR / template_name).read_text(encoding="utf-8")
    template = expand_includes(template, REPORT_DIR / "partials", header_params("analise"))
    return render_strings(template, strings)


def _piece_page(piece, strings: dict, logos: dict, display: dict) -> str:
    values = placeholders(piece, display)
    title = fill(piece.title, values, piece.slug)
    summary = fill(piece.summary, values, piece.slug)
    body = markdown_lite.render(fill(piece.body, values, piece.slug))
    a, b = piece.data["clubs"]
    labels = {"down": strings["analysis.matrix_down"], "safe": strings["analysis.matrix_safe"],
              "total": strings["analysis.matrix_total"], "caption": strings["analysis.matrix_caption"]}
    chart_html = "\n".join(
        charts.RENDERERS[name](piece.data["counts"], {"name": values["club_a"], "crest": logos.get(a)},
                               {"name": values["club_b"], "crest": logos.get(b)}, labels)
        for name in piece.charts
    )
    stamp = {"date": values["date"], "round": values["round"]}
    replacements = {
        "__TITLE__": html_lib.escape(title), "__SUMMARY__": html_lib.escape(summary),
        "__OG_IMAGE__": f"{SITE}/analise/{piece.slug}/og.png", "__URL__": f"{SITE}/analise/{piece.slug}/",
        "__KICKER__": html_lib.escape(strings["analysis.kicker"].format(**stamp)),
        "__CHARTS__": chart_html, "__BODY__": body,
        "__FOOTER__": strings["analysis.footer"].format(**stamp),
    }
    page = _shell("template_analysis.html", strings)
    return re.sub("|".join(replacements), lambda m: replacements[m.group(0)], page)


def _index(pieces: list, strings: dict, display: dict) -> str:
    if pieces:
        cards = "\n".join(
            f'  <a class="index-card" href="/analise/{p.slug}/">'
            f'<time datetime="{p.date}">{og_card.br_date(p.date)}</time>'
            f"<h2>{html_lib.escape(fill(p.title, placeholders(p, display), p.slug))}</h2>"
            f"<p>{html_lib.escape(fill(p.summary, placeholders(p, display), p.slug))}</p></a>"
            for p in pieces
        )
    else:
        cards = f'  <p class="summary">{strings["analysis.empty"]}</p>'
    return _shell("template_analysis_index.html", strings).replace("__CARDS__", cards)


def _write(path: Path, page: str, ga4: Optional[str]) -> Path:
    page = _with_ga4(page, ga4).encode("ascii", "xmlcharrefreplace").decode("ascii")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page, encoding="ascii")
    return path


def _card(piece, out: Path, logos: dict, display: dict) -> None:
    values = placeholders(piece, display)
    s = shares(piece.data["counts"])
    rows = [("Nenhum cai", og_card.pct(s["neither"])), (f"Só {values['club_a']}", og_card.pct(s["only_a"])),
            (f"Só {values['club_b']}", og_card.pct(s["only_b"])), ("Os dois caem", og_card.pct(s["both"]))]
    crests = [logos[c] for c in piece.data["clubs"] if c in logos]
    svg = og_card.analysis_card_svg(fill(piece.title, values, piece.slug),
                                    f"{values['date']} · após a {values['round']}ª rodada", rows, crests)
    source = out.with_suffix(".svg")
    source.write_text(svg, encoding="utf-8")
    og_card.rasterise(source, out)
    source.unlink()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--analyses", default=str(ANALYSES_DIR))
    parser.add_argument("--out", required=True)
    parser.add_argument("--ga4", default=None)
    args = parser.parse_args()
    for path in build(Path(args.analyses), Path(args.out), ga4=args.ga4):
        print(f"wrote {path}")
