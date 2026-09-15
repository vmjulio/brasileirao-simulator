"""Chart renderers an analysis piece can list in its front matter.

Each renderer returns self-contained HTML styled by template_analysis.html.
Cells show percentages only - never the counts behind them.
"""

from brasileirao_simulator.entrypoints.report.analysis_piece import shares
from brasileirao_simulator.entrypoints.report.og_card import esc, pct


def _club(club: dict, suffix: str, crest: bool = False) -> str:
    crest_html = f'<img class="crest" src="{club["crest"]}" alt="">' if crest and club.get("crest") else ""
    return f'{crest_html}{esc(club["name"])} {esc(suffix)}'


def _cell(share: float, good: bool = False) -> str:
    # Shade by share on the site's bad (red) or good (green) scale.
    rgb = "var(--good-rgb)" if good else "var(--bad-rgb)"
    alpha = 0.06 + 0.6 * share / 100
    cls = "cell good" if good else "cell"
    return f'<td class="{cls}" style="background:rgba({rgb},{alpha:.3f})">{pct(share)}</td>'


def pair_matrix(counts: dict, club_a: dict, club_b: dict, labels: dict) -> str:
    """Two clubs, one outcome each (down / not down): the four joint cases,
    and each club's own chance in the totals."""
    s = shares(counts)
    return (
        f'<figure class="chart pair-matrix"><div class="scroller"><table>'
        f'<caption>{esc(labels["caption"])}</caption>'
        f'<thead><tr><th></th><th scope="col">{_club(club_b, labels["down"], crest=True)}</th>'
        f'<th scope="col">{_club(club_b, labels["safe"], crest=False)}</th><th scope="col">{esc(labels["total"])}</th></tr></thead>'
        f'<tbody>'
        f'<tr><th scope="row">{_club(club_a, labels["down"], crest=True)}</th>{_cell(s["both"])}{_cell(s["only_a"])}'
        f'<td class="total">{pct(s["a_down"])}</td></tr>'
        f'<tr><th scope="row">{_club(club_a, labels["safe"], crest=False)}</th>{_cell(s["only_b"])}{_cell(s["neither"], good=True)}'
        f'<td class="total">{pct(s["a_safe"])}</td></tr>'
        f'<tr class="total"><th scope="row">{esc(labels["total"])}</th><td>{pct(s["b_down"])}</td>'
        f'<td>{pct(s["b_safe"])}</td><td>100%</td></tr>'
        f'</tbody></table></div></figure>'
    )


RENDERERS = {"pair_matrix": pair_matrix}
