"""The report build must reproduce the page already handed to colleagues.

`benchmark.json` keeps growing new seasons as later, unrelated work lands, so
this test pins it to the exact snapshot the reference page was built from
(recovered from git history at the commit that was current when the page was
generated) rather than reading the live export. Everything else the build
reads - forecast_dataset.json, the recency CSV, logos.json, template.html -
has not changed since, so no other fixture is needed.
"""

import hashlib
from pathlib import Path

from brasileirao_simulator.entrypoints.report import build_report

# Moved once, deliberately, when club-display-names landed: the template gained
# nameOf() and the payload a display_names map, so the page colleagues had
# (46676a0e...) is no longer reproducible from the current template by design.
# This is the default `en` build of the current template against the frozen
# benchmark fixture. Move it again only for an intended change to the page.
# Moved again, deliberately, when explorer-model-dropdown landed: the page
# gained a model picker and per-model text, and the payload became one entry
# per model. Pinned to the incumbent alone so a new model's dataset appearing
# in the exports cannot move it.
# Moved on 2026-09-12: the page embeds the forecast archive, and the archive
# gained three dates when the 2026 season file was refreshed (results through
# 11 September) and both models were topped up to 20,000 simulations on them.
# The template is untouched; this gate pins the built bytes, so a data refresh
# moves it by construction.
# Moved again on 2026-09-12: the payload gained an `archive` summary (how
# many seasons and dates stand behind the page) so a page bundling one
# season can still describe the whole archive in its header.
REFERENCE_MD5 = "f43ee44c9c6c5c9c163ee075cafaf884"
FIXTURE_BENCHMARK = (
    Path(__file__).resolve().parent / "fixtures" / "report_benchmark_2026-09-10T0943.json"
)


def test_build_reproduces_the_reference_page_byte_for_byte(tmp_path):
    """The default build of the current template is pinned, so unintended
    drift in the build - a changed escape, a reordered key - is caught."""
    out = tmp_path / "forecasts.html"

    build_report.build(out, benchmark_path=FIXTURE_BENCHMARK, models=["incumbent"])

    digest = hashlib.md5(out.read_bytes()).hexdigest()
    assert digest == REFERENCE_MD5


def test_display_names_are_additive(tmp_path):
    """The additivity gate for club-display-names, hash-free: with the map
    empty, everything outside the data payload is byte-identical to the
    default build, and the payload differs only by carrying an empty map.
    The template never branches on the map at build time; names are looked
    up at render time through nameOf()."""
    on, off = tmp_path / "on.html", tmp_path / "off.html"

    build_report.build(on, benchmark_path=FIXTURE_BENCHMARK, models=["incumbent"])
    build_report.build(off, benchmark_path=FIXTURE_BENCHMARK, models=["incumbent"], display_names={})

    assert _markup_only(on.read_text()) == _markup_only(off.read_text())
    assert '"display_names":{}' in off.read_text()


def _markup_only(html: str) -> str:
    """Everything except the embedded data payload, whose keys are - and must
    stay - the canonical names."""
    head, _, rest = html.partition('<script id="data"')
    _, _, tail = rest.partition("</script>")
    return head + tail


def test_display_names_render_but_never_replace_the_canonical_keys(tmp_path):
    out = tmp_path / "forecasts.html"

    build_report.build(out, benchmark_path=FIXTURE_BENCHMARK, models=["incumbent"])
    html = out.read_text()

    # The map travels with the page and carries the renderings the ticket names.
    assert '"Vasco DA Gama":"Vasco da Gama"' in html
    assert '"Gremio":"Gr\\u00eamio"' in html
    # Data keys are untouched: the canonical name is still what the page looks up by.
    assert '"Vasco DA Gama":{' in html
    # Rendering goes through nameOf() at runtime, not through the build.
    assert "nameOf(" in _markup_only(html)


def test_the_site_page_is_a_standalone_document_even_with_analytics(tmp_path):
    """The site serves the built file bare - no host wraps it the way the
    artifact publisher did. Without a doctype the browser renders in quirks
    mode, and without a viewport meta a phone lays the page out 980px wide
    and never reaches the phone layout. The GA4 tag must not push the
    doctype off the first line, which would put the page back in quirks mode."""
    out = tmp_path / "site.html"

    build_report.build(out, lang="pt", version="db", seasons=["current"], ga4="G-TEST1234")
    html = out.read_text()

    assert html.startswith("<!doctype html>")
    assert '<html lang="pt-BR">' in html
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in html
    assert html.index("<!doctype html>") < html.index("googletagmanager")
