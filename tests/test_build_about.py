"""The Quem somos page: a real page at /quem-somos/, Portuguese only, sharing the
site's header, and not yet listed in the menu."""

from brasileirao_simulator.entrypoints.report import build_about


def build(tmp_path, **kw):
    out = tmp_path / "quem-somos" / "index.html"
    build_about.build(out, **kw)
    return out.read_text()


def test_it_is_a_complete_document_with_its_own_preview(tmp_path):
    html = build(tmp_path)
    assert html.startswith("<!doctype html>")
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in html
    assert '<html lang="pt-BR">' in html
    assert 'property="og:url" content="https://databrasileirao.com.br/quem-somos/"' in html
    assert "<h1>Quem somos</h1>" in html
    assert html.isascii()


def test_it_says_who_is_behind_it_and_how_to_reach_him(tmp_path):
    html = build(tmp_path)
    assert "Vitor Julio" in html
    assert '<a href="mailto:vitor.mjulio@gmail.com">vitor.mjulio@gmail.com</a>' in html
    assert "Fonte: Data Brasileir&#227;o" in html
    # The profile link carries no share-tracking parameters.
    assert 'href="https://www.linkedin.com/in/vitor-julio"' in html
    assert "utm_" not in html


def test_it_keeps_the_site_rules(tmp_path):
    html = build(tmp_path)
    assert "Elo" not in html
    assert "20.000" not in html and "20,000" not in html
    assert "apostas" in html  # says plainly it is not a betting site


def test_it_is_not_in_the_menu_yet(tmp_path):
    nav = build(tmp_path).split('<nav class="pages"')[1].split("</nav>")[0]
    assert "quem-somos" not in nav
    assert 'aria-current="page"' not in nav  # no menu entry is this page


def test_analytics_only_when_asked(tmp_path):
    assert "googletagmanager" not in build(tmp_path)
    html = build(tmp_path, ga4="G-TEST1234")
    assert html.index("<!doctype html>") < html.index("googletagmanager")
