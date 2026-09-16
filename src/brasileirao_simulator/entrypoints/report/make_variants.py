"""Build three tracker designs from the v3 template.

Each variant keeps v3's markup and script - same data, same charts, same
strings - and replaces the palette, the type and the way a probability cell is
drawn. The three sit between the project's first design (cards, pills, a green
accent) and the newsroom one (a table first, monospace numbers).
"""
import pathlib
import re

REPORT = pathlib.Path("/Users/vmjulio/Documents/GitHub/brasileirao-simulator/src/brasileirao_simulator/entrypoints/report")
base = (REPORT / "template_v3.html").read_text(encoding="utf-8")
style = re.search(r"<style>(.*?)</style>", base, re.S).group(1)

# Everything after the token blocks is structure, and every variant keeps it.
STRUCTURE_FROM = style.index("*{box-sizing:border-box}")
structure = style[STRUCTURE_FROM:]


def tokens(light: str, dark: str) -> str:
    """The three token blocks every design needs: light, system dark, forced dark."""
    return (":root{\n" + light + "\n}\n"
            "@media (prefers-color-scheme:dark){\n  :root:not([data-theme=\"light\"]){\n" + dark + "\n  }\n}\n"
            ":root[data-theme=\"dark\"]{\n" + dark + "\n}\n")


SHARED_SCALE = """
  --fs-100:.6875rem; --fs-200:.75rem; --fs-300:.8125rem; --fs-400:.9375rem;
  --fs-500:1rem; --fs-600:1.25rem; --fs-700:1.75rem; --fs-800:3rem;
  --sp-1:2px; --sp-2:4px; --sp-3:6px; --sp-4:8px; --sp-5:12px; --sp-6:18px; --sp-7:28px; --sp-8:44px;
"""

VARIANTS = {}

