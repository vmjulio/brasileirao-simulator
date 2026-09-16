# Análise Pages Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish dated editorial analysis pieces at `databrasileirao.com.br/analise/` — an index plus one page per piece — starting with "Grêmio e Inter: em 15% das simulações, nenhum dos dois cai".

**Architecture:** The production page's header, colour tokens, base CSS and theme toggle move into shared partials that both `build_report.py` and a new `build_analyses.py` expand at build time. Each piece is a folder (`analyses/<slug>/piece.pt.md` + `data.json`); the builder validates it, fills `{placeholders}` from the counts, renders a small Markdown subset and a `pair_matrix` chart, and writes the page plus a per-piece `og.png`. `build_site.sh` runs the builder; `deploy_s3.sh` also invalidates `/analise/*`.

**Tech Stack:** Python 3 standard library for all build code (host-runnable), pandas only in `relegation_pairs.py` (container), pytest in the container, `rsvg-convert` on the host for PNG cards, S3 + CloudFront.

**Spec:** `docs/superpowers/specs/2026-09-15-analise-pages-design.md`

## Global Constraints

- Build code (`build_report.py`, `build_analyses.py`, `markdown_lite.py`, `analysis_piece.py`, `charts.py`, `og_card.py`) imports nothing outside the standard library and this package.
- Portuguese only for analysis pages; no `/en/analise/`. The English header keeps "Analysis · soon".
- **Never render a simulation count** (the total of `counts`, or any per-cell count) on any page or card. Percentages only.
- Percentages round with the site rule: `<1%` below 0.5, `>99%` at 99.5 and above, else whole percent (`og_card.pct`).
- Every page starts with `<!doctype html>` and has `<meta name="viewport" content="width=device-width, initial-scale=1">`; the GA4 tag goes after the preamble (`build_report._with_ga4`).
- Tests run in the container **as their own step**; read the result before any commit, merge or deploy. Never pipe pytest into `tail` inside an `&&` chain.
- Identical pt/en string values must be added to the allowlist in `tests/test_report_strings.py`; pt and en key sets stay identical.
- Work in a git worktree (another session commits on `fix/local-day-cutoff`). Run tests from a worktree with the existing image:
  `docker run --rm -v "$PWD/src:/src" -v "$PWD/tests:/tests" -w /src --entrypoint "" brasileirao-simulator-app python -m pytest -q /tests/<file>`
