# Deploying databrasileirao.com.br

The site is two self-contained HTML files and a preview card. No server, no
database, no backend: everything is precomputed by the Monte Carlo and baked
into the page. Deploying means copying three files to S3 and clearing the CDN.

Set up 2026-09-14.

---

## The short version

```bash
scripts/deploy_s3.sh
```

That builds both languages, draws the card, syncs to S3 and invalidates
CloudFront. About 40 seconds. Live within a minute or two.

It reads `.env.site` (gitignored, beside the repo root):

```
SITE_BUCKET=databrasileirao-site
SITE_DISTRIBUTION_ID=E2RZSW4J8OESEP
AWS_PROFILE=db-provision
GA4_ID=G-XYCVR3JZHP
```

`DRY_RUN=1 scripts/deploy_s3.sh` shows what would change and writes nothing.

---

## The full round refresh

Deploying is the last step of the refresh, not a separate job. When a round
has been played:

**1. Fetch the results** - usually nothing to do

`data-brasileirao-extractor` runs on GitHub Actions twice a day and publishes
to `s3://vmj-lake/app/football/` at stable keys. To force a run, trigger the
workflow in that repo. Série A only; the cup and Série B shards are refreshed
separately.

**2. Ingest what was published**

```bash
PYTHONPATH=src INGEST_AWS_PROFILE=<a profile that can read vmj-lake> \
  python3 -m brasileirao_simulator.entrypoints.ingest_fixtures
```

Reads the extractor's `manifest.json`, compares its `last_result_date` with
the local fixtures, and **does nothing if they match** - which is most runs,
and is what makes the refresh safe on a schedule. Otherwise it downloads the
published CSV, verifies the file says what the manifest said, replaces
`datasets/2026/fixtures.csv` and reshards into `competitions/71/2026.csv`.

Runs on the host, not in the container: the lake objects are private, the
container has no boto3, and the AWS CLI is here. `--dry-run` reports without
writing.

It refuses to go backwards. If the published date is older than the local
one, something is wrong upstream and overwriting would destroy results
already simulated on.

**3. Stop and ask before simulating.** Standing instruction: no Monte Carlo
runs without the user saying go.

**4. Simulate only the new dates**

```bash
docker-compose run --rm --entrypoint "" app \
  python brasileirao_simulator/entrypoints/topup_iterations.py \
  --seasons 2026 --model elo --target 20000
```

Computes each date's shortfall and runs exactly that. One new date at 20,000
iterations takes about ten seconds; dates already at target cost nothing.

**5. Export**

```bash
docker-compose run --rm --entrypoint "" app \
  python brasileirao_simulator/entrypoints/export_forecast_dataset.py \
  --seasons 2016,2017,2018,2019,2020,2021,2022,2023,2024,2025,2026 --model elo
```

**6. Deploy**

```bash
scripts/deploy_s3.sh
```

**7. The bolão page** is separate - it builds its own `EloAdapter`, so it moves
whenever the ratings do:

```bash
docker-compose run --rm --entrypoint "" app \
  python -m brasileirao_simulator.entrypoints.bolao_odds --iterations 20000
```

No bind mount: owners and doubles come out of the published bundle now, not
from CSVs in a sibling checkout.

Then splice `files/exports/bolao_odds.json` into the `<script id="data">` block
of `docs/artifacts/bolao.html` and republish that artifact.

---

## What the build produces

`scripts/build_site.sh` writes `site/` - the exact bytes S3 serves:

| file | what |
|---|---|
| `index.html` | Portuguese, 432 KB, self-contained |
| `en/index.html` | English, 432 KB |
| `og.png` | 1200x630 link-preview card, redrawn from the current data |

Caching is set on the sync itself (`--cache-control ... must-revalidate`),
not by a `_headers` file: that is a Cloudflare Pages convention and S3 would
serve it as an ordinary object that does nothing.

Portuguese is at the root because the readers are. `GA4_ID` is picked up from
the environment; without it the build carries no analytics tag at all, which
is what you want for a local look.

