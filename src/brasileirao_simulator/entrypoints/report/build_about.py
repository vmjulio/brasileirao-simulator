"""Build the Quem somos page.

    PYTHONPATH=src python3 src/brasileirao_simulator/entrypoints/report/build_about.py --out site/quem-somos/index.html [--ga4 G-…]

Portuguese only, and its copy lives in template_about.html: the page has no
English twin, so a strings entry per sentence would buy nothing. It shares the
site's header, tokens and theme through report/partials, where it is the
current menu entry.
"""

import argparse
from pathlib import Path
from typing import Optional

from brasileirao_simulator.entrypoints.report.build_report import (
    REPORT_DIR, _with_ga4, expand_includes, header_params, load_strings, render_strings,
)


def build(out_path: Path, ga4: Optional[str] = None) -> Path:
    template = (REPORT_DIR / "template_about.html").read_text(encoding="utf-8")
    page = expand_includes(template, REPORT_DIR / "partials", header_params("quem-somos"))
    page = render_strings(page, load_strings("pt"))
    page = _with_ga4(page, ga4).encode("ascii", "xmlcharrefreplace").decode("ascii")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(page, encoding="ascii")
    return out_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", required=True)
    parser.add_argument("--ga4", default=None)
    args = parser.parse_args()
    print(f"wrote {build(Path(args.out), ga4=args.ga4)}")
