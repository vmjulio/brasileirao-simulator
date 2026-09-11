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
REFERENCE_MD5 = "7671056d36a97ad057230b64129cd8ac"


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


# T9.3 translates strings.pt.json. Until then the pt build reads the same
# English values as strings.en.json, so this grep is *expected* to find every
# token below - flip this one constant to False once T9.3 lands, and the
# assertion below flips from "hits" to "no hits" with it.
EXPECT_ENGLISH_TOKENS_IN_PT_BUILD = True

TRANSLATION_TOKENS = ["Title", "Relegation", "season", "matches", "points", "chance", "Jan", "Feb"]


def test_pt_build_english_token_grep(tmp_path):
    out = tmp_path / "forecasts.pt.html"
    build_report.build(out, lang="pt", benchmark_path=FIXTURE_BENCHMARK, models=["incumbent"])
    content = out.read_text(encoding="utf-8")

    hits = [token for token in TRANSLATION_TOKENS if token in content]

    if EXPECT_ENGLISH_TOKENS_IN_PT_BUILD:
        assert hits, "expected untranslated English tokens before T9.3 translates strings.pt.json"
    else:
        assert not hits, f"untranslated English tokens survived T9.3's translation: {hits}"
