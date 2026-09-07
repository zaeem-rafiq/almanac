#!/bin/bash
# A-06 deferred proof 3 — compare `scan --source youtube` against `scan --source corpus`.
#
# Exists because the comparison is quota-blocked, not code-blocked: seeding the test channel
# costs 10,000 units (videos.insert is 1600, see ADR-000 §9) which is the entire daily
# allocation, so the scan has to wait for the reset at 00:00 PT.
#
# Deliberately conservative. The risk in an unattended retry is burning a fresh day's quota on
# a scan nobody is watching, so this:
#   * probes cheaply (1 unit) and only spends the 1253-unit scan when three probes in a row pass
#   * runs the scan exactly ONCE — there is no retry loop around the expensive call
#   * writes the honest verdict, PASS or FAIL, and records both count dictionaries either way
#   * does NOT commit or push; an unattended job that edits a proof file is one thing, an
#     unattended job that pushes to main is another
#
# Re-runnable at any time. It probes first and only sleeps if the quota is actually exhausted,
# so running it at 02:00 after a reset does the work immediately instead of waiting a day.
set -u
cd "$(dirname "$0")/.." || exit 1
mkdir -p reports
exec >>reports/proof3-run.log 2>&1
echo "=== started $(date '+%F %T %Z') ==="

probe_ok() {   # three cheap calls; all must pass
  local ok=0
  for _ in 1 2 3; do
    venv/bin/python -c "
import sys; sys.path.insert(0,'.')
from almanac.cli import load_env; load_env()
from almanac.youtube import build_client
build_client().channels().list(part='id', mine=True).execute()
" 2>/dev/null && ok=$((ok+1))
    sleep 10
  done
  echo "$(date '+%T') probes: $ok/3"
  [ "$ok" -eq 3 ]
}

seconds_until_reset() {   # next 00:05 PT strictly in the future
  local now target
  now=$(date '+%s')
  target=$(TZ=America/Los_Angeles date -v0H -v5M -v0S '+%s')
  [ "$target" -le "$now" ] && target=$(TZ=America/Los_Angeles date -v+1d -v0H -v5M -v0S '+%s')
  echo $(( target - now ))
}

if ! probe_ok; then
  s=$(seconds_until_reset)
  echo "quota exhausted; sleeping ${s}s until the reset"
  sleep "$s"
  for attempt in 1 2 3 4 5 6; do
    probe_ok && break
    [ "$attempt" -eq 6 ] && { echo "quota never came clean; giving up WITHOUT spending a scan"; exit 1; }
    echo "attempt $attempt failed; waiting 15m"
    sleep 900
  done
fi

echo "$(date '+%T') running scan --source youtube"
venv/bin/python -m almanac scan --source youtube --out reports/youtube --no-notes || {
  echo "scan failed; NOT writing a proof line"; exit 1; }

venv/bin/python - <<'PY'
import json, pathlib, datetime
c = json.load(open('reports/corpus/latest.json'))
y = json.load(open('reports/youtube/latest.json'))
verdict = 'PASS' if c['counts'] == y['counts'] else 'FAIL'
line = ("PROOF A-06: scan --source youtube status counts == scan --source corpus status "
        f"counts = {verdict}")
detail = (f"  corpus  ({len(c['videos'])} videos): {json.dumps(c['counts'], sort_keys=True)}\n"
          f"  youtube ({len(y['videos'])} videos): {json.dumps(y['counts'], sort_keys=True)}\n"
          f"  corpus source_ids : {sorted(v['source_id'] for v in c['videos'])[:2]}\n"
          f"  youtube source_ids: {sorted(v['source_id'] for v in y['videos'])[:2]}")
print(detail); print(line)
stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
with open('docs/proofs/A-06.md', 'a', encoding='utf-8') as f:
    f.write(f"\n## Deferred proof 3, run {stamp}\n\n```\n{detail}\n{line}\n```\n")
pathlib.Path('reports/proof3-result.txt').write_text(detail + "\n" + line + "\n")
PY
echo "=== finished $(date '+%F %T %Z') ==="
