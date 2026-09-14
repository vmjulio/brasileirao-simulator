#!/usr/bin/env bash
# Build the site and put it behind CloudFront.
#
#   scripts/deploy_s3.sh                      # build, sync, invalidate
#   GA4_ID=G-XXXXXXX scripts/deploy_s3.sh     # ...with the analytics tag
#   DRY_RUN=1 scripts/deploy_s3.sh            # show what would change, touch nothing
#
# Configuration comes from the environment, or from .env.site beside this repo
# (gitignored). Both are required rather than defaulted: a typo'd bucket name
# should fail loudly, not publish the site somewhere nobody is looking.
#
#   SITE_BUCKET=databrasileirao-site
#   SITE_DISTRIBUTION_ID=E1234567890ABC
#   AWS_PROFILE=databrasileirao-deploy
#
# Use a profile whose key can reach ONLY the site bucket and one distribution -
# scripts/iam-site-deploy-policy.json is that policy. The default profile on
# this laptop belongs to a user whose key also lives on another project's
# server and can write to the data lake; publishing the website must not
# depend on that.
set -euo pipefail

cd "$(dirname "$0")/.."
# set -a so the sourced values are exported: `aws` is a child process and sees
# only the environment, not this shell's variables. Without it the deploy
# silently falls back to the default profile.
if [[ -f .env.site ]]; then set -a; source .env.site; set +a; fi

: "${SITE_BUCKET:?set SITE_BUCKET (see the header of this script)}"
: "${SITE_DISTRIBUTION_ID:?set SITE_DISTRIBUTION_ID (see the header of this script)}"

# The data lake holds the pipeline's own output and the bolão bundle. Publishing
# a public website into it would put two very different blast radii in one
# bucket, so refuse rather than trust a stray environment variable.
if [[ "$SITE_BUCKET" == "vmj-lake" ]]; then
  echo "refusing: vmj-lake is the data lake, not the website bucket" >&2
  exit 1
fi

scripts/build_site.sh

SYNC_ARGS=(--delete --cache-control "public, max-age=0, must-revalidate")
# The card changes with the data, and social platforms cache it aggressively by
# URL - so it is invalidated with the pages rather than left to go stale.
INVALIDATE_PATHS=("/index.html" "/en/index.html" "/og.png")
if [[ -n "${DRY_RUN:-}" ]]; then
  echo
  echo "DRY RUN - nothing will be written"
  aws s3 sync site/ "s3://${SITE_BUCKET}" "${SYNC_ARGS[@]}" --dryrun
  echo "would invalidate /* on ${SITE_DISTRIBUTION_ID}"
  exit 0
fi

echo
aws s3 sync site/ "s3://${SITE_BUCKET}" "${SYNC_ARGS[@]}"

# CloudFront caches at the edge, so a sync alone leaves readers on last round's
# forecast. Two paths rather than /*: it is the whole site, and it keeps well
# inside the 1,000 free invalidation paths a month.
INVALIDATION=$(aws cloudfront create-invalidation \
  --distribution-id "$SITE_DISTRIBUTION_ID" \
  --paths "${INVALIDATE_PATHS[@]}" \
  --query 'Invalidation.Id' --output text)

echo
echo "synced to s3://${SITE_BUCKET}"
echo "invalidation ${INVALIDATION} created on ${SITE_DISTRIBUTION_ID}"
echo "live in a minute or two: https://databrasileirao.com.br"