- No simulation runs in this plan (the first piece's counts are transcribed). No deploy without the user's go.
- End commit messages with:
  `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01NdopobJg3w6UVXsPfadHgy`

## File Structure

| Path | Responsibility |
|---|---|
| `src/brasileirao_simulator/entrypoints/report/partials/head.html` | doctype, `<html lang>`, charset, viewport |
| `…/report/partials/fonts.html` | Google Fonts preconnect + stylesheet |
| `…/report/partials/tokens.css` | colour/spacing/type tokens, light and dark |
| `…/report/partials/base.css` | box-sizing, `[hidden]`, body, `.wrap`, links, code |
| `…/report/partials/bar.css` | green bar, pages menu, theme button, section row styles |
| `…/report/partials/bar-phone.css` | the bar's phone rules (inside `@media (max-width:720px)`) |
| `…/report/partials/header-top.html` | the bar's top row: brand, pages menu, model picker, theme button |
| `…/report/partials/theme.js` | theme toggle, saved theme, `themechange` event |
| `…/report/build_report.py` | gains `expand_includes`, `header_params` |
| `…/report/template_db.html` | uses the partials |
| `…/report/markdown_lite.py` | Markdown subset → HTML |
| `…/report/og_card.py` | shared card primitives (sizes, colours, `pct`, `br_date`, `esc`, `display_names`, `rasterise`) + `analysis_card_svg` |
| `…/report/build_og_card.py` | main card, now importing `og_card` |
| `…/report/analysis_piece.py` | load/validate a piece, shares, placeholders, fill |
| `…/report/charts.py` | chart renderers (`pair_matrix`) |
| `…/report/template_analysis.html` | one piece page |
| `…/report/template_analysis_index.html` | the index page |
| `…/report/build_analyses.py` | builds `/analise/` and every piece |
| `…/entrypoints/relegation_pairs.py` | gains `round_as_of`, `data_document`, `--out` |
| `analyses/gremio-inter-rebaixamento/{piece.pt.md,data.json}` | first piece |
| `scripts/build_site.sh`, `scripts/deploy_s3.sh` | build and invalidate analyses |
| `tests/test_site_partials.py`, `test_markdown_lite.py`, `test_og_card.py`, `test_analysis_piece.py`, `test_analysis_charts.py`, `test_build_analyses.py`, `test_relegation_pairs.py` | tests |

---

### Task 1: Extract shared partials without changing a byte

**Files:**
- Create: `src/brasileirao_simulator/entrypoints/report/partials/{head.html,fonts.html,tokens.css,base.css,bar.css,bar-phone.css,header-top.html}`
- Modify: `src/brasileirao_simulator/entrypoints/report/build_report.py` (add `PARTIALS_DIR`, `expand_includes`; call it in `build`)
- Modify: `src/brasileirao_simulator/entrypoints/report/template_db.html`
- Test: `tests/test_site_partials.py`

**Interfaces:**
- Produces: `build_report.PARTIALS_DIR: Path`; `build_report.expand_includes(template: str, partials_dir: Path = PARTIALS_DIR, params: Optional[dict] = None) -> str` — replaces `{{> name}}` with the verbatim contents of `partials_dir/name` (recursively), then replaces `{{@key}}` with `params.get(key, "")`; raises `FileNotFoundError` naming the partial when missing.

- [ ] **Step 1: Capture the current production pages (the byte gate)**

From the worktree root:

```bash
mkdir -p /tmp/claude-501/partials-gate
for l in pt en; do
  PYTHONPATH=src python3 src/brasileirao_simulator/entrypoints/report/build_report.py \
    --version db --lang $l --seasons current --out /tmp/claude-501/partials-gate/before.$l.html
done
```

Expected: two `wrote … (0.45 MB)` lines.

- [ ] **Step 2: Write the failing tests**

`tests/test_site_partials.py`:

```python
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
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `docker run --rm -v "$PWD/src:/src" -v "$PWD/tests:/tests" -w /src --entrypoint "" brasileirao-simulator-app python -m pytest -q /tests/test_site_partials.py`
Expected: FAIL — `AttributeError: module … has no attribute 'expand_includes'`.

- [ ] **Step 4: Add `expand_includes` to `build_report.py`**

After the `TEMPLATES = {…}` block add:

```python
PARTIALS_DIR = REPORT_DIR / "partials"
_INCLUDE_RE = re.compile(r"\{\{> ([\w.-]+)\}\}")
_PARAM_RE = re.compile(r"\{\{@(\w+)\}\}")


def expand_includes(template: str, partials_dir: Path = PARTIALS_DIR, params: Optional[dict] = None) -> str:
    """Replace `{{> name}}` with the verbatim contents of `partials_dir/name`
    (partials may include partials), then `{{@key}}` with `params[key]`, or
    nothing when the key is absent.

    Runs before `render_strings`, so a partial's `{{key}}` tokens are filled
    like the template's own. Verbatim matters: the main page must build to the
    same bytes whether its header lives inline or in a partial.
    """
    params = params or {}

    def include(match: "re.Match[str]") -> str:
        path = partials_dir / match.group(1)
        if not path.is_file():
            raise FileNotFoundError(f"template includes missing partial {match.group(1)!r} ({path})")
        return expand_includes(path.read_text(encoding="utf-8"), partials_dir, params)

    expanded = _INCLUDE_RE.sub(include, template)
    return _PARAM_RE.sub(lambda m: params.get(m.group(1), ""), expanded)
```

In `build`, change the template read to:

```python
    with open(report_dir / TEMPLATES[version]) as f:
        template = f.read()

    template = expand_includes(template, report_dir / "partials")
    template = render_strings(template, strings)
```

- [ ] **Step 5: Extract the partials from `template_db.html` by exact text**

Run this one-off script from the worktree root (it asserts every anchor exists exactly once, writes each partial with the exact text removed, and leaves `{{> name}}` in its place):

```bash
python3 - <<'EOF'
from pathlib import Path
R = Path("src/brasileirao_simulator/entrypoints/report")
t = (R / "template_db.html").read_text(encoding="utf-8")
(R / "partials").mkdir(exist_ok=True)

def cut(name, start, end, include_end=False):
    global t
    i = t.index(start); assert t.count(start) == 1, start
    j = t.index(end, i) + (len(end) if include_end else 0)
    (R / "partials" / name).write_text(t[i:j], encoding="utf-8")
    t = t[:i] + "{{> " + name + "}}" + t[j:]

cut("head.html", "<!doctype html>", "<title>")
cut("fonts.html", '<link rel="preconnect" href="https://fonts.googleapis.com">', "<style>")
cut("tokens.css", ":root{\n  --paper:", "*{box-sizing:border-box}")
cut("base.css", "*{box-sizing:border-box}", "/* ---- slim bar")
cut("bar.css", "/* ---- slim bar", "/* ---- masthead ---- */")
cut("bar-phone.css", "  .bar .name{display:none}\n", "}\n</style>")
cut("header-top.html", '  <div class="wrap">\n    <div class="name">', '  <div class="sections-row">')
(R / "template_db.html").write_text(t, encoding="utf-8")
print("ok")
EOF
```

Expected: `ok`, seven files in `partials/`.

- [ ] **Step 6: Verify the byte gate**

```bash
for l in pt en; do
  PYTHONPATH=src python3 src/brasileirao_simulator/entrypoints/report/build_report.py \
    --version db --lang $l --seasons current --out /tmp/claude-501/partials-gate/after.$l.html
  cmp /tmp/claude-501/partials-gate/before.$l.html /tmp/claude-501/partials-gate/after.$l.html && echo "$l identical"
done
```

Expected: `pt identical`, `en identical`. If `cmp` reports a difference, the extraction lost or added a character: fix the partial boundary, do not continue.

- [ ] **Step 7: Run the new tests and the report tests**

Run: `docker run --rm -v "$PWD/src:/src" -v "$PWD/tests:/tests" -w /src --entrypoint "" brasileirao-simulator-app python -m pytest -q /tests/test_site_partials.py /tests/test_report_build.py /tests/test_report_strings.py`
Expected: `16 passed`.

- [ ] **Step 8: Commit**

```bash
git add src/brasileirao_simulator/entrypoints/report/partials src/brasileirao_simulator/entrypoints/report/build_report.py src/brasileirao_simulator/entrypoints/report/template_db.html tests/test_site_partials.py
git commit -m "refactor(site): the production page's head, tokens, bar and header move into shared partials

Built PT and EN pages are byte-identical before and after.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NdopobJg3w6UVXsPfadHgy"
```

---

### Task 2: A header that works on any page

**Files:**
- Modify: `…/report/partials/header-top.html`, `…/report/partials/bar.css`
- Create: `…/report/partials/theme.js`
- Modify: `…/report/build_report.py` (add `header_params`; pass params in `build`)
- Modify: `…/report/template_db.html` (theme block, menu JS, model picker JS)
- Modify: `…/report/strings.pt.json`, `…/report/strings.en.json` (add `nav.season`)
- Modify: `tests/test_report_strings.py` (allowlist `nav.season`)
- Test: `tests/test_site_partials.py`

**Interfaces:**
- Consumes: `build_report.expand_includes` (Task 1).
- Produces: `build_report.header_params(page: str) -> dict` with keys `current_2026`, `current_analise` (value `' aria-current="page"'` for the active page, `""` otherwise); `page` is `"2026"` or `"analise"`. Partial `theme.js` defines `labelThemeButton()` and dispatches `document` event `"themechange"` after a toggle.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_site_partials.py`:

```python
def test_header_params_mark_only_the_active_page():
    assert build_report.header_params("2026") == {"current_2026": ' aria-current="page"', "current_analise": ""}
    assert build_report.header_params("analise") == {"current_2026": "", "current_analise": ' aria-current="page"'}


def test_the_main_page_header_links_to_analise_and_home(tmp_path):
    out = tmp_path / "db.pt.html"
    build_report.build(out, lang="pt", version="db", seasons=["2026"])
    html = out.read_text()
    assert '<a class="page" href="/" aria-current="page">2026</a>' in html
    assert '<a class="page analise-link" href="/analise/">' in html
    assert 'class="page soon analise-soon"' in html  # the English build shows this one
    assert 'id="page-current"' not in html


def test_the_theme_toggle_is_shared_and_the_main_page_listens(tmp_path):
    out = tmp_path / "db.pt.html"
    build_report.build(out, lang="pt", version="db", seasons=["2026"])
    html = out.read_text()
    assert 'new Event("themechange")' in html
    assert 'addEventListener("themechange"' in html
    assert '<label class="model-pick" for="model" hidden>' in html
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `docker run --rm -v "$PWD/src:/src" -v "$PWD/tests:/tests" -w /src --entrypoint "" brasileirao-simulator-app python -m pytest -q /tests/test_site_partials.py`
Expected: FAIL on the three new tests (`header_params` missing; markup not found).

- [ ] **Step 3: Add `header_params` and pass it in `build`**

In `build_report.py`, below `expand_includes`:

```python
def header_params(page: str) -> dict:
    """Which pages-menu entry is the current page: `"2026"` (the forecast) or
    `"analise"`. The header partial reads these as `{{@current_2026}}` and
    `{{@current_analise}}`."""
    current = ' aria-current="page"'
    return {"current_2026": current if page == "2026" else "",
            "current_analise": current if page == "analise" else ""}
```

In `build`: `template = expand_includes(template, report_dir / "partials", header_params("2026"))`.

- [ ] **Step 4: Rewrite the pages menu in `partials/header-top.html`**

Replace the `<nav class="pages" …>` block (the `page-current` link and the three `soon` spans) with:

```html
    <nav class="pages" aria-label="{{nav.pages_label}}">
      <a class="page" href="/"{{@current_2026}}>{{nav.season}}</a>
      <span class="page soon" aria-hidden="true">{{nav.seasons}}<small>{{nav.soon}}</small></span>
      <a class="page analise-link" href="/analise/"{{@current_analise}}>{{nav.analysis}}</a>
      <span class="page soon analise-soon" aria-hidden="true">{{nav.analysis}}<small>{{nav.soon}}</small></span>
      <span class="page soon" aria-hidden="true">{{nav.about}}<small>{{nav.soon}}</small></span>
    </nav>
```

Replace the model picker opening tag `<label class="model-pick" for="model">` with `<label class="model-pick" for="model" hidden>`.

- [ ] **Step 5: Language rule for the Análise entry in `partials/bar.css`**

Append at the end of `bar.css`:

```css
/* Análise exists in Portuguese only: the Portuguese header links to it, the
   English header keeps it "soon". */
html[lang="en"] .page.analise-link{display:none}
html:not([lang="en"]) .page.analise-soon{display:none}
```

- [ ] **Step 6: Move the theme toggle into `partials/theme.js`**

Create `partials/theme.js`:

```js
/* ---------- theme ---------- */

document.getElementById("theme").addEventListener("click", function(){
  var root = document.documentElement;
  var dark = root.getAttribute("data-theme") === "dark";
  root.setAttribute("data-theme", dark ? "light" : "dark");
  try { localStorage.setItem("theme", dark ? "light" : "dark"); } catch(e){}
  labelThemeButton();
  // Pages that draw with theme colours redraw on this event.
  document.dispatchEvent(new Event("themechange"));
});
try { var saved = localStorage.getItem("theme"); if (saved) document.documentElement.setAttribute("data-theme", saved); } catch(e){}
function labelThemeButton(){
  var btn = document.getElementById("theme");
  var dark = document.documentElement.getAttribute("data-theme") === "dark";
  btn.setAttribute("aria-label", btn.getAttribute(dark ? "data-to-light" : "data-to-dark"));
}
labelThemeButton();
```

In `template_db.html`, replace the whole block from `/* ---------- theme ---------- */` through the line `labelThemeButton();` with:

```js
{{> theme.js}}
document.addEventListener("themechange", function(){ render(); renderHeat(); renderPoints(); renderTable(); });
```

- [ ] **Step 7: Update the page JS that referenced removed markup**

In `template_db.html`:
- In `renderMenu`, delete the line `  document.getElementById("page-current").textContent = latestSeason();`.
- Replace the model-picker hiding block

```js
if (MODEL_ORDER.length < 2) {
  var pickLabel = document.querySelector(".model-pick");
  if (pickLabel) pickLabel.hidden = true;
}
```

with

```js
/* The picker ships hidden; it appears only when there is a choice to make. */
if (MODEL_ORDER.length > 1) {
  var pickLabel = document.querySelector(".model-pick");
  if (pickLabel) pickLabel.hidden = false;
}
```

(Keep the comment above it that explains why a single model hides the control.)

- [ ] **Step 8: Strings**

Add after `"nav.about"` in `strings.pt.json`: `"nav.season": "2026",` and in `strings.en.json`: `"nav.season": "2026",`.
In `tests/test_report_strings.py` allowlist add: `"nav.season",  # the season shown in the menu - a number, same in both`.

- [ ] **Step 9: Run the tests**

Run: `docker run --rm -v "$PWD/src:/src" -v "$PWD/tests:/tests" -w /src --entrypoint "" brasileirao-simulator-app python -m pytest -q /tests/test_site_partials.py /tests/test_report_build.py /tests/test_report_strings.py`
Expected: `19 passed`.

- [ ] **Step 10: Browser check of the main page**

Build PT and EN (`build_report.py --version db --lang {pt,en} --seasons current`), serve the report folder with `python3 -m http.server`, and in Chrome confirm: PT header shows `ANÁLISE` as a link; EN shows `ANALYSIS soon`; the sun/moon toggle still redraws the charts; the model picker stays hidden; clicking `2026` goes to `/`.

- [ ] **Step 11: Commit**

```bash
git add src/brasileirao_simulator/entrypoints/report tests/test_site_partials.py tests/test_report_strings.py
git commit -m "feat(site): one header for every page - Análise links in Portuguese, the theme toggle is shared

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NdopobJg3w6UVXsPfadHgy"
```

---

### Task 3: A Markdown subset

**Files:**
- Create: `src/brasileirao_simulator/entrypoints/report/markdown_lite.py`
- Test: `tests/test_markdown_lite.py`

**Interfaces:**
- Produces: `markdown_lite.render(text: str) -> str`. Blocks split on blank lines. `# `, `## `, `### ` → `<h2>`, `<h3>`, `<h4>` (the page owns `<h1>`). A block whose every line starts with `- ` → `<ul><li>…</li></ul>`. Anything else → `<p>` with lines joined by a space. Inline, after HTML-escaping `&`, `<`, `>`: `[text](url)` for `http://`, `https://` or `/` URLs, `**bold**` → `<strong>`, `*italic*` → `<em>`. Blocks joined with `\n`.

- [ ] **Step 1: Write the failing tests**

`tests/test_markdown_lite.py`:

```python
from brasileirao_simulator.entrypoints.report.markdown_lite import render


def test_paragraphs_join_lines_and_split_on_blank_lines():
    assert render("uma\nlinha\n\noutra") == "<p>uma linha</p>\n<p>outra</p>"


def test_headings_start_below_the_page_title():
    assert render("# A\n\n## B\n\n### C") == "<h2>A</h2>\n<h3>B</h3>\n<h4>C</h4>"


def test_lists():
    assert render("- um\n- dois") == "<ul><li>um</li><li>dois</li></ul>"


def test_inline_bold_italic_and_links():
    assert render("**forte** e *leve* e [aqui](/analise/)") == \
        '<p><strong>forte</strong> e <em>leve</em> e <a href="/analise/">aqui</a></p>'


def test_html_is_escaped_and_unsafe_links_stay_text():
    assert render("a < b & c") == "<p>a &lt; b &amp; c</p>"
    assert render("[x](javascript:alert(1))") == "<p>[x](javascript:alert(1))</p>"


def test_percent_signs_and_quotes_pass_through():
    assert render('<1% e "aspas"') == '<p>&lt;1% e "aspas"</p>'
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `docker run --rm -v "$PWD/src:/src" -v "$PWD/tests:/tests" -w /src --entrypoint "" brasileirao-simulator-app python -m pytest -q /tests/test_markdown_lite.py`
Expected: FAIL — `ModuleNotFoundError: … markdown_lite`.

- [ ] **Step 3: Implement**

`src/brasileirao_simulator/entrypoints/report/markdown_lite.py`:

```python
"""The Markdown an analysis piece is written in - a deliberate subset.

Paragraphs, three heading levels, bullet lists, bold, italic and links. That is
all a piece needs, and a subset small enough to test completely beats a
dependency the host build would have to install. Text is HTML-escaped before
any markup is added, so a piece can never inject HTML; links are kept only for
http(s) and site-relative URLs.
"""

import re

_LINK_RE = re.compile(r"\[([^\]]+)\]\(((?:https?://|/)[^)\s]*)\)")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_RE = re.compile(r"\*(.+?)\*")
_HEADING_TAGS = {"#": "h2", "##": "h3", "###": "h4"}


def render(text: str) -> str:
    blocks = [b for b in re.split(r"\n\s*\n", text.strip()) if b.strip()]
    return "\n".join(_block(b.strip()) for b in blocks)


def _block(block: str) -> str:
    marker, _, rest = block.partition(" ")
    if marker in _HEADING_TAGS and "\n" not in block:
        tag = _HEADING_TAGS[marker]
        return f"<{tag}>{_inline(rest)}</{tag}>"
    lines = block.splitlines()
    if all(line.startswith("- ") for line in lines):
        return "<ul>" + "".join(f"<li>{_inline(line[2:])}</li>" for line in lines) + "</ul>"
    return f"<p>{_inline(' '.join(line.strip() for line in lines))}</p>"


def _inline(text: str) -> str:
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = _LINK_RE.sub(r'<a href="\2">\1</a>', text)
    text = _BOLD_RE.sub(r"<strong>\1</strong>", text)
    return _ITALIC_RE.sub(r"<em>\1</em>", text)
```

- [ ] **Step 4: Run the tests**

Run: same command as Step 2. Expected: `6 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/brasileirao_simulator/entrypoints/report/markdown_lite.py tests/test_markdown_lite.py
git commit -m "feat(analise): a tested Markdown subset for analysis pieces

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NdopobJg3w6UVXsPfadHgy"
```

---

### Task 4: Shared card primitives and the analysis card

**Files:**
- Create: `src/brasileirao_simulator/entrypoints/report/og_card.py`
- Modify: `src/brasileirao_simulator/entrypoints/report/build_og_card.py` (import from `og_card`, delete the moved definitions)
- Test: `tests/test_og_card.py`

**Interfaces:**
- Produces, in `og_card`: `W = 1200`, `H = 630`, `INK`, `PAPER`, `CAMPO`, `Z4`, `MUTED` (same values as today); `esc(text: str) -> str`; `pct(value: float) -> str`; `br_date(iso: str) -> str` (`"2026-09-14"` → `"14 set 2026"`); `display_names() -> dict` (canonical → display); `rasterise(source: Path, out: Path) -> None`; `analysis_card_svg(title: str, stamp: str, rows: list[tuple[str, str]], crests: list[str]) -> str` where `rows` is `(label, percent text)` and `crests` are `data:` URIs (0–2).

- [ ] **Step 1: Capture the current main card SVG (byte gate for the refactor)**

```bash
PYTHONPATH=src python3 - <<'EOF'
from pathlib import Path
from brasileirao_simulator.entrypoints.report import build_og_card as c
state = c.latest(c.EXPORTS / "forecast_dataset.elo.json")
Path("/tmp/claude-501/og-before.svg").write_text(c.svg(state), encoding="utf-8")
print("ok")
EOF
```

- [ ] **Step 2: Write the failing tests**

`tests/test_og_card.py`:

```python
from brasileirao_simulator.entrypoints.report import og_card


def test_pct_follows_the_site_rule():
    assert [og_card.pct(v) for v in (0.2, 0.5, 15.06, 99.4, 99.5)] == ["<1%", "1%", "15%", "99%", ">99%"]


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
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `docker run --rm -v "$PWD/src:/src" -v "$PWD/tests:/tests" -w /src --entrypoint "" brasileirao-simulator-app python -m pytest -q /tests/test_og_card.py`
Expected: FAIL — `ImportError` for `og_card`.

- [ ] **Step 4: Create `og_card.py`**

Move, verbatim, from `build_og_card.py` into `og_card.py`: the constants `REPORT_DIR`, `DATASETS`, `W`, `H`, `INK`, `PAPER`, `CAMPO`, `Z4`, `MUTED`, and the functions `display_names`, `esc`, `pct`, `br_date`, `rasterise` (with the `csv`, `json`, `subprocess`, `Path` imports they use). Give the module this docstring:

```python
"""What every link-preview card shares: size, palette, the site's percentage
and date formats, club display names, and SVG-to-PNG rasterising.

`build_og_card.py` draws the forecast card; `build_analyses.py` draws one card
per analysis piece. Standard library only, host-runnable.
"""
```

Then append:

```python
def _title_lines(title: str, width: int = 30, most: int = 3) -> list[str]:
    """Greedy word wrap; a title longer than `most` lines ends in an ellipsis."""
    lines, line = [], ""
    for word in title.split():
        if line and len(line) + 1 + len(word) > width:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    lines.append(line)
    if len(lines) > most:
        lines = lines[:most]
        lines[-1] = lines[-1].rstrip(".,:;") + "…"
    return lines


def analysis_card_svg(title: str, stamp: str, rows: list, crests: list) -> str:
    """The card for one analysis piece: brand and date, the crests of the clubs
    it is about, the headline, and up to four labelled percentages."""
    crest_svg = "".join(
        f'\n  <image x="{1120 - 110 * (len(crests) - k)}" y="40" width="96" height="96" href="{uri}"/>'
        for k, uri in enumerate(crests[:2])
    )
    title_svg = "".join(
        f'\n  <text x="80" y="{230 + 62 * k}" font-family="Helvetica,Arial,sans-serif" font-size="52"'
        f' font-weight="700" fill="{INK}">{esc(line)}</text>'
        for k, line in enumerate(_title_lines(title))
    )
    row_svg = "".join(
        f'\n  <text x="{80 + 265 * k}" y="480" font-family="Helvetica,Arial,sans-serif" font-size="24"'
        f' fill="{MUTED}">{esc(label)}</text>'
        f'\n  <text x="{80 + 265 * k}" y="540" font-family="Helvetica,Arial,sans-serif" font-size="56"'
        f' font-weight="700" fill="{Z4 if k else CAMPO}">{esc(value)}</text>'
        for k, (label, value) in enumerate(rows[:4])
    )
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">
  <rect width="{W}" height="{H}" fill="{PAPER}"/>
  <rect x="0" y="0" width="{W}" height="14" fill="{CAMPO}"/>
  <text x="80" y="90" font-family="Helvetica,Arial,sans-serif" font-size="30"
        font-weight="700" fill="{CAMPO}" letter-spacing="3">DATA BRASILEIRÃO · ANÁLISE</text>
  <text x="80" y="130" font-family="Helvetica,Arial,sans-serif" font-size="24" fill="{MUTED}">{esc(stamp)}</text>{crest_svg}{title_svg}{row_svg}
  <text x="80" y="600" font-family="Helvetica,Arial,sans-serif" font-size="22"
        fill="{MUTED}">databrasileirao.com.br/analise</text>
</svg>"""
```

In `build_og_card.py`, delete the moved definitions and add below the other imports:

```python
from brasileirao_simulator.entrypoints.report.og_card import (  # noqa: F401 - re-used names
    CAMPO, DATASETS, H, INK, MUTED, PAPER, REPORT_DIR, W, Z4, br_date, display_names, esc, pct, rasterise,
)
```

Keep `EXPORTS = REPORT_DIR.parents[2] / "files" / "exports"` in `build_og_card.py`. Because `build_og_card.py` is run as a script from `build_site.sh` (`python3 src/…/build_og_card.py`), change that line in `scripts/build_site.sh` to `PYTHONPATH=src python3 src/brasileirao_simulator/entrypoints/report/build_og_card.py --out "$OUT/og.png"` so the package import resolves.

- [ ] **Step 5: Verify the main card is unchanged**

```bash
PYTHONPATH=src python3 - <<'EOF'
from pathlib import Path
from brasileirao_simulator.entrypoints.report import build_og_card as c
state = c.latest(c.EXPORTS / "forecast_dataset.elo.json")
assert c.svg(state) == Path("/tmp/claude-501/og-before.svg").read_text(encoding="utf-8")
print("main card identical")
EOF
```

Expected: `main card identical`.

- [ ] **Step 6: Run the tests**

Run: `docker run --rm -v "$PWD/src:/src" -v "$PWD/tests:/tests" -w /src --entrypoint "" brasileirao-simulator-app python -m pytest -q /tests/test_og_card.py`
Expected: `3 passed`.

- [ ] **Step 7: Commit**

```bash
git add src/brasileirao_simulator/entrypoints/report/og_card.py src/brasileirao_simulator/entrypoints/report/build_og_card.py scripts/build_site.sh tests/test_og_card.py
git commit -m "refactor(site): card primitives shared in og_card, plus the analysis card

The forecast card's SVG is identical before and after.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NdopobJg3w6UVXsPfadHgy"
```

---

### Task 5: Loading, validating and filling a piece

**Files:**
- Create: `src/brasileirao_simulator/entrypoints/report/analysis_piece.py`
- Test: `tests/test_analysis_piece.py`

**Interfaces:**
- Consumes: `og_card.pct`, `og_card.br_date` (Task 4).
- Produces:
  - `class PieceError(ValueError)`
  - `@dataclass(frozen=True) class Piece: slug: str; title: str; summary: str; date: str; charts: tuple; body: str; data: dict` — `title`, `summary`, `body` still hold `{placeholders}`.
  - `KNOWN_QUESTIONS = frozenset({"relegation_pairs"})`, `KNOWN_CHARTS = frozenset({"pair_matrix"})`
  - `load_piece(folder: Path) -> Piece` (validates everything in the spec's Validation list)
  - `shares(counts: dict) -> dict` — percent floats for `neither, only_a, only_b, both, either, a_down, b_down, a_safe, b_safe`
  - `placeholders(piece: Piece, display: dict) -> dict` — `"<share>_pct"` strings via `pct`, plus `club_a`, `club_b` (display names), `date` (`br_date`), `round` (str)
  - `fill(text: str, values: dict, piece_slug: str) -> str` — replaces `{name}`; unknown name raises `PieceError` naming slug and placeholder

- [ ] **Step 1: Write the failing tests**

`tests/test_analysis_piece.py`:

```python
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
])
def test_invalid_pieces_fail_with_the_cause(tmp_path, change, message):
    piece, data = change(PIECE, DATA)
    with pytest.raises(ap.PieceError, match=message):
        ap.load_piece(write(tmp_path, piece, data))


def test_an_unknown_placeholder_names_itself(tmp_path):
    piece = ap.load_piece(write(tmp_path))
    with pytest.raises(ap.PieceError, match="nope"):
        ap.fill("{nope}", ap.placeholders(piece, {}), piece.slug)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `docker run --rm -v "$PWD/src:/src" -v "$PWD/tests:/tests" -w /src --entrypoint "" brasileirao-simulator-app python -m pytest -q /tests/test_analysis_piece.py`
Expected: FAIL — `ModuleNotFoundError: … analysis_piece`.

- [ ] **Step 3: Implement**

`src/brasileirao_simulator/entrypoints/report/analysis_piece.py`:

```python
"""One analysis piece: a folder holding `piece.pt.md` and `data.json`.

The text is written by a person; the numbers are written by an analysis script
into `data.json`, and reach the text only through `{placeholders}`, so a number
on the page can always be traced to the run that produced it. Everything that
could make a piece wrong fails loudly here, before a page is written.

Only percentages are ever exposed as placeholders: how many seasons were
simulated is internal.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path

from brasileirao_simulator.entrypoints.report.og_card import br_date, pct

KNOWN_QUESTIONS = frozenset({"relegation_pairs"})
KNOWN_CHARTS = frozenset({"pair_matrix"})
_COUNT_KEYS = ("neither", "only_a", "only_b", "both")
_PLACEHOLDER_RE = re.compile(r"\{([a-z_]+)\}")


class PieceError(ValueError):
    """A piece that must not be published as it stands."""


@dataclass(frozen=True)
class Piece:
    slug: str
    title: str
    summary: str
    date: str
    charts: tuple
    body: str
    data: dict


def load_piece(folder: Path) -> Piece:
    folder = Path(folder)
    text = (folder / "piece.pt.md").read_text(encoding="utf-8")
    meta, body = _front_matter(text, folder.name)
    data_path = folder / "data.json"
    if not data_path.is_file():
        raise PieceError(f"{folder.name}: missing data.json")
    try:
        data = json.loads(data_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise PieceError(f"{folder.name}: data.json is not valid JSON ({e})") from e

    piece = Piece(slug=meta.get("slug", ""), title=meta.get("title", ""), summary=meta.get("summary", ""),
                  date=meta.get("date", ""), charts=tuple(meta.get("charts", ())), body=body, data=data)
    _validate(piece, folder.name)
    return piece


def _front_matter(text: str, name: str) -> tuple:
    """The fixed key set between the leading `---` lines: strings (optionally
    double-quoted) and one bracketed list, `charts: [a, b]`."""
    if not text.startswith("---\n"):
        raise PieceError(f"{name}: piece.pt.md must open with a --- front-matter block")
    head, sep, body = text[4:].partition("\n---\n")
    if not sep:
        raise PieceError(f"{name}: front matter is not closed by ---")
    meta = {}
    for line in head.splitlines():
        if not line.strip():
            continue
        key, _, value = line.partition(":")
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            meta[key.strip()] = [v.strip() for v in value[1:-1].split(",") if v.strip()]
        else:
            meta[key.strip()] = value[1:-1] if len(value) > 1 and value[0] == value[-1] == '"' else value
    return meta, body


def _validate(piece: Piece, folder_name: str) -> None:
    name = folder_name
    if piece.slug != folder_name:
        raise PieceError(f"{name}: slug {piece.slug!r} differs from the folder name")
    for field in ("title", "summary", "date"):
        if not getattr(piece, field):
            raise PieceError(f"{name}: front matter needs {field}")
    unknown_charts = [c for c in piece.charts if c not in KNOWN_CHARTS]
    if unknown_charts:
        raise PieceError(f"{name}: no renderer for chart(s) {unknown_charts}")
    data = piece.data
    if data.get("question") not in KNOWN_QUESTIONS:
        raise PieceError(f"{name}: unknown question {data.get('question')!r}")
    if data.get("as_of") != piece.date:
        raise PieceError(f"{name}: front-matter date {piece.date} differs from data.json as_of {data.get('as_of')}")
    counts = data.get("counts", {})
    values = [counts.get(k) for k in _COUNT_KEYS]
    if not all(isinstance(v, int) and v >= 0 for v in values) or sum(values) <= 0:
        raise PieceError(f"{name}: counts must be four non-negative integers with a positive total")
    if len(data.get("clubs", [])) != 2:
        raise PieceError(f"{name}: data.json clubs must name two clubs")


def shares(counts: dict) -> dict:
    total = sum(counts[k] for k in _COUNT_KEYS)
    s = {k: 100 * counts[k] / total for k in _COUNT_KEYS}
    s["either"] = 100 - s["neither"]
    s["a_down"] = s["only_a"] + s["both"]
    s["b_down"] = s["only_b"] + s["both"]
    s["a_safe"] = 100 - s["a_down"]
    s["b_safe"] = 100 - s["b_down"]
    return s


def placeholders(piece: Piece, display: dict) -> dict:
    values = {f"{key}_pct": pct(value) for key, value in shares(piece.data["counts"]).items()}
    a, b = piece.data["clubs"]
    values.update(club_a=display.get(a, a), club_b=display.get(b, b),
                  date=br_date(piece.data["as_of"]), round=str(piece.data["round"]))
    return values


def fill(text: str, values: dict, piece_slug: str) -> str:
    def value(match: "re.Match[str]") -> str:
        key = match.group(1)
        if key not in values:
            raise PieceError(f"{piece_slug}: no value for placeholder {{{key}}}")
        return values[key]
    return _PLACEHOLDER_RE.sub(value, text)
```

- [ ] **Step 4: Run the tests**

Run: same command as Step 2. Expected: `11 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/brasileirao_simulator/entrypoints/report/analysis_piece.py tests/test_analysis_piece.py
git commit -m "feat(analise): load, validate and fill an analysis piece

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NdopobJg3w6UVXsPfadHgy"
```

---

### Task 6: The `pair_matrix` chart

**Files:**
- Create: `src/brasileirao_simulator/entrypoints/report/charts.py`
- Modify: `strings.pt.json`, `strings.en.json` (matrix labels), `tests/test_report_strings.py` (allowlist)
- Test: `tests/test_analysis_charts.py`

**Interfaces:**
- Consumes: `analysis_piece.shares` (Task 5), `og_card.pct`, `og_card.esc` (Task 4).
- Produces: `charts.pair_matrix(counts: dict, club_a: dict, club_b: dict, labels: dict) -> str`. `club_a`/`club_b` are `{"name": str, "crest": Optional[str]}`; `labels` has `down`, `safe`, `total`, `caption`. `charts.RENDERERS = {"pair_matrix": pair_matrix}`.

- [ ] **Step 1: Write the failing tests**

`tests/test_analysis_charts.py`:

```python
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
        assert n not in h


def test_registry():
    assert charts.RENDERERS["pair_matrix"] is charts.pair_matrix
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `docker run --rm -v "$PWD/src:/src" -v "$PWD/tests:/tests" -w /src --entrypoint "" brasileirao-simulator-app python -m pytest -q /tests/test_analysis_charts.py`
Expected: FAIL — `ModuleNotFoundError: … charts`.

- [ ] **Step 3: Implement**

`src/brasileirao_simulator/entrypoints/report/charts.py`:

```python
"""Chart renderers an analysis piece can list in its front matter.

Each renderer returns self-contained HTML styled by template_analysis.html.
Cells show percentages only - never the counts behind them.
"""

from brasileirao_simulator.entrypoints.report.analysis_piece import shares
from brasileirao_simulator.entrypoints.report.og_card import esc, pct


def _club(club: dict, suffix: str) -> str:
    crest = f'<img class="crest" src="{club["crest"]}" alt="">' if club.get("crest") else ""
    return f'{crest}{esc(club["name"])} {esc(suffix)}'


def _cell(share: float, good: bool = False) -> str:
    # Shade by share on the site's bad (red) or good (green) scale.
    rgb = "var(--good-rgb)" if good else "var(--bad-rgb)"
    alpha = 0.06 + 0.6 * share / 100
    cls = "cell good" if good else "cell"
    return f'<td class="{cls}" style="background:rgba({rgb},{alpha:.3f})">{pct(share)}</td>'


def pair_matrix(counts: dict, club_a: dict, club_b: dict, labels: dict) -> str:
    """Two clubs, one outcome each (down / not down): the four joint cases,
    and each club's own chance in the totals."""
    s = shares(counts)
    return (
        f'<figure class="chart pair-matrix"><div class="scroller"><table>'
        f'<caption>{esc(labels["caption"])}</caption>'
        f'<thead><tr><th></th><th scope="col">{_club(club_b, labels["down"])}</th>'
        f'<th scope="col">{_club(club_b, labels["safe"])}</th><th scope="col">{esc(labels["total"])}</th></tr></thead>'
        f'<tbody>'
        f'<tr><th scope="row">{_club(club_a, labels["down"])}</th>{_cell(s["both"])}{_cell(s["only_a"])}'
        f'<td class="total">{pct(s["a_down"])}</td></tr>'
        f'<tr><th scope="row">{_club(club_a, labels["safe"])}</th>{_cell(s["only_b"])}{_cell(s["neither"], good=True)}'
        f'<td class="total">{pct(s["a_safe"])}</td></tr>'
        f'<tr class="total"><th scope="row">{esc(labels["total"])}</th><td>{pct(s["b_down"])}</td>'
        f'<td>{pct(s["b_safe"])}</td><td>100%</td></tr>'
        f'</tbody></table></div></figure>'
    )


RENDERERS = {"pair_matrix": pair_matrix}
```

- [ ] **Step 4: Strings for the analysis pages** (used from Task 7 on)

Add to `strings.pt.json` (after `"nav.season"`):

```json
  "analysis.index_title": "Análises",
  "analysis.index_intro": "Perguntas específicas respondidas com as simulações do Data Brasileirão.",
  "analysis.index_description": "Análises do Brasileirão com as simulações do Data Brasileirão.",
  "analysis.empty": "Nenhuma análise publicada ainda.",
  "analysis.kicker": "Análise · {date} · após a {round}ª rodada",
  "analysis.footer": "Números de {date}, após a {round}ª rodada. As chances de hoje estão na <a href=\"/\">página principal</a>.",
  "analysis.back": "← Todas as análises",
  "analysis.matrix_down": "cai",
  "analysis.matrix_safe": "não cai",
  "analysis.matrix_total": "Total",
  "analysis.matrix_caption": "Em quantos cenários simulados cada combinação aconteceu",
```

Add to `strings.en.json` (after `"nav.season"`):

```json
  "analysis.index_title": "Analyses",
  "analysis.index_intro": "Specific questions answered with the Data Brasileirão simulations.",
  "analysis.index_description": "Brasileirão analyses from the Data Brasileirão simulations.",
  "analysis.empty": "No analyses published yet.",
  "analysis.kicker": "Analysis · {date} · after round {round}",
  "analysis.footer": "Numbers from {date}, after round {round}. Today's chances are on the <a href=\"/en/\">main page</a>.",
  "analysis.back": "← All analyses",
  "analysis.matrix_down": "down",
  "analysis.matrix_safe": "stays up",
  "analysis.matrix_total": "Total",
  "analysis.matrix_caption": "Share of simulated scenarios in which each combination happened",
```

Allowlist in `tests/test_report_strings.py`: `"analysis.matrix_total",  # "Total" - the same word in both`.

- [ ] **Step 5: Run the tests**

Run: `docker run --rm -v "$PWD/src:/src" -v "$PWD/tests:/tests" -w /src --entrypoint "" brasileirao-simulator-app python -m pytest -q /tests/test_analysis_charts.py /tests/test_report_strings.py`
Expected: all pass (`4` chart tests plus the strings tests).

- [ ] **Step 6: Commit**

```bash
git add src/brasileirao_simulator/entrypoints/report/charts.py src/brasileirao_simulator/entrypoints/report/strings.pt.json src/brasileirao_simulator/entrypoints/report/strings.en.json tests/test_analysis_charts.py tests/test_report_strings.py
git commit -m "feat(analise): pair_matrix - two clubs, four joint outcomes, each club's total

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NdopobJg3w6UVXsPfadHgy"
```

---

### Task 7: Templates and `build_analyses.py`

**Files:**
- Create: `…/report/template_analysis.html`, `…/report/template_analysis_index.html`, `…/report/build_analyses.py`
- Test: `tests/test_build_analyses.py`

**Interfaces:**
- Consumes: `build_report.{expand_includes, header_params, render_strings, load_strings, _with_ga4, REPORT_DIR}`; `markdown_lite.render`; `analysis_piece.{load_piece, placeholders, fill, shares, PieceError}`; `charts.RENDERERS`; `og_card.{analysis_card_svg, display_names, rasterise, br_date, pct}`.
- Produces: `build_analyses.ANALYSES_DIR: Path` (repo-root `analyses/`), `build_analyses.SITE = "https://databrasileirao.com.br"`, `build_analyses.build(analyses_dir: Path, out_dir: Path, ga4: Optional[str] = None, cards: bool = True, logos: Optional[dict] = None, display: Optional[dict] = None) -> list[Path]` returning written HTML paths (index first).

- [ ] **Step 1: Write the failing tests**

`tests/test_build_analyses.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `docker run --rm -v "$PWD/src:/src" -v "$PWD/tests:/tests" -w /src --entrypoint "" brasileirao-simulator-app python -m pytest -q /tests/test_build_analyses.py`
Expected: FAIL — `ImportError` for `build_analyses`.

- [ ] **Step 3: Create `template_analysis.html`**

```html
{{> head.html}}<title>__TITLE__ · {{brand.name}}</title>
<meta property="og:type" content="article">
<meta property="og:site_name" content="{{brand.name}}">
<meta property="og:title" content="__TITLE__">
<meta property="og:description" content="__SUMMARY__">
<meta property="og:image" content="__OG_IMAGE__">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:url" content="__URL__">
<meta property="og:locale" content="{{share.locale}}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:image" content="__OG_IMAGE__">
<meta name="twitter:title" content="__TITLE__">
<meta name="twitter:description" content="__SUMMARY__">
<meta name="description" content="__SUMMARY__">
{{> fonts.html}}<style>
{{> tokens.css}}{{> base.css}}{{> bar.css}}
/* ---- analysis page ---- */
.piece{max-width:720px; padding-top:var(--sp-7)}
.kicker{font-size:var(--fs-300); font-weight:700; letter-spacing:.12em; text-transform:uppercase; color:var(--signal); margin:0 0 var(--sp-4)}
h1{font-size:var(--fs-700); font-weight:800; font-stretch:64%; line-height:1.05; margin:0; text-wrap:balance}
.summary{font-size:var(--fs-600); color:var(--ink-2); line-height:1.45; margin:var(--sp-4) 0 0}
.body p,.body li{font-size:1.0625rem; line-height:1.6}
.body h2,.body h3,.body h4{font-stretch:75%; margin:var(--sp-7) 0 var(--sp-3)}
.chart{margin:var(--sp-7) 0}
.scroller{overflow-x:auto}
.pair-matrix table{width:100%; border-collapse:collapse; font-size:var(--fs-400)}
.pair-matrix caption{caption-side:bottom; text-align:left; color:var(--ink-3); font-size:var(--fs-200); padding-top:var(--sp-3)}
.pair-matrix th{font-weight:600; text-align:left; padding:var(--sp-3) var(--sp-3); white-space:nowrap}
.pair-matrix thead th{font-size:var(--fs-300); border-bottom:1px solid var(--rule-strong)}
.pair-matrix td{font-family:var(--mono); text-align:center; padding:var(--sp-5) var(--sp-3); border:2px solid var(--paper)}
.pair-matrix td.total,.pair-matrix tr.total td{color:var(--ink-2)}
.pair-matrix .crest{width:18px; height:18px; vertical-align:-4px; margin-right:var(--sp-2)}
.foot{margin-top:var(--sp-8); padding-top:var(--sp-5); border-top:1px solid var(--rule); color:var(--ink-2); font-size:var(--fs-400)}
.foot a{color:var(--ink)}
.index-card{display:block; text-decoration:none; border-top:3px solid var(--ink); padding:var(--sp-5) 0; margin-top:var(--sp-6)}
.index-card time{font-family:var(--mono); font-size:var(--fs-200); color:var(--ink-3)}
.index-card h2{font-size:var(--fs-600); font-stretch:75%; margin:var(--sp-2) 0}
.index-card p{color:var(--ink-2); margin:0}
@media (max-width:720px){
{{> bar-phone.css}}  .wrap{padding-left:var(--sp-5); padding-right:var(--sp-5)}
  h1{font-size:1.6rem}
}
</style>

<header class="bar">
{{> header-top.html}}</header>

<main class="wrap piece">
  <p class="kicker">__KICKER__</p>
  <h1>__TITLE__</h1>
  <p class="summary">__SUMMARY__</p>
  __CHARTS__
  <div class="body">
__BODY__
  </div>
  <p class="foot">__FOOTER__<br><a href="/analise/">{{analysis.back}}</a></p>
</main>

<script>
{{> theme.js}}</script>
```

- [ ] **Step 4: Create `template_analysis_index.html`**

```html
{{> head.html}}<title>{{analysis.index_title}} · {{brand.name}}</title>
<meta property="og:type" content="website">
<meta property="og:site_name" content="{{brand.name}}">
<meta property="og:title" content="{{analysis.index_title}}">
<meta property="og:description" content="{{analysis.index_description}}">
<meta property="og:image" content="https://databrasileirao.com.br/og.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:url" content="https://databrasileirao.com.br/analise/">
<meta property="og:locale" content="{{share.locale}}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:image" content="https://databrasileirao.com.br/og.png">
<meta name="twitter:title" content="{{analysis.index_title}}">
<meta name="twitter:description" content="{{analysis.index_description}}">
<meta name="description" content="{{analysis.index_description}}">
{{> fonts.html}}<style>
{{> tokens.css}}{{> base.css}}{{> bar.css}}
/* ---- analysis index ---- */
.piece{max-width:720px; padding-top:var(--sp-7)}
.kicker{font-size:var(--fs-300); font-weight:700; letter-spacing:.12em; text-transform:uppercase; color:var(--signal); margin:0 0 var(--sp-4)}
h1{font-size:var(--fs-700); font-weight:800; font-stretch:64%; line-height:1.05; margin:0; text-wrap:balance}
.summary{font-size:var(--fs-600); color:var(--ink-2); line-height:1.45; margin:var(--sp-4) 0 0}
.index-card{display:block; text-decoration:none; border-top:3px solid var(--ink); padding:var(--sp-5) 0; margin-top:var(--sp-6)}
.index-card time{font-family:var(--mono); font-size:var(--fs-200); color:var(--ink-3)}
.index-card h2{font-size:var(--fs-600); font-stretch:75%; margin:var(--sp-2) 0}
.index-card p{color:var(--ink-2); margin:0}
@media (max-width:720px){
{{> bar-phone.css}}  .wrap{padding-left:var(--sp-5); padding-right:var(--sp-5)}
  h1{font-size:1.6rem}
}
</style>

<header class="bar">
{{> header-top.html}}</header>

<main class="wrap piece">
  <p class="kicker">{{nav.analysis}}</p>
  <h1>{{analysis.index_title}}</h1>
  <p class="summary">{{analysis.index_intro}}</p>
__CARDS__
</main>

<script>
{{> theme.js}}</script>
```

- [ ] **Step 5: Create `build_analyses.py`**

```python
"""Build the Análise section: `/analise/` and one page per piece.

    PYTHONPATH=src python3 src/brasileirao_simulator/entrypoints/report/build_analyses.py --out site/analise [--ga4 G-…]

Pieces live in `analyses/<slug>/` at the repository root (see analysis_piece.py
for the format). Pages share the production page's header, tokens and theme
through report/partials. Portuguese only. Standard library only; the PNG card
needs `rsvg-convert` on the host (see og_card.rasterise).
"""

import argparse
import html as html_lib
import json
import re
from pathlib import Path
from typing import Optional

from brasileirao_simulator.entrypoints.report import charts, markdown_lite, og_card
from brasileirao_simulator.entrypoints.report.analysis_piece import fill, load_piece, placeholders, shares
from brasileirao_simulator.entrypoints.report.build_report import (
    REPORT_DIR, _with_ga4, expand_includes, header_params, load_strings, render_strings,
)

ANALYSES_DIR = REPORT_DIR.parents[3] / "analyses"
SITE = "https://databrasileirao.com.br"
LANG = "pt"


def build(analyses_dir: Path, out_dir: Path, ga4: Optional[str] = None, cards: bool = True,
          logos: Optional[dict] = None, display: Optional[dict] = None) -> list:
    analyses_dir, out_dir = Path(analyses_dir), Path(out_dir)
    strings = load_strings(LANG)
    logos = logos if logos is not None else json.loads((REPORT_DIR / "logos.json").read_text(encoding="utf-8"))
    display = display if display is not None else og_card.display_names()

    folders = sorted(p for p in analyses_dir.iterdir() if (p / "piece.pt.md").is_file()) if analyses_dir.is_dir() else []
    pieces = sorted((load_piece(f) for f in folders), key=lambda p: (p.date, p.slug), reverse=True)

    written = [_write(out_dir / "index.html", _index(pieces, strings, display), ga4)]
    for piece in pieces:
        folder = out_dir / piece.slug
        written.append(_write(folder / "index.html", _piece_page(piece, strings, logos, display), ga4))
        if cards:
            _card(piece, folder / "og.png", logos, display)
    return written


def _shell(template_name: str, strings: dict) -> str:
    template = (REPORT_DIR / template_name).read_text(encoding="utf-8")
    template = expand_includes(template, REPORT_DIR / "partials", header_params("analise"))
    return render_strings(template, strings)


def _piece_page(piece, strings: dict, logos: dict, display: dict) -> str:
    values = placeholders(piece, display)
    title = fill(piece.title, values, piece.slug)
    summary = fill(piece.summary, values, piece.slug)
    body = markdown_lite.render(fill(piece.body, values, piece.slug))
    a, b = piece.data["clubs"]
    labels = {"down": strings["analysis.matrix_down"], "safe": strings["analysis.matrix_safe"],
              "total": strings["analysis.matrix_total"], "caption": strings["analysis.matrix_caption"]}
    chart_html = "\n".join(
        charts.RENDERERS[name](piece.data["counts"], {"name": values["club_a"], "crest": logos.get(a)},
                               {"name": values["club_b"], "crest": logos.get(b)}, labels)
        for name in piece.charts
    )
    stamp = {"date": values["date"], "round": values["round"]}
    replacements = {
        "__TITLE__": html_lib.escape(title), "__SUMMARY__": html_lib.escape(summary),
        "__OG_IMAGE__": f"{SITE}/analise/{piece.slug}/og.png", "__URL__": f"{SITE}/analise/{piece.slug}/",
        "__KICKER__": html_lib.escape(strings["analysis.kicker"].format(**stamp)),
        "__CHARTS__": chart_html, "__BODY__": body,
        "__FOOTER__": strings["analysis.footer"].format(**stamp),
    }
    page = _shell("template_analysis.html", strings)
    return re.sub("|".join(replacements), lambda m: replacements[m.group(0)], page)


def _index(pieces: list, strings: dict, display: dict) -> str:
    if pieces:
        cards = "\n".join(
            f'  <a class="index-card" href="/analise/{p.slug}/">'
            f'<time datetime="{p.date}">{og_card.br_date(p.date)}</time>'
            f"<h2>{html_lib.escape(fill(p.title, placeholders(p, display), p.slug))}</h2>"
            f"<p>{html_lib.escape(fill(p.summary, placeholders(p, display), p.slug))}</p></a>"
            for p in pieces
        )
    else:
        cards = f'  <p class="summary">{strings["analysis.empty"]}</p>'
    return _shell("template_analysis_index.html", strings).replace("__CARDS__", cards)


def _write(path: Path, page: str, ga4: Optional[str]) -> Path:
    page = _with_ga4(page, ga4).encode("ascii", "xmlcharrefreplace").decode("ascii")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page, encoding="ascii")
    return path


def _card(piece, out: Path, logos: dict, display: dict) -> None:
    values = placeholders(piece, display)
    s = shares(piece.data["counts"])
    rows = [("Nenhum cai", og_card.pct(s["neither"])), (f"Só {values['club_a']}", og_card.pct(s["only_a"])),
            (f"Só {values['club_b']}", og_card.pct(s["only_b"])), ("Os dois caem", og_card.pct(s["both"]))]
    crests = [logos[c] for c in piece.data["clubs"] if c in logos]
    svg = og_card.analysis_card_svg(fill(piece.title, values, piece.slug),
                                    f"{values['date']} · após a {values['round']}ª rodada", rows, crests)
    source = out.with_suffix(".svg")
    source.write_text(svg, encoding="utf-8")
    og_card.rasterise(source, out)
    source.unlink()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--analyses", default=str(ANALYSES_DIR))
    parser.add_argument("--out", required=True)
    parser.add_argument("--ga4", default=None)
    args = parser.parse_args()
    for path in build(Path(args.analyses), Path(args.out), ga4=args.ga4):
        print(f"wrote {path}")
```

- [ ] **Step 6: Run the tests**

Run: `docker run --rm -v "$PWD/src:/src" -v "$PWD/tests:/tests" -w /src --entrypoint "" brasileirao-simulator-app python -m pytest -q /tests/test_build_analyses.py`
Expected: `5 passed`. (The rows in `_card` are only exercised when `cards=True`; Task 9 builds a real card.)

- [ ] **Step 7: Commit**

```bash
git add src/brasileirao_simulator/entrypoints/report/template_analysis.html src/brasileirao_simulator/entrypoints/report/template_analysis_index.html src/brasileirao_simulator/entrypoints/report/build_analyses.py tests/test_build_analyses.py
git commit -m "feat(analise): build the index and one page per piece, with per-piece cards

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NdopobJg3w6UVXsPfadHgy"
```

---

### Task 8: `relegation_pairs.py --out`

**Files:**
- Modify: `src/brasileirao_simulator/entrypoints/relegation_pairs.py`
- Test: `tests/test_relegation_pairs.py`

**Interfaces:**
- Produces: `relegation_pairs.round_as_of(fixtures: pd.DataFrame, date: str) -> Optional[int]` (highest round number with a result kicked off before the end of local `date`); `relegation_pairs.data_document(season: int, date: str, clubs: tuple, counts: dict, round_: Optional[int], generated_at: str) -> dict` (keys exactly as the spec's `data.json`, with `counts` keys `neither, only_a, only_b, both`).

- [ ] **Step 1: Write the failing tests**

`tests/test_relegation_pairs.py`:

```python
import pandas as pd

from brasileirao_simulator.entrypoints import relegation_pairs as rp


def test_round_as_of_uses_local_dates_and_played_matches():
    fixtures = pd.DataFrame({
        "fixture_date": ["2026-09-07T22:00:00+00:00", "2026-09-15T01:30:00+00:00", "2026-09-16T22:00:00+00:00"],
        "league_round": ["Regular Season - 26", "Regular Season - 27", "Regular Season - 28"],
        "goals_home": [1, 2, None],
    })
    # 01:30 UTC on the 15th is 22:30 on the 14th in Brazil.
    assert rp.round_as_of(fixtures, "2026-09-14") == 27
    assert rp.round_as_of(fixtures, "2026-09-07") == 26
    assert rp.round_as_of(fixtures, "2026-01-01") is None


def test_data_document_has_the_agreed_keys():
    doc = rp.data_document(2026, "2026-09-14", ("Gremio", "Internacional"),
                           {"neither": 1, "only_Gremio": 2, "only_Internacional": 3, "both": 4}, 27, "2026-09-15")
    assert doc == {
        "question": "relegation_pairs", "season": 2026, "as_of": "2026-09-14", "round": 27,
        "clubs": ["Gremio", "Internacional"],
        "counts": {"neither": 1, "only_a": 2, "only_b": 3, "both": 4},
        "generated_by": "relegation_pairs.py", "generated_at": "2026-09-15",
    }
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `docker run --rm -v "$PWD/src:/src" -v "$PWD/tests:/tests" -w /src --entrypoint "" brasileirao-simulator-app python -m pytest -q /tests/test_relegation_pairs.py`
Expected: FAIL — `AttributeError: … round_as_of`.

- [ ] **Step 3: Implement**

In `relegation_pairs.py` add imports `import datetime`, `import json`, `from pathlib import Path`, `from typing import Optional`, `import pandas as pd`, `from brasileirao_simulator.domain.season_dates import utc_cutoff`, and these functions above `if __name__ == "__main__":`:

```python
def round_as_of(fixtures: pd.DataFrame, date: str) -> Optional[int]:
    """The furthest round with a result by the end of local `date` - the round
    a piece dated `date` is "after"."""
    next_day = str((pd.Timestamp(date) + pd.Timedelta(days=1)).date())
    kickoff = pd.to_datetime(fixtures["fixture_date"], utc=True, format="mixed")
    played = fixtures[fixtures["goals_home"].notnull() & (kickoff < utc_cutoff(next_day))]
    if played.empty:
        return None
    return int(played["league_round"].map(lambda label: int("".join(c for c in str(label) if c.isdigit()) or 0)).max())


def data_document(season: int, date: str, clubs: tuple, counts: dict, round_: Optional[int], generated_at: str) -> dict:
    """The `data.json` an analysis piece reads (see docs/superpowers/specs/2026-09-15-analise-pages-design.md)."""
    a, b = clubs
    return {
        "question": "relegation_pairs", "season": season, "as_of": date, "round": round_,
        "clubs": [a, b],
        "counts": {"neither": counts["neither"], "only_a": counts["only_" + a],
                   "only_b": counts["only_" + b], "both": counts["both"]},
        "generated_by": "relegation_pairs.py", "generated_at": generated_at,
    }
```

In the `__main__` block add `parser.add_argument("--out", default=None, help="write the analysis data.json here")` and, after printing:

```python
    if args.out:
        doc = data_document(args.season, args.date, tuple(args.clubs), counts,
                            round_as_of(SeasonData(args.season).fixtures, args.date),
                            datetime.date.today().isoformat())
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {args.out}")
```

- [ ] **Step 4: Run the tests**

Run: same command as Step 2. Expected: `2 passed`. (No simulation runs: neither function simulates.)

- [ ] **Step 5: Commit**

```bash
git add src/brasileirao_simulator/entrypoints/relegation_pairs.py tests/test_relegation_pairs.py
git commit -m "feat(analysis): relegation_pairs --out writes an analysis data.json

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NdopobJg3w6UVXsPfadHgy"
```

---

### Task 9: The first piece

**Files:**
- Create: `analyses/gremio-inter-rebaixamento/data.json`, `analyses/gremio-inter-rebaixamento/piece.pt.md`

**Interfaces:**
- Consumes: `build_analyses.build` (Task 7). Counts transcribed from the 2026-09-15 run (spec, "Data for the first piece").

- [ ] **Step 1: Write `data.json`** (transcribed, per the user's decision)

```json
{
  "question": "relegation_pairs",
  "season": 2026,
  "as_of": "2026-09-14",
  "round": 27,
  "clubs": ["Gremio", "Internacional"],
  "counts": {"neither": 3010, "only_a": 4661, "only_b": 7768, "both": 4561},
  "generated_by": "relegation_pairs.py (run 2026-09-15, counts transcribed)",
  "generated_at": "2026-09-15"
}
```

- [ ] **Step 2: Write `piece.pt.md`**

```markdown
---
slug: gremio-inter-rebaixamento
title: "{club_a} e {club_b}: em {neither_pct} das simulações, nenhum dos dois cai"
summary: "Um dos dois vai para a Série B em {either_pct} dos cenários; os dois juntos, em {both_pct}."
date: 2026-09-14
charts: [pair_matrix]
---
Depois da {round}ª rodada, {club_a} e {club_b} dividem a mesma aflição: o {club_a} cai em {a_down_pct} das temporadas simuladas, e o {club_b}, em {b_down_pct}. Olhando cada um isoladamente, parece que dá para torcer pelos dois. Olhando juntos, a conta é mais dura.

Em só **{neither_pct}** dos cenários os dois terminam fora do Z-4. Em **{both_pct}**, os dois caem. No resto, um se salva e o outro não: só o {club_a} cai em {only_a_pct}, só o {club_b} em {only_b_pct}.

## Por que um ajuda o outro

São quatro vagas de rebaixamento. Quando o {club_a} escapa, quase sempre é porque outro time ficou para trás — e o {club_b} é um dos candidatos mais fortes a ocupar esse lugar. Por isso a chance de os dois se salvarem é menor do que multiplicar as chances de cada um.

Números de {date}. As chances mudam a cada rodada.
```

- [ ] **Step 3: Build locally and check**

```bash
PYTHONPATH=src python3 src/brasileirao_simulator/entrypoints/report/build_analyses.py --out /tmp/claude-501/analise
grep -o "<h1>[^<]*</h1>" /tmp/claude-501/analise/gremio-inter-rebaixamento/index.html
ls -la /tmp/claude-501/analise/gremio-inter-rebaixamento/og.png
```

Expected: `<h1>Gr&#234;mio e Internacional: em 15% das simula&#231;&#245;es, nenhum dos dois cai</h1>`; an `og.png` of tens of KB.

- [ ] **Step 4: Browser check**

Serve `/tmp/claude-501` with `python3 -m http.server`; open `/analise/` and `/analise/gremio-inter-rebaixamento/` at 390px and 1280px, light and dark: the matrix shows 23% / 23% / 39% / 15% with totals 46/54 and 62/38, both crests (Grêmio, Internacional), green "neither" cell, no horizontal scroll at 390px; header `ANÁLISE` is active and `2026` links to `/`; the theme toggle works. Open `og.png` and send it to the user with `SendUserFile`.

- [ ] **Step 5: Commit**

```bash
git add analyses/gremio-inter-rebaixamento
git commit -m "content(analise): Grêmio e Inter - em 15% das simulações, nenhum dos dois cai

Counts transcribed from the 2026-09-15 relegation_pairs run, as decided.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NdopobJg3w6UVXsPfadHgy"
```

---

### Task 10: Site build and deploy wiring

**Files:**
- Modify: `scripts/build_site.sh`, `scripts/deploy_s3.sh`, `docs/DEPLOY.md`

**Interfaces:**
- Consumes: `build_analyses.py` CLI (Task 7).

- [ ] **Step 1: Build analyses in `build_site.sh`**

After the `build_og_card.py` line add:

```bash
# The Análise section: an index and one page per piece in analyses/, each with
# its own preview card. Portuguese only.
PYTHONPATH=src python3 src/brasileirao_simulator/entrypoints/report/build_analyses.py --out "$OUT/analise" $GA4_ARG
```

- [ ] **Step 2: Invalidate analyses in `deploy_s3.sh`**

Change `INVALIDATE_PATHS=("/index.html" "/en/index.html" "/og.png")` to:

```bash
INVALIDATE_PATHS=("/index.html" "/en/index.html" "/og.png" "/analise/*")
```

Update the comment above the invalidation (`Two paths rather than /*`) to: `Named paths rather than /*: the pages, the card, and the Análise section as one wildcard - well inside the 1,000 free invalidation paths a month.`

- [ ] **Step 3: Document**

In `docs/DEPLOY.md`, section "What the build produces", add rows for `analise/index.html`, `analise/<slug>/index.html` and `analise/<slug>/og.png`, and a short paragraph: pieces live in `analyses/<slug>/`, are built by `build_analyses.py`, never refresh on their own, and a new piece's numbers come from `relegation_pairs.py --out analyses/<slug>/data.json` (a simulation — ask first).

- [ ] **Step 4: Full local build**

Run: `scripts/build_site.sh`
Expected: the listing shows `analise/index.html`, `analise/gremio-inter-rebaixamento/index.html`, `analise/gremio-inter-rebaixamento/og.png`, plus the existing three files.

- [ ] **Step 5: Run every test touched by this plan, as its own step**

Run: `docker run --rm -v "$PWD/src:/src" -v "$PWD/tests:/tests" -w /src --entrypoint "" brasileirao-simulator-app python -m pytest -q /tests/test_site_partials.py /tests/test_report_build.py /tests/test_report_strings.py /tests/test_markdown_lite.py /tests/test_og_card.py /tests/test_analysis_piece.py /tests/test_analysis_charts.py /tests/test_build_analyses.py /tests/test_relegation_pairs.py`
Expected: all pass. Read the result before continuing.

- [ ] **Step 6: Commit, merge**

```bash
git add scripts/build_site.sh scripts/deploy_s3.sh docs/DEPLOY.md
git commit -m "build(site): build and invalidate the Análise section

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NdopobJg3w6UVXsPfadHgy"
```

Merge the worktree branch into `fix/local-day-cutoff` with `--no-ff`, remove the worktree, delete the branch.

- [ ] **Step 7: Deploy — only after the user says go**

```bash
DRY_RUN=1 scripts/deploy_s3.sh
```

Expected: uploads for the two main pages, `og.png`, and the three `analise/` files; `would invalidate /index.html /en/index.html /og.png /analise/* on E2RZSW4J8OESEP`. Ask the user; on go:

```bash
scripts/deploy_s3.sh
curl -s -o /dev/null -w "%{http_code}\n" https://databrasileirao.com.br/analise/
curl -s https://databrasileirao.com.br/analise/gremio-inter-rebaixamento/ | grep -o '<title>[^<]*</title>\|og:image" content="[^"]*"'
curl -s -o /dev/null -w "%{http_code}\n" https://databrasileirao.com.br/analise/gremio-inter-rebaixamento/og.png
```

Expected: `200`, the piece title and `…/analise/gremio-inter-rebaixamento/og.png`, `200`. If a folder URL returns 403, the CloudFront rewrite does not cover sub-directories: stop and bring it to the user (infrastructure change).