# ---------------------------------------------------------------- placar ----
VARIANTS["v4"] = dict(
    title="Placar",
    fonts='<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Roboto+Condensed:wght@500;700&family=Roboto:wght@400;500;700&family=Roboto+Mono:wght@400;500&display=swap">',
    light="""
  --ground:#ffffff; --surface:#ffffff; --ink:#12171c; --ink-2:#4c565f; --ink-3:#78848d;
  --ocean:#0b7a3b; --ocean-soft:rgba(11,122,59,.12); --sun:#e8b006; --clay:#c0271c;
  --rule:#dfe3e7; --rule-strong:#b9c1c8;
  --good:#0b7a3b; --bad:#c0271c; --flag:#e8b006;
  --grid:#eceff1; --tooltip-bg:rgba(255,255,255,.98);
  --bg-page:var(--ground); --bg-elevated:var(--surface); --bg-panel:#f4f6f7;
  --border:var(--rule); --border-strong:var(--rule-strong);
  --text-primary:var(--ink); --text-secondary:var(--ink-2); --text-muted:var(--ink-3);
  --accent:var(--good); --accent-muted:rgba(11,122,59,.10); --accent-rgb:11,122,59;
  --danger:var(--bad); --danger-muted:rgba(192,39,28,.10); --danger-rgb:192,39,28;
  --gold:#8a6a00; --seq-rgb:11,122,59;
  --display:"Roboto Condensed","Helvetica Neue",Arial,sans-serif;
  --body:Roboto,system-ui,-apple-system,sans-serif;
  --mono:"Roboto Mono",ui-monospace,Menlo,monospace;
  --radius-sm:2px; --radius-md:4px; --radius-pill:3px;""" + SHARED_SCALE,
    dark="""
    --ground:#101418; --surface:#161b21; --ink:#e9edf1; --ink-2:#a4b0b9; --ink-3:#7a858e;
    --ocean:#48b876; --ocean-soft:rgba(72,184,118,.18); --sun:#f0c552; --clay:#e5695c;
    --rule:#262d34; --rule-strong:#3d464e;
    --good:#48b876; --bad:#e5695c; --flag:#f0c552;
    --grid:#20262c; --tooltip-bg:rgba(22,27,33,.98);
    --accent-rgb:72,184,118; --danger-rgb:229,105,92; --seq-rgb:72,184,118; --gold:#f0c552;""",
    overrides="""
/* Placar: a results page. Condensed headlines, square corners, a thick rule
   under the masthead, probabilities shaded straight into the cell. */
h1{font-family:var(--display); font-weight:700; font-stretch:normal; font-variation-settings:normal;
  text-transform:uppercase; letter-spacing:-.01em; font-size:var(--fs-800)}
h2{font-family:var(--display); font-weight:700; text-transform:uppercase; letter-spacing:.01em;
  font-variation-settings:normal; border-top:4px solid var(--ink); padding-top:var(--sp-4)}
h2::before{display:none}
.kicker{font-family:var(--body); font-weight:700; color:var(--bad); letter-spacing:.14em}
.kicker::after{display:none}
.mast{border-bottom:4px double var(--rule-strong); padding-bottom:var(--sp-6)}
.mast::before{display:none}
.bar .name::before{border-radius:2px; background:var(--good)}
.tiles{border-top:2px solid var(--ink); padding-top:var(--sp-5)}
.tile{background:none; border-left:0; border-right:1px solid var(--rule); border-radius:0; padding:0 var(--sp-6) 0 0}
.tile:last-child{border-right:0}
.tile .n{font-family:var(--mono); font-weight:500}
.card, .scroller{background:none; border:1px solid var(--rule); border-radius:var(--radius-md)}
.pill{border-radius:var(--radius-pill); text-transform:uppercase; letter-spacing:.05em; font-family:var(--display)}
.fc .meter{display:block; position:relative; height:28px; border-radius:var(--radius-sm);
  background:rgba(var(--accent-rgb), calc(var(--p) * 0.0055)); text-align:center}
.fc .meter.bad{background:rgba(var(--danger-rgb), calc(var(--p) * 0.0055))}
.fc .meter .track{display:none}
.fc .meter .v{width:auto; display:block; line-height:28px; text-align:center; font-weight:500; color:var(--ink)}
table.fc td{height:34px}
thead th{border-bottom:2px solid var(--ink)}
""")

