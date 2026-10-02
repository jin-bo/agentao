#!/usr/bin/env bash
# Pre-fill a monthly metrics snapshot (docs/design/path-a-roadmap.md §15).
#
# Prints the §15.2 template with the "Now" column filled from the §15.1
# sources. The "Δ vs last month" and "Notes" columns stay manual — §15.4:
# that is where judgment lives. Needs curl, python3 and an authenticated gh.
#
#   scripts/metrics_snapshot.sh > docs/dev-notes/metrics/$(date +%Y-%m).md
#
# Two deviations from the §15.1 commands, both because the original source
# stopped answering usefully:
# - Weekly downloads: the last 7 complete days from pypistats `overall`
#   (without mirrors). `recent` rate-limits (429) and does not say which
#   days it covers.
# - pyproject.toml hits: GitHub code search. grep.app's API returned
#   non-JSON on 2026-10-01. Hits in jin-bo/* are counted apart: they are
#   this project's own repos, not adoption. Code search matches substrings
#   (a package named `agentaos` counts), so open each "other" hit by hand.
set -euo pipefail

REPO=jin-bo/agentao
OWNER=jin-bo
MONTH=$(date +%Y-%m)
SINCE=$(python3 -c 'import datetime as d; print(d.date.today()-d.timedelta(days=30))')

weekly=$(curl -sf 'https://pypistats.org/api/packages/agentao/overall?mirrors=false' | python3 -c '
import json, sys, datetime as d
rows = {r["date"]: r["downloads"] for r in json.load(sys.stdin)["data"]
        if r["category"] == "without_mirrors"}
end = d.date.today() - d.timedelta(days=1)
print(sum(rows.get(str(end - d.timedelta(days=i)), 0) for i in range(7)))
' || echo "?")

dependents=$(curl -sL "https://github.com/$REPO/network/dependents" \
  | grep -oE '[0-9,]+ +(Repositories|Packages)' | sort -u | tr '\n' ' ' || true)

pyproject=$(gh api -X GET search/code -f q='agentao filename:pyproject.toml' -f per_page=100 \
  --jq "[.items[].repository.full_name] | unique | map(select(. != \"$REPO\"))
        | {own: map(select(startswith(\"$OWNER/\"))), other: map(select(startswith(\"$OWNER/\") | not))}
        | \"\(.other | length) other, unverified (\(.other | join(\", \"))); \(.own | length) own (\(.own | join(\", \")))\"" || echo "?")

issues=$(gh issue list --repo "$REPO" --state all --limit 200 --search "created:>$SINCE" \
  --json author --jq "[.[] | select(.author.login != \"$OWNER\")] | length" || echo "?")

breaks=$(git log --since="30 days ago" --oneline -- agentao/host/ | grep -ciE 'breaking|break:|!:' || true)

stars=$(gh api repos/$REPO --jq '.stargazers_count' || echo "?")

ci=$(gh run list --repo "$REPO" --workflow ci.yml --branch main --event push --limit 1 \
  --json conclusion,headSha --jq '.[0] | "\(.conclusion) @ \(.headSha[:7])"' || echo "?")

cat <<EOF
# Metrics snapshot — $MONTH

Collected $(date +%Y-%m-%d) by \`scripts/metrics_snapshot.sh\`.

| Metric | Target M+6 | Target M+12 | Now | Δ vs last month |
|---|---:|---:|---:|---:|
| PyPI weekly downloads (last 7 full days, no mirrors) | 500 | 2000 | $weekly | __ |
| GitHub dependents (repos + packages) | 3 | 15 | ${dependents:-?} | __ |
| pyproject.toml hits (GitHub code search) | 5 | 30 | $pyproject | __ |
| Embed:CLI issue ratio (30d) | ≥1:1 | ≥2:1 | __:__ ($issues issues by others; classify by hand) | __ |
| Public-API breaks (30d) | 0 | 0 | $breaks | __ |
| Example mypy strict pass rate | 100% | 100% | not measured (CI runs mypy on agentao.host only; main CI: $ci) | __ |

Stars: $stars (anti-metric input, §2.2).

## Notes
- Lighthouse status:
- Outreach:
- Anomalies:
EOF
