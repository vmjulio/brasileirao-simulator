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
REFERENCE_MD5 = "7afb8b84dc0bd72dbcb14d3305c33dc9"
FIXTURE_BENCHMARK = (
    Path(__file__).resolve().parent / "fixtures" / "report_benchmark_2026-09-10T0943.json"
)


def test_build_reproduces_the_reference_page_byte_for_byte(tmp_path):
    """The default build of the current template is pinned, so unintended
    drift in the build - a changed escape, a reordered key - is caught."""
    out = tmp_path / "forecasts.html"

    build_report.build(out, benchmark_path=FIXTURE_BENCHMARK)

    digest = hashlib.md5(out.read_bytes()).hexdigest()
    assert digest == REFERENCE_MD5


def test_display_names_are_additive(tmp_path):
    """The additivity gate for club-display-names, hash-free: with the map
    empty, everything outside the data payload is byte-identical to the
    default build, and the payload differs only by carrying an empty map.
    The template never branches on the map at build time; names are looked
    up at render time through nameOf()."""
    on, off = tmp_path / "on.html", tmp_path / "off.html"

    build_report.build(on, benchmark_path=FIXTURE_BENCHMARK)
    build_report.build(off, benchmark_path=FIXTURE_BENCHMARK, display_names={})

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

    build_report.build(out, benchmark_path=FIXTURE_BENCHMARK)
    html = out.read_text()

    # The map travels with the page and carries the renderings the ticket names.
    assert '"Vasco DA Gama":"Vasco da Gama"' in html
    assert '"Gremio":"Gr\\u00eamio"' in html
    # Data keys are untouched: the canonical name is still what the page looks up by.
    assert '"Vasco DA Gama":{' in html
    # Rendering goes through nameOf() at runtime, not through the build.
    assert "nameOf(" in _markup_only(html)
