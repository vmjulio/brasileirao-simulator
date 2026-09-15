"""Gate for T9.1's strings table: every user-facing string in the report
template lives in strings.{lang}.json, keyed by id, and the `en` build still
reproduces the reference page byte-for-byte.

`build_report.render_strings` replaces two kinds of token:
  {{key}}     -- the whole value, verbatim (or a JSON array rendered as a
                 compact JS array literal, for the month-abbreviation table).
  {{key#i}}   -- the i-th static segment of a value that carries runtime
                 `{name}` placeholders, split on those placeholders. This is
                 what lets one strings.json entry hold a whole sentence for a
                 translator while the surrounding, unchanged JS concatenation
                 still supplies the runtime value between segments.
"""

import hashlib
import json
import re
from pathlib import Path

from brasileirao_simulator.entrypoints.report import build_report

REPORT_DIR = Path(__file__).resolve().parent.parent / "src" / "brasileirao_simulator" / "entrypoints" / "report"
FIXTURE_BENCHMARK = Path(__file__).resolve().parent / "fixtures" / "report_benchmark_2026-09-10T0943.json"
# Same reference test_report_build.py pins; moved together when
# club-display-names changed the template (see the comment there).
# Moved on 2026-09-12: the page embeds the forecast archive, and the archive
# gained three dates when the 2026 season file was refreshed (results through
# 11 September) and both models were topped up to 20,000 simulations on them.
# The template is untouched; this gate pins the built bytes, so a data refresh
# moves it by construction.
# Moved again on 2026-09-12: the payload gained an `archive` summary (how
# many seasons and dates stand behind the page) so a page bundling one
# season can still describe the whole archive in its header.
REFERENCE_MD5 = "f43ee44c9c6c5c9c163ee075cafaf884"


def _load(lang):
    with open(REPORT_DIR / f"strings.{lang}.json", encoding="utf-8") as f:
        return json.load(f)


def test_lang_en_build_is_byte_identical_to_the_reference_page(tmp_path):
    out = tmp_path / "forecasts.en.html"

    build_report.build(out, lang="en", benchmark_path=FIXTURE_BENCHMARK, models=["incumbent"])

    digest = hashlib.md5(out.read_bytes()).hexdigest()
    assert digest == REFERENCE_MD5


def test_lang_pt_build_runs_and_produces_a_page(tmp_path):
    """T9.3 translates strings.pt.json; T9.1 only has to prove the pt build
    runs end to end (identical keys, valid substitution, a real HTML page)."""
    out = tmp_path / "forecasts.pt.html"

    build_report.build(out, lang="pt", benchmark_path=FIXTURE_BENCHMARK, models=["incumbent"])

    assert out.exists()
    assert out.stat().st_size > 1_000_000


def test_strings_en_and_pt_have_identical_key_sets():
    en, pt = _load("en"), _load("pt")
    assert set(en) == set(pt)


_LITERAL_RE = re.compile(r"""(['"])((?:\\.|(?!\1).)*)\1""")
_TOKEN_RE = re.compile(r"\{\{[\w.]+(?:#\d+)?\}\}")
_THREE_WORD_RUN_RE = re.compile(r"[A-Za-zÀ-ÿ]+(?:[ \t]+[A-Za-zÀ-ÿ]+){2,}")

# The COLORS lookup keys the chart palette by the dataset's canonical team
# name (e.g. "Vasco DA Gama") - that is a data key matching DATA.seasons,
# never rendered as prose, and club display names are T9.2's job, not T9.1's.
# It is the one place a multi-word quoted literal is allowed to survive.
_COLORS_OBJECT_START = 'var COLORS = {'
_COLORS_OBJECT_END = '};'


def _template_text():
    with open(REPORT_DIR / "template.html", encoding="utf-8") as f:
        return f.read()


def test_template_has_no_untranslated_prose_outside_placeholder_tokens():
    text = _template_text()
    i = text.index(_COLORS_OBJECT_START)
    j = text.index(_COLORS_OBJECT_END, i) + len(_COLORS_OBJECT_END)
    outside_colors = text[:i] + text[j:]

    violations = []
    for m in _LITERAL_RE.finditer(outside_colors):
        literal = m.group(2)
        stripped = _TOKEN_RE.sub(" ", literal)
        if _THREE_WORD_RUN_RE.search(stripped):
            violations.append(literal)

    assert not violations, f"quoted prose survives outside {{{{key}}}} tokens: {violations!r}"


