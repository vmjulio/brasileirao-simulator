"""Shared partials: the production page's header, tokens, base CSS and theme
toggle live in report/partials and are expanded at build time, so the main
page and the analysis pages cannot drift apart."""

import pytest

from brasileirao_simulator.entrypoints.report import build_report


def test_expand_includes_inserts_partials_verbatim_and_fills_params(tmp_path):
    (tmp_path / "a.html").write_text("<b>{{@who}}</b>\n{{> b.css}}")
    (tmp_path / "b.css").write_text("x{color:red}\n")

    out = build_report.expand_includes("[{{> a.html}}]", tmp_path, {"who": "hi"})

    assert out == "[<b>hi</b>\nx{color:red}\n]"


def test_expand_includes_leaves_missing_params_empty(tmp_path):
    (tmp_path / "a.html").write_text("<a{{@current}}>")
    assert build_report.expand_includes("{{> a.html}}", tmp_path) == "<a>"


def test_expand_includes_names_a_missing_partial(tmp_path):
    with pytest.raises(FileNotFoundError, match="nope.html"):
        build_report.expand_includes("{{> nope.html}}", tmp_path)


def test_the_production_page_has_no_unexpanded_markers(tmp_path):
    out = tmp_path / "db.pt.html"
    build_report.build(out, lang="pt", version="db", seasons=["2026"])
    html = out.read_text()
    assert "{{>" not in html and "{{@" not in html
