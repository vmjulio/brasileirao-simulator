#!/usr/bin/env bash
# Build the public site into site/ - the exact bytes scripts/deploy_s3.sh
# syncs to S3 and CloudFront serves.
#
#   scripts/build_site.sh                 # no analytics, for a local look
#   GA4_ID=G-XXXXXXX scripts/build_site.sh   # the real deploy build
#
# Portuguese sits at the root because the readers do; English lives under /en/.
# Both are self-contained single files, so there is nothing else to copy.
set -euo pipefail

cd "$(dirname "$0")/.."
OUT=site
BUILD="python brasileirao_simulator/entrypoints/report/build_report.py --version db --seasons current"
GA4_ARG=""
if [[ -n "${GA4_ID:-}" ]]; then
  GA4_ARG="--ga4 ${GA4_ID}"
  echo "building with analytics: ${GA4_ID}"
else
  echo "building without analytics (set GA4_ID to include the tag)"
fi

rm -rf "$OUT"
mkdir -p "$OUT/en"

docker-compose run --rm --entrypoint "" app sh -c "
  $BUILD --lang pt $GA4_ARG --out /src/site_pt.html &&
  $BUILD --lang en $GA4_ARG --out /src/site_en.html" >/dev/null

mv src/site_pt.html "$OUT/index.html"
mv src/site_en.html "$OUT/en/index.html"

# The card a pasted link shows. Drawn from the same dataset the pages carry, so
# it says this round's numbers rather than being a logo that never changes.
PYTHONPATH=src python3 src/brasileirao_simulator/entrypoints/report/build_og_card.py --out "$OUT/og.png"

# The Análise section: an index and one page per piece in analyses/, each with
# its own preview card. Portuguese only.
PYTHONPATH=src python3 src/brasileirao_simulator/entrypoints/report/build_analyses.py --out "$OUT/analise" $GA4_ARG

printf '\n%s\n' "site/ built:"
find "$OUT" -type f | sort | while read -r f; do
  printf '  %-22s %s\n' "${f#"$OUT"/}" "$(du -h "$f" | cut -f1)"
done