# T9.3 landed on 2026-09-11: strings.pt.json carries a real translation, so
# the pt build must no longer show the English prose. Grepping for bare words
# would be useless here - the page's data payload and its JavaScript are full
# of English identifiers - so this pins whole rendered sentences instead, one
# pair per part of the page.
# The pt copy was rewritten on 2026-09-13 into the register Brazilian football
# media uses - "time" over "clube", "chance" over "probabilidade", "Z-4" - so
# these sentences moved. They are still whole rendered sentences, one per part
# of the page; only the wording is new.
TRANSLATED_PAIRS = [
    ("The race", "A briga"),
    ("Relegation probability", "Risco de rebaixamento"),
    ("Where does each club finish?", "Onde cada time termina?"),
    ("How many points keep you up?", "Quantos pontos livram do Z-4?"),
    ("Is the model calibrated?", "O modelo est\u00e1 calibrado?"),
    ("Against another forecaster", "Contra outro previsor"),
]


def _as_written(text: str) -> list:
    """The forms a string can take in the built page, which is pure ASCII:
    markup takes HTML entities, JavaScript string literals take \\u escapes
    (see build_report.build)."""
    return [text,
            text.encode("ascii", "xmlcharrefreplace").decode("ascii"),
            "".join(c if c.isascii() else "\\u%04x" % ord(c) for c in text)]


def test_pt_build_carries_the_translation_and_not_the_english_prose(tmp_path):
    out = tmp_path / "forecasts.pt.html"
    build_report.build(out, lang="pt", benchmark_path=FIXTURE_BENCHMARK, models=["incumbent"])
    content = out.read_text(encoding="utf-8")

    for english, portuguese in TRANSLATED_PAIRS:
        assert any(form in content for form in _as_written(portuguese)), f"missing translation: {portuguese!r}"
        assert english not in content, f"English prose survives in the pt build: {english!r}"


# The headline and the forecast table belong to the v2/v3 designs, so they are
# checked on a build that uses them.
V2_TRANSLATED_PAIRS = [
    ("Who wins the league, and who goes down", "Quem leva o t\u00edtulo e quem cai"),
    ("Every club, every outcome", "Time a time"),
]


def test_pt_build_of_the_later_designs_is_translated_too(tmp_path):
    for version in ("v2", "v3"):
        out = tmp_path / f"forecasts.{version}.pt.html"
        build_report.build(out, lang="pt", benchmark_path=FIXTURE_BENCHMARK, models=["incumbent"], version=version)
        content = out.read_text(encoding="utf-8")
        for english, portuguese in V2_TRANSLATED_PAIRS:
            assert any(form in content for form in _as_written(portuguese)), f"{version}: missing {portuguese!r}"
            assert english not in content, f"{version}: English prose survives: {english!r}"


def test_every_string_differs_between_en_and_pt_unless_it_is_punctuation_or_a_name():
    """Whatever is identical in both tables should be identical on purpose:
    separators, dashes, a scoring rule's name, a club-neutral abbreviation."""
    en, pt = _load("en"), _load("pt")
    same = {k for k in en if en[k] == pt[k]}
    allowed = {
        "shared.en_dash", "shared.middot_sep", "shared.slash_sep", "shared.minus", "shared.em_dash",
        "shared.iter_abbr", "bench.th_model_rps", "format.ordinal_suffix", "model.elo.short",
        "v2.kicker", "v2.table.key_bad", "v2.table.th_releg", "model.elo.name",
        "points.shape_15",  # a bare <p style="..."> opener, no prose in it
        "brand.name",  # the publication's name - a proper noun, not prose
        "standings.th_proj",  # "proj." - a column abbreviation, same in both
        "db.points.unit",  # " pts" - a points abbreviation, same in both
    }
    assert same <= allowed, f"untranslated strings: {sorted(same - allowed)!r}"


def test_v2_builds_from_the_same_strings_and_keeps_prose_in_tokens(tmp_path):
    """The v2 design reads the same payload and strings tables as v1; its
    template, like v1's, keeps every sentence in a strings token."""
    for lang in ("en", "pt"):
        out = tmp_path / f"forecasts.v2.{lang}.html"
        build_report.build(out, lang=lang, benchmark_path=FIXTURE_BENCHMARK, models=["incumbent"], version="v2")
        assert out.stat().st_size > 1_000_000

    text = (REPORT_DIR / "template_v2.html").read_text(encoding="utf-8")
    i = text.index(_COLORS_OBJECT_START)
    j = text.index(_COLORS_OBJECT_END, i) + len(_COLORS_OBJECT_END)
    outside_colors = text[:i] + text[j:]
    violations = [m.group(2) for m in _LITERAL_RE.finditer(outside_colors)
                  if _THREE_WORD_RUN_RE.search(_TOKEN_RE.sub(" ", m.group(2)))]
    assert not violations, f"quoted prose survives outside {{{{key}}}} tokens: {violations!r}"
