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

REFERENCE_MD5 = "46676a0e0bc1bf23b507c830e017af31"
FIXTURE_BENCHMARK = (
    Path(__file__).resolve().parent / "fixtures" / "report_benchmark_2026-09-10T0943.json"
)


def test_build_reproduces_the_reference_page_byte_for_byte(tmp_path):
    out = tmp_path / "forecasts.html"

    build_report.build(out, benchmark_path=FIXTURE_BENCHMARK)

    digest = hashlib.md5(out.read_bytes()).hexdigest()
    assert digest == REFERENCE_MD5
