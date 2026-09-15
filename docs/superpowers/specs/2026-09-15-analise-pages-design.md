# Análise pages — design

Date: 2026-09-15. Status: approved in conversation, pending review of this document.

## Goal

Give databrasileirao.com.br an **Análise** section: dated editorial pieces that
answer specific questions with the simulations — questions the main forecast page
cannot answer, such as "in how many simulated seasons are neither Grêmio nor
Internacional relegated?". The first piece is exactly that question.

## Decisions (made with the user)

| # | Question | Decision |
|---|---|---|
| 1 | What the page is | **Editorial pieces**, numbers computed when written and frozen "as of" a date. No live pair tool for now. |
| 2 | Layout | **Index `/analise/` + one page per piece** at `/analise/<slug>/`. |
| 3 | Authoring | **A folder per piece**: Markdown text + `data.json` written by the analysis script. |
| 4 | Languages | **Portuguese only.** The English header keeps "Analysis · soon"; no `/en/analise/`. |
| 5 | Link previews | **One `og.png` per piece, generated from its data.** |
| 6 | Build approach | **Shared partials + a dedicated analysis builder** (approach 1 of 3). |
| 7 | First chart | **`pair_matrix`**: a 2×2 table (club A down / not down × club B down / not down) with totals. |

Rejected alternatives, for the record: a live "pick two clubs" tool (needs a
pipeline change; wait until pieces show which questions matter); one long page
(every shared link would show the same preview); hand-written HTML per piece and
Markdown with typed-in numbers (numbers not traceable to their run); both
languages always (blocks timely pieces on translation); a copied header per
template (drifts — the header changed five times in one week); an analysis mode
of `build_report.py` (ships the 450 KB forecast payload with every piece).

## Pages

### Header (all pages)

- Top green row as today. **ANÁLISE becomes a link** to `/analise/` in Portuguese;
  it is the underlined active entry on analysis pages, where **2026** becomes a
  plain link back to `/`. Temporadas and Quem somos stay "em breve".
- The section row (Time a time, Seu time, …) exists only on the main page.

### Index — `/analise/`

- Kicker "ANÁLISE", heading "Análises", one-line intro.
- One card per piece, newest first: date, title, summary, linking to the piece.
- No pieces: the line "Nenhuma análise publicada ainda".

### Piece — `/analise/<slug>/`

- Kicker: `ANÁLISE · <date> · APÓS A <round>ª RODADA`.
- Title and summary (from front matter, placeholders filled).
- Charts listed in front matter, in order.
- Body (Markdown).
- Footer: `Números de <date>, após a <round>ª rodada. As chances de hoje estão na
  página principal.` linking to `/`, then `← Todas as análises`.
- Same width, typography and light/dark themes as the main page; phone-first.

### `pair_matrix`

```
                  <B> cai     <B> não cai    Total
<A> cai            both         only_a       a_down
<A> não cai        only_b       neither      a_safe
Total              b_down       b_safe       100%
```

- Cells shaded by share with the site's relegation (red) palette; the
  **neither** cell takes the green tint.
- Crests on the row and column labels; labels come from the club names in
  `data.json` via the site's display names.
- Totals are each club's own relegation chance.
- Fits 390px without horizontal scrolling.

### Not included

Comments, bylines, tags, search, related pieces, English pieces, a live pair tool.

## Piece format

```
analyses/<slug>/
  piece.pt.md
  data.json
```

`analyses/` sits at the repository root.

### `piece.pt.md`

```markdown
---
slug: gremio-inter-rebaixamento
title: "Grêmio e Inter: em {neither_pct} das simulações, nenhum dos dois cai"
summary: "Um dos dois vai para a Série B em {either_pct} dos cenários; os dois juntos, em {both_pct}."
date: 2026-09-14
charts: [pair_matrix]
---
Body in Markdown, with placeholders such as {only_a_pct}.
```

Front matter is a small fixed key set (`slug`, `title`, `summary`, `date`,
`charts`); the parser supports exactly that shape, not general YAML.

### `data.json`

Written by the analysis script, never by hand:

```json
{
  "question": "relegation_pairs",
  "season": 2026,
  "as_of": "2026-09-14",
  "round": 27,
  "clubs": ["Gremio", "Internacional"],
  "counts": {"neither": 3010, "only_a": 4661, "only_b": 7768, "both": 4561},
  "generated_by": "relegation_pairs.py",
  "generated_at": "2026-09-15"
}
```

### Placeholders

Derived by the builder from `counts` (total = sum of the four):

| Placeholder | Value |
|---|---|
| `neither_pct` | neither / total |
| `only_a_pct`, `only_b_pct` | only_a / total, only_b / total |
| `both_pct` | both / total |
| `either_pct` | (only_a + only_b + both) / total |
| `a_down_pct`, `b_down_pct` | (only_a + both) / total, (only_b + both) / total |
| `club_a`, `club_b` | display names of `clubs` |
| `date`, `round` | from `as_of` (long Portuguese date) and `round` |

