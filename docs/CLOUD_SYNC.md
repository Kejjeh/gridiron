# Sleeper cloud sync

This workflow runs the reviewed GET-only Sleeper sync on GitHub-hosted Linux.
The desktop can be turned off. It never launches a desktop console and does
not use Claude or Codex tokens. No new Python dependencies are required for
this narrow sync path. The verified league identifier in league_config is
used; no local .env or private desktop cache is uploaded.

## Activation and cost gate

The workflow must reach main before GitHub will schedule it. It currently
requests hourly runs at minute 17, approved by the owner on September 19.
The job remains disabled until repository variable GRIDIRON_CLOUD_SYNC_ENABLED
is exactly true. On a PUBLIC repository it additionally needs
GRIDIRON_PUBLIC_PUBLICATION exactly true — the explicit publication opt-in
the owner gave on 2026-09-21 (docs/DECISIONS.md). Without it a public
repository runs nothing, which is the fail-safe that stopped both workflows
the moment visibility flipped. Before activation, verify the GitHub Actions
budget stops usage at the included allowance (no paid overages). Do not assume
the workflow timeout enforces a billing cap; it only bounds a single job.
Manual dispatch has the same gates.

Every five minutes is 288 scheduled runs/day, approximately 8,640/30 days.
Hourly is 24/day, approximately 720/30 days. These are run counts, not a billing
quote: runner time and account plan determine actual usage. Scheduled runs can
be delayed or dropped under load; neither cadence is a real-time guarantee.

## Data and failure behavior

Each run starts with an empty runner-local cache. Existing snapshot validation,
rollover checks, settings-drift reporting and read-window metadata are reused.
Only a successful sync uploads sleeper-snapshot, with seven-day retention.
Failed runs upload nothing and cannot replace artifacts from earlier successful
runs. For last-good data select the newest completed successful run with a
non-expired sleeper-snapshot artifact; never call an old artifact fresh simply
because it is available. If every retained artifact has expired, report no
available snapshot rather than inventing a fallback.

The repository is public, so the sleeper-snapshot artifact — every roster in
the league, as Sleeper serves them from its open API — is downloadable by any
signed-in GitHub user while it is retained; so are the dashboard artifact, the
carry cache and every run log. The owner authorised that. No roster data is
printed in the summary or committed to Git. Artifact storage uses the account's allowance.
No overlapping cloud jobs are allowed. The Windows scheduled task remains off.
Runner-local sync counters restart every run; use Actions run history for
cloud health. A failed run is a failure, not a successful stale publication.
The owner may need to renew league settings/season configuration next season;
validation fails closed on mismatch.

## Viewing and consuming results

Open Actions > Sleeper cloud sync > latest successful run > sleeper-snapshot.
The artifact contains the season folder with a manifest, the referenced
immutable snapshot and sync state. Check the manifest's as_of before use.
Download from a laptop or any authenticated device without the desktop running.

This collects cloud snapshots. The dashboard workflow consumes them hourly
(best effort) and, under the same publication opt-in, publishes exactly two
HTML pages to GitHub Pages at https://kejjeh.github.io/gridiron/ (root opens
Game Day; the pages link to each other). Snapshot timestamps are preserved:
the packager copies bytes and restamps nothing. GitHub Pages from a PRIVATE
repository needs a paid plan (Pro, Team or Enterprise); on Free the repository
must be public, which it now is.

## Verification

Before release:

    python scripts/ci/smoke.py
    python scripts/ci/run_summary.py -- python -m pytest

After activation: manually dispatch once on main, require a successful run and
its artifact, inspect manifest freshness without printing roster contents,
then confirm a scheduled run. Disable GRIDIRON_CLOUD_SYNC_ENABLED to stop future
jobs. Do not re-enable the desktop task as a fallback.
