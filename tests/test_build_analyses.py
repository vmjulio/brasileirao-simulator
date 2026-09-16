import json

import pytest

from brasileirao_simulator.entrypoints.report import build_analyses
from brasileirao_simulator.entrypoints.report.analysis_piece import PieceError

DATA = {
    "question": "relegation_pairs", "season": 2026, "as_of": "2026-09-14", "round": 27,
    "clubs": ["Gremio", "Internacional"],
    "counts": {"neither": 3010, "only_a": 4661, "only_b": 7768, "both": 4561},
    "generated_by": "test", "generated_at": "2026-09-15",
}


def piece(root, slug, date, title="{club_a} e {club_b}: {neither_pct} sem queda"):
    folder = root / slug
    folder.mkdir(parents=True)
    (folder / "piece.pt.md").write_text(
        f'---\nslug: {slug}\ntitle: "{title}"\nsummary: "Os dois caem em {{both_pct}}."\n'
        f"date: {date}\ncharts: [pair_matrix]\n---\nNenhum cai em **{{neither_pct}}** dos cenários.\n",
        encoding="utf-8")
    (folder / "data.json").write_text(json.dumps({**DATA, "as_of": date}), encoding="utf-8")


def build(tmp_path, **kw):
    return build_analyses.build(tmp_path / "analyses", tmp_path / "out", cards=False,
                                logos={"Gremio": "data:image/svg+xml;base64,AAA"},
                                display={"Gremio": "Grêmio"}, **kw)


def test_a_piece_page_is_a_complete_document(tmp_path):
    piece(tmp_path / "analyses", "gremio-inter", "2026-09-14")
    build(tmp_path)
    html = (tmp_path / "out" / "gremio-inter" / "index.html").read_text()
    assert html.startswith("<!doctype html>")
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in html
    assert "<h1>Gr&#234;mio e Internacional: 15% sem queda</h1>" in html
    assert "<strong>15%</strong>" in html
    assert 'property="og:url" content="https://databrasileirao.com.br/analise/gremio-inter/"' in html
    assert 'property="og:image" content="https://databrasileirao.com.br/analise/gremio-inter/og.png"' in html
    assert 'class="page analise-link" href="/analise/" aria-current="page"' in html
    assert "pair-matrix" in html and "14 set 2026" in html
    assert "4561" not in html and "20000" not in html
    assert html.isascii()


def test_the_index_lists_pieces_newest_first(tmp_path):
    piece(tmp_path / "analyses", "velha", "2026-09-01", title="Antiga {neither_pct}")
    piece(tmp_path / "analyses", "nova", "2026-09-14", title="Nova {neither_pct}")
    paths = build(tmp_path)
    index = (tmp_path / "out" / "index.html").read_text()
    assert paths[0] == tmp_path / "out" / "index.html"
    assert index.index("/analise/nova/") < index.index("/analise/velha/")


def test_an_empty_folder_builds_the_empty_index(tmp_path):
    (tmp_path / "analyses").mkdir()
    build(tmp_path)
    assert "Nenhuma an&#225;lise publicada ainda." in (tmp_path / "out" / "index.html").read_text()


def test_a_broken_piece_stops_the_build(tmp_path):
    piece(tmp_path / "analyses", "quebrada", "2026-09-14", title="{nope}")
    with pytest.raises(PieceError, match="nope"):
        build(tmp_path)


def test_analytics_only_when_asked(tmp_path):
    piece(tmp_path / "analyses", "gremio-inter", "2026-09-14")
    build(tmp_path, ga4="G-TEST1234")
    html = (tmp_path / "out" / "gremio-inter" / "index.html").read_text()
    assert html.index("<!doctype html>") < html.index("googletagmanager")