Percentages round as the site's `pctQuote` does (`<1%`, `>99%`, else whole
percent). **The total number of simulated seasons is never rendered**
(simulation counts stay internal).

### Validation — each fails the build with the piece and the cause

- `data.json` missing or unreadable; `question` not one the builder knows.
- Front-matter `date` differs from `data.json` `as_of`.
- A placeholder with no value; a chart name with no renderer.
- Counts not four non-negative integers with a positive total.
- `slug` differs from the folder name.

A re-run of the analysis script changes a published piece's numbers; that is a
deliberate edit, never automatic.

## Build and deploy

### Shared partials (refactor of `template_db.html`)

`src/brasileirao_simulator/entrypoints/report/partials/`:

- `head.html` — doctype, `<html lang>`, charset, viewport, fonts, GA4 slot.
- `header.html` — the green top row and pages menu, parameterised by the active
  page (`2026` or `analise`). The section row stays in `template_db.html`.
- `base.css` — colour tokens (light and dark), `--bar-*` tokens, typography,
  `[hidden]`, focus ring.
- `theme.js` — sun/moon toggle and saved theme.

`build_report.py` gains an include step (`{{> name}}`) that runs before string
substitution. **Gate:** the PT and EN main pages are built and hashed before the
extraction; after it, both builds must be byte-identical. The existing pinned v1
page and translation gates stay green.

### `build_analyses.py`

`src/brasileirao_simulator/entrypoints/report/build_analyses.py`, stdlib only,
host-runnable:

1. Read every `analyses/*/`; validate (above).
2. Render Markdown (subset: paragraphs, `#`–`###` headings, `**bold**`,
   `*italic*`, `[links](url)`, `-` lists; HTML-escaped otherwise).
3. Fill placeholders; render charts.
4. Write `site/analise/<slug>/index.html` with its own `og:title`,
   `og:description`, `og:image` (`/analise/<slug>/og.png`), `og:url`.
5. Write `site/analise/<slug>/og.png` through a shared `og_card` module
   (rasterise step moved out of `build_og_card.py`): brand, both crests, title,
   the four percentages.
6. Write `site/analise/index.html`.

### Scripts

- `scripts/build_site.sh` runs `build_analyses.py --out site/analise` after the
  main pages.
- `scripts/deploy_s3.sh`: the existing `aws s3 sync site/ … --delete` uploads the
  new files; `INVALIDATE_PATHS` gains `/analise/*` (one wildcard = one path).
- `scripts/refresh_data.sh`: unchanged. Analyses never refresh themselves.

### Analysis script

`relegation_pairs.py` gains `--out <path>` writing `data.json` with the keys
above (`round` from the season's round state as of the date).

### Hosting prerequisite

S3 behind CloudFront must serve `/analise/` and `/analise/<slug>/` as
`index.html`. Before building, inspect the distribution read-only (the
`.function-arn` file suggests a CloudFront Function exists). If sub-directory
index documents are not served, adding or changing a CloudFront Function is an
infrastructure change and is proposed to the user before it is made.

## Testing

Run in the container, as a separate step from any commit, merge or deploy.

1. **Markdown**: each supported construct; escaping of `<` and `&`; plain lines
   pass through.
2. **Placeholders**: all derived values from fixed counts; rounding edges
   (`<1%`, `>99%`); unknown placeholder fails; the total never appears in output.
3. **Validation**: one failing case per rule above.
4. **`pair_matrix`**: four cells and totals for fixed counts (e.g. 46/54, 62/38),
   crests present, green tint on the neither cell.
5. **Index**: newest first; empty `analyses/` renders the empty-state page.
6. **Partials**: byte-identical PT/EN main pages before and after; existing
   report tests (12) green.
7. **`relegation_pairs.py --out`**: writes the agreed keys, with the simulator
   stubbed (no simulation in tests).

Build checks: `build_site.sh` (no GA4) produces both analysis pages and the piece
`og.png`; each page starts with `<!doctype html>`, has the viewport meta, its own
`og:*`, ANÁLISE active, and no simulation counts.

Browser checks: 390px and 1280px, light and dark; header links both ways; theme
toggle on analysis pages; matrix without horizontal scroll; the piece `og.png`
sent to the user before deploy.

Deploy checks: hosting prerequisite confirmed; dry run lists only the new files
plus the main pages; after deploy, `curl` both analysis URLs for title and
`og:image`.

## Open decision

**Data for the first piece.** Either a fresh `relegation_pairs.py --out` run as
of 2026-09-14 (20,000 seasons, ~11 s — needs the user's go, per the
no-simulations rule), or `data.json` built once from the counts already produced
on 2026-09-15 (3,010 / 4,661 / 7,768 / 4,561), which is the one exception to
"never by hand" and is recorded as such in the file's `generated_by`.
