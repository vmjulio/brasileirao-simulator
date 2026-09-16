from brasileirao_simulator.entrypoints.report import og_card


def test_pct_follows_the_site_rule():
    assert [og_card.pct(v) for v in (0.2, 0.6, 15.06, 99.4, 99.5)] == ["<1%", "1%", "15%", "99%", ">99%"]


def test_pct_rounds_halves_up_like_the_site_js():
    assert og_card.pct(22.5) == "23%"
    assert og_card.pct(12.5) == "13%"


def test_br_date():
    assert og_card.br_date("2026-09-14") == "14 set 2026"


def test_analysis_card_carries_title_rows_and_crests_and_escapes():
    svg = og_card.analysis_card_svg(
        title="Grêmio e Inter: em 15% das simulações, nenhum dos dois cai",
        stamp="14 set 2026 · após a 27ª rodada",
        rows=[("Nenhum cai", "15%"), ("Os dois caem", "23%"), ("A & B", "<1%")],
        crests=["data:image/svg+xml;base64,AAA", "data:image/svg+xml;base64,BBB"],
    )
    assert svg.startswith("<svg") and 'width="1200"' in svg
    assert "nenhum dos dois" in svg and "Nenhum cai" in svg and "15%" in svg
    assert "A &amp; B" in svg and "&lt;1%" in svg
    assert svg.count("<image ") == 2
