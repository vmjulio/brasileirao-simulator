import json

import pytest

from brasileirao_simulator.entrypoints.report import analysis_piece as ap

DATA = {
    "question": "relegation_pairs", "season": 2026, "as_of": "2026-09-14", "round": 27,
    "clubs": ["Gremio", "Internacional"],
    "counts": {"neither": 3010, "only_a": 4661, "only_b": 7768, "both": 4561},
    "generated_by": "test", "generated_at": "2026-09-15",
}
PIECE = """---
slug: gremio-inter-rebaixamento
title: "{club_a} e {club_b}: em {neither_pct} das simulações, nenhum dos dois cai"
summary: "Os dois juntos, em {both_pct}."
date: 2026-09-14
charts: [pair_matrix]
---
Texto com {only_a_pct}.
"""


def write(tmp_path, piece=PIECE, data=DATA, slug="gremio-inter-rebaixamento"):
    folder = tmp_path / slug
    folder.mkdir()
    (folder / "piece.pt.md").write_text(piece, encoding="utf-8")
    if data is not None:
        (folder / "data.json").write_text(json.dumps(data), encoding="utf-8")
    return folder


def test_load_piece_reads_front_matter_body_and_data(tmp_path):
    piece = ap.load_piece(write(tmp_path))
    assert piece.slug == "gremio-inter-rebaixamento"
    assert piece.charts == ("pair_matrix",)
    assert piece.date == "2026-09-14"
    assert piece.body.strip() == "Texto com {only_a_pct}."
    assert piece.data["round"] == 27


def test_shares_are_percentages_of_the_four_counts():
    s = ap.shares(DATA["counts"])
    assert abs(s["neither"] - 15.05) < 1e-9 and abs(s["both"] - 22.805) < 1e-9
    assert abs(s["a_down"] - 46.11) < 1e-9 and abs(s["b_down"] - 61.645) < 1e-9
    assert abs(s["either"] + s["neither"] - 100) < 1e-9


def test_placeholders_and_fill(tmp_path):
    piece = ap.load_piece(write(tmp_path))
    values = ap.placeholders(piece, {"Gremio": "Grêmio"})
    assert values["neither_pct"] == "15%" and values["both_pct"] == "23%"
    assert values["club_a"] == "Grêmio" and values["club_b"] == "Internacional"
    assert values["date"] == "14 set 2026" and values["round"] == "27"
    assert ap.fill(piece.title, values, piece.slug) == "Grêmio e Internacional: em 15% das simulações, nenhum dos dois cai"


def test_the_total_is_never_a_placeholder(tmp_path):
    values = ap.placeholders(ap.load_piece(write(tmp_path)), {})
    assert "20000" not in "".join(values.values()) and "20.000" not in "".join(values.values())


@pytest.mark.parametrize("change, message", [
    (lambda p, d: (p, None), "data.json"),
    (lambda p, d: (p.replace("date: 2026-09-14", "date: 2026-09-13"), d), "as_of"),
    (lambda p, d: (p.replace("[pair_matrix]", "[pie]"), d), "pie"),
    (lambda p, d: (p, {**d, "question": "other"}), "other"),
    (lambda p, d: (p, {**d, "counts": {**d["counts"], "both": -1}}), "counts"),
    (lambda p, d: (p.replace("slug: gremio-inter-rebaixamento", "slug: outro"), d), "slug"),
    (lambda p, d: (p, {k: v for k, v in d.items() if k != "round"}), "round"),
    (lambda p, d: (p, {**d, "counts": {**d["counts"], "both": True}}), "counts"),
])
def test_invalid_pieces_fail_with_the_cause(tmp_path, change, message):
    piece, data = change(PIECE, DATA)
    with pytest.raises(ap.PieceError, match=message):
        ap.load_piece(write(tmp_path, piece, data))


def test_an_unknown_placeholder_names_itself(tmp_path):
    piece = ap.load_piece(write(tmp_path))
    with pytest.raises(ap.PieceError, match="nope"):
        ap.fill("{nope}", ap.placeholders(piece, {}), piece.slug)
