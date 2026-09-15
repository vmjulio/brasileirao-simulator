from brasileirao_simulator.entrypoints.report import charts

COUNTS = {"neither": 3010, "only_a": 4661, "only_b": 7768, "both": 4561}
LABELS = {"down": "cai", "safe": "não cai", "total": "Total", "caption": "Cenários"}


def html():
    return charts.pair_matrix(COUNTS, {"name": "Grêmio", "crest": "data:image/svg+xml;base64,AAA"},
                              {"name": "Internacional", "crest": None}, LABELS)


def test_the_four_cells_and_the_totals():
    h = html()
    for text in ("23%", "15%", "39%", "46%", "54%", "62%", "38%", "100%"):
        assert f">{text}<" in h, text


def test_labels_crests_and_the_good_cell():
    h = html()
    assert "Grêmio cai" in h and "Internacional não cai" in h
    assert h.count("<img ") == 1
    assert 'class="cell good"' in h and h.count('class="cell good"') == 1


def test_no_counts_on_the_page():
    h = html()
    for n in ("3010", "4661", "7768", "4561", "20000"):
        assert n not in h, n


def test_registry():
    assert charts.RENDERERS["pair_matrix"] is charts.pair_matrix