# ---------------------------------------------------------------- painel ----
VARIANTS["v5"] = dict(
    title="Painel",
    fonts='<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap">',
    light="""
  --ground:#f4f6f8; --surface:#ffffff; --ink:#15202b; --ink-2:#4a5a68; --ink-3:#7b8a97;
  --ocean:#0f766e; --ocean-soft:rgba(15,118,110,.14); --sun:#c98a12; --clay:#be3a2b;
  --rule:#e2e8ee; --rule-strong:#c2ccd6;
  --good:#0f766e; --bad:#be3a2b; --flag:#c98a12;
  --grid:#edf1f5; --tooltip-bg:rgba(255,255,255,.98);
  --bg-page:var(--ground); --bg-elevated:var(--surface); --bg-panel:#eef2f6;
  --border:var(--rule); --border-strong:var(--rule-strong);
  --text-primary:var(--ink); --text-secondary:var(--ink-2); --text-muted:var(--ink-3);
  --accent:var(--good); --accent-muted:rgba(15,118,110,.12); --accent-rgb:15,118,110;
  --danger:var(--bad); --danger-muted:rgba(190,58,43,.12); --danger-rgb:190,58,43;
  --gold:#a06a00; --seq-rgb:15,118,110;
  --display:"IBM Plex Sans",system-ui,sans-serif;
  --body:"IBM Plex Sans",system-ui,-apple-system,sans-serif;
  --mono:"IBM Plex Mono",ui-monospace,Menlo,monospace;
  --radius-sm:6px; --radius-md:12px; --radius-pill:999px;""" + SHARED_SCALE,
    dark="""
    --ground:#0e1620; --surface:#152029; --ink:#e6edf3; --ink-2:#9fb0bd; --ink-3:#78899a;
    --ocean:#3fbfae; --ocean-soft:rgba(63,191,174,.2); --sun:#e0aa46; --clay:#e2705f;
    --rule:#22303c; --rule-strong:#384a58;
    --good:#3fbfae; --bad:#e2705f; --flag:#e0aa46;
    --grid:#1b2733; --tooltip-bg:rgba(21,32,41,.98);
    --accent-rgb:63,191,174; --danger-rgb:226,112,95; --seq-rgb:63,191,174; --gold:#e0aa46;""",
    overrides="""
/* Painel: the project's first design, modernised. Soft cards on a tinted
   ground, rounded controls, probabilities as tinted chips. */
h1{font-family:var(--display); font-weight:600; font-variation-settings:normal; letter-spacing:-.02em; font-size:2.5rem}
h2{font-family:var(--display); font-weight:600; font-variation-settings:normal}
h2::before{width:8px; height:8px; background:var(--accent)}
.mast::before{border-color:var(--accent-muted)}
.card, .scroller{box-shadow:0 1px 2px rgba(21,32,41,.06), 0 8px 24px rgba(21,32,41,.05); border-radius:var(--radius-md)}
.tile{background:var(--surface); border-left:0; border-radius:var(--radius-md);
  box-shadow:0 1px 2px rgba(21,32,41,.06)}
.tile .n{color:var(--accent)}
.tiles{gap:var(--sp-5)}
.pill{border-radius:var(--radius-pill); box-shadow:0 1px 2px rgba(21,32,41,.04)}
.fc .meter{display:inline-flex; gap:0; justify-content:center; min-width:66px; height:26px; padding:0 var(--sp-5);
  border-radius:var(--radius-pill); background:rgba(var(--accent-rgb), calc(.08 + var(--p) * 0.0045))}
.fc .meter.bad{background:rgba(var(--danger-rgb), calc(.08 + var(--p) * 0.0045))}
.fc .meter .track{display:none}
.fc .meter .v{width:auto; line-height:26px; font-weight:500; color:var(--ink)}
.fc td.prob{text-align:center}
.heat .cell{border-radius:var(--radius-sm)}
""")