The card needs an SVG rasteriser. `rsvg-convert` is installed
(`brew install librsvg`); Inkscape and ImageMagick are tried as fallbacks.

---

## The infrastructure

Account `696228517302`, everything in `us-east-1`.

| | |
|---|---|
| Bucket | `databrasileirao-site` - private, versioned, readable only by the distribution |
| Distribution | `E2RZSW4J8OESEP` -> `d3mvc3eh4ssmka.cloudfront.net` |
| Certificate | ACM, apex + `www`, auto-renews (expires 2027-03-31) |
| Function | `databrasileirao-rewrite` - viewer-request, so `/en/` serves `/en/index.html` |
| Hosted zone | `Z10406981JY92WPZ1BOX2` - apex and `www` ALIAS to CloudFront |
| Registrar | registro.br, nameservers delegated to Route 53 |
| Analytics | GA4 `G-XYCVR3JZHP` |

Three decisions worth not undoing:

- **`PriceClass_All`.** The cheaper CloudFront price classes exclude South
  America, which is the entire audience.
- **The bucket is private.** CloudFront reads it through Origin Access
  Control; nothing is world-readable directly.
- **The origin is the REST endpoint, not the website endpoint.** That is why
  the rewrite function exists: the REST endpoint does not turn `/en/` into
  `/en/index.html` by itself.

---

## Credentials

The `db-provision` profile can do exactly two things: read and write objects
in `databrasileirao-site`, and create invalidations on `E2RZSW4J8OESEP`. It
cannot reach `vmj-lake`, other buckets, or any other service.

That is deliberate. The deploy runs unattended; it should hold only the rights
for the job it does.

**To change infrastructure** - the distribution, DNS, the certificate - attach
`scripts/iam-site-provision-policy.json` to that user in the IAM console, make
the change, then swap back to `scripts/iam-site-deploy-policy.json`. Ten
minutes of broader rights, not permanent ones.

Other credentials on this machine, for context: `macbook` (also deployed on a
Lightsail box for another project, read/write on `vmj-lake`) and `vmjulio-s3`
(what lean-pype uses). Neither should be used for the site.

---

## Verifying a deploy

```bash
curl -s https://databrasileirao.com.br/ | grep -o "<title>[^<]*</title>"
curl -s -o /dev/null -w "%{http_code}\n" https://databrasileirao.com.br/en/
```

The title must be `Data Brasileirão`. If it says `databrasileirao.com.br`,
something is serving a parking page - see below.

To check the data actually moved, look at the freshness line in the page:
*"Atualizado depois de cada rodada · última atualização em ..."*.

---

## When it goes wrong

**The page looks unchanged after a deploy.** The invalidation is the step
whose absence is silent. `deploy_s3.sh` always issues one; if you synced by
hand, you have to invalidate yourself.

**`AccessDenied` on ListBucket or PutObject.** The deploy ran under the wrong
credential. `.env.site` must be *sourced with export* - `scripts/deploy_s3.sh`
uses `set -a` for exactly this reason, because `aws` is a child process and
reads only the environment. Check with:

```bash
aws sts get-caller-identity --profile db-provision
```

**A resolver still shows the old site.** DNS caches outlive changes. Compare:

```bash
dig +short A databrasileirao.com.br @8.8.8.8
dig +short A databrasileirao.com.br @ns-582.awsdns-08.net
```

If Route 53's answer is right and the public one is not, it is cache and it
expires on its own. Test through the truth with
`curl --resolve databrasileirao.com.br:443:<ip>`.

**The certificate is about to expire.** ACM renews automatically as long as
the validation CNAMEs stay in the hosted zone. They are there; do not delete
the two `_<hash>.databrasileirao.com.br` CNAME records.

---

## If it all has to be rebuilt

Nothing here is precious. The bucket holds only derived files; the
distribution and zone are configuration. Rebuilding means: create the bucket,
request the certificate, create the OAC, function and distribution, point the
zone at it, and run `scripts/deploy_s3.sh`. The two policy files in `scripts/`
carry the permissions needed. The whole thing took about twenty minutes the
first time, most of it waiting for DNS.