# ---------------------------------------------------------------- jornal ----
VARIANTS["v6"] = dict(
    title="Jornal",
    fonts='<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Source+Serif+4:opsz,wght@8..60,400;8..60,600;8..60,700&family=Source+Sans+3:wght@400;600;700&family=Roboto+Mono:wght@400;500&display=swap">',
    light="""
  --ground:#fbf8f1; --surface:#fffdf8; --ink:#1a1a18; --ink-2:#4f4f49; --ink-3:#7d7d74;
  --ocean:#1f5d3f; --ocean-soft:rgba(31,93,63,.12); --sun:#8a6a12; --clay:#a32a1e;
  --rule:#ddd8cb; --rule-strong:#b3ad9d;
  --good:#1f5d3f; --bad:#a32a1e; --flag:#8a6a12;
  --grid:#e8e3d7; --tooltip-bg:rgba(255,253,248,.98);
  --bg-page:var(--ground); --bg-elevated:var(--surface); --bg-panel:#f2ede2;
  --border:var(--rule); --border-strong:var(--rule-strong);
  --text-primary:var(--ink); --text-secondary:var(--ink-2); --text-muted:var(--ink-3);
  --accent:var(--good); --accent-muted:rgba(31,93,63,.10); --accent-rgb:31,93,63;
  --danger:var(--bad); --danger-muted:rgba(163,42,30,.10); --danger-rgb:163,42,30;
  --gold:#8a6a12; --seq-rgb:31,93,63;
  --display:"Source Serif 4",Georgia,serif;
  --body:"Source Sans 3",system-ui,-apple-system,sans-serif;
  --mono:"Roboto Mono",ui-monospace,Menlo,monospace;
  --radius-sm:0px; --radius-md:0px; --radius-pill:0px;""" + SHARED_SCALE,
    dark="""
    --ground:#14140f; --surface:#1b1b16; --ink:#f1ece0; --ink-2:#b0aa9c; --ink-3:#847e72;
    --ocean:#5fae83; --ocean-soft:rgba(95,174,131,.2); --sun:#d4ac52; --clay:#d9695a;
    --rule:#2c2c25; --rule-strong:#464538;
    --good:#5fae83; --bad:#d9695a; --flag:#d4ac52;
    --grid:#242420; --tooltip-bg:rgba(27,27,22,.98);
    --accent-rgb:95,174,131; --danger-rgb:217,105,90; --seq-rgb:95,174,131; --gold:#d4ac52;""",
    overrides="""
/* Jornal: a broadsheet. Serif headlines, hairline rules, square corners,
   probabilities shaded into ruled cells. */
h1{font-family:var(--display); font-weight:700; font-variation-settings:normal; letter-spacing:-.015em;
  font-size:3.1rem; line-height:1.02}
h2{font-family:var(--display); font-weight:700; font-variation-settings:normal; letter-spacing:-.01em;
  border-bottom:1px solid var(--ink); padding-bottom:var(--sp-3)}
h2::before{display:none}
.kicker{font-family:var(--body); font-weight:700; color:var(--ink); letter-spacing:.2em}
.kicker::after{background:var(--ink); max-width:none}
.dek{font-family:var(--display); font-size:1.15rem; font-style:italic}
.mast::before{display:none}
.mast{border-bottom:3px solid var(--ink); padding-bottom:var(--sp-6)}
.bar{border-bottom:3px double var(--rule-strong)}
.bar .name{font-family:var(--display)}
.bar .name::before{background:var(--ink)}
.tiles{border-top:1px solid var(--ink); border-bottom:1px solid var(--ink); padding:var(--sp-5) 0; gap:0}
.tile{background:none; border-left:1px solid var(--rule); border-radius:0; padding:0 var(--sp-6)}
.tile:first-child{border-left:0; padding-left:0}
.tile .n{font-family:var(--display); font-weight:700; font-size:1.9rem}
.card, .scroller{background:none; border-top:1px solid var(--ink); border-radius:0; padding-left:0; padding-right:0}
.chart-title{font-family:var(--display); font-weight:700}
.pill{border-radius:0; border-color:var(--rule-strong); background:none}
.pill[aria-pressed="true"]{background:var(--ink); border-color:var(--ink); color:var(--ground)}
.fc .meter{display:block; height:30px; text-align:center; border-bottom:1px solid var(--rule);
  background:rgba(var(--accent-rgb), calc(var(--p) * 0.005))}
.fc .meter.bad{background:rgba(var(--danger-rgb), calc(var(--p) * 0.005))}
.fc .meter .track{display:none}
.fc .meter .v{width:auto; display:block; line-height:30px; font-family:var(--display); font-weight:600; color:var(--ink)}
table.fc thead th{border-bottom:1px solid var(--ink)}
.callout{background:none; border-left:3px solid var(--ink)}
""")

for name, spec in VARIANTS.items():
    css = tokens(spec["light"], spec["dark"]) + structure + spec["overrides"]
    page = base
    page = re.sub(r"<style>.*?</style>", lambda _: "<style>\n" + css + "</style>", page, count=1, flags=re.S)
    page = re.sub(r'<link rel="stylesheet" href="https://fonts\.googleapis\.com[^>]*>', spec["fonts"], page, count=1)
    # Its own name in the gallery: the tag always wins over the publish title.
    page = page.replace("<title>{{meta.title}}</title>",
                        "<title>Brasileir\u00e3o \u00b7 " + spec["title"] + "</title>", 1)
    (REPORT / f"template_{name}.html").write_text(page, encoding="utf-8")
    print(f"wrote template_{name}.html  ({spec['title']}, {len(page)//1024} KB)")
