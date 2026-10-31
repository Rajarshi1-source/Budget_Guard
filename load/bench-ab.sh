#!/usr/bin/env bash
# Scaling-decision evidence (plan W8.7, Rev 2 §11.2): the same merge storm with the gate reading the
# 30-day budget from 5-minute buckets (option C, the default) and from raw Prometheus counters
# (option A, BG_HISTORY_SOURCE=raw). Prints p50/p95/max verdict latency per option and the data size
# behind it, so the README quotes measured numbers with their context.
#
#   bash load/bench-prom.sh up                                     # a full 30-day window (recommended)
#   PROM=http://bg-bench-prom:9090 bash load/bench-ab.sh [merges=40] [rounds=3]
#   bash load/bench-prom.sh down
# Without PROM the gate workers use the stack's own Prometheus, with however much history it holds.
set -euo pipefail
cd "$(dirname "$0")/.."
MERGES=${1:-40}
ROUNDS=${2:-3}
PROM=${PROM:-http://prometheus:9090}
E=deploy/compose/secrets/local.env
C=(docker compose --env-file deploy/compose/.env.versions --env-file "$E" -f deploy/compose/docker-compose.yml)
SECRET=$(sed -n 's/^BG_GITHUB_WEBHOOK_SECRET=//p' "$E")
K6_TAG=$(sed -n 's/^K6_TAG=\([^ #]*\).*/\1/p' deploy/compose/.env.versions)
OUT=$(mktemp -d)

workers() {      # workers <history source>: recreate both gate workers against $PROM
  BG_PROMETHEUS_URL=$PROM BG_HISTORY_SOURCE=$1 "${C[@]}" up -d --no-deps --force-recreate gate-worker \
    >/dev/null 2>"$OUT/up.err" || { cat "$OUT/up.err"; exit 1; }
  local got
  got=$(docker exec budgetguard-gate-worker-1 sh -c 'echo "$BG_HISTORY_SOURCE $BG_PROMETHEUS_URL"')
  [ "$got" = "$1 $PROM" ] || { echo "gate-worker runs '$got', wanted '$1 $PROM'"; exit 1; }
}

for source in buckets raw; do
  workers "$source"
  sleep 8
  since=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  for r in $(seq "$ROUNDS"); do
    tag="storm-$source-$r-$(date +%s)"
    docker run --rm -i --network budgetguard_default -v "$PWD/load:/load:ro" "grafana/k6:$K6_TAG" run -q \
      -e SECRET="$SECRET" -e TAG="$tag" -e SEED="$(( $(date +%s) % 100000 * 1000 ))" \
      -e MERGES="$MERGES" -e VUS="$MERGES" /load/merge-storm.js >/dev/null
    for _ in $(seq 120); do                     # wait until every delivery of the round has a log line
      n=$("${C[@]}" logs --since "$since" gate-worker 2>/dev/null | grep -c "$tag-.*latency_ms=" || true)
      [ "$n" -ge "$MERGES" ] && break; sleep 1
    done
    sleep 20                                    # let the 15 s per-service locks drain between rounds
  done
  "${C[@]}" logs --since "$since" gate-worker 2>/dev/null | grep -o "storm-$source-[^ ]* -> .*latency_ms=[0-9]*" \
    > "$OUT/$source.log" || true
done
PROM=http://prometheus:9090 workers buckets       # back to the stack's defaults

# Data behind option A, read from the Prometheus the workers used.
query() {
  docker run --rm --network budgetguard_default --entrypoint promtool \
    "prom/prometheus:$(sed -n 's/^PROMETHEUS_TAG=\([^ #]*\).*/\1/p' deploy/compose/.env.versions)" \
    query instant "$PROM" "$1" 2>/dev/null | sed -n 's/.*=> \([0-9.e+]*\) @.*/\1/p' | head -1
}
SAMPLES=$(query 'sum(count_over_time(http_server_requests_seconds_count[30d]))')
SERIES=$(query 'count(http_server_requests_seconds_count)')
OLDEST=$(query 'time() - min(min_over_time(timestamp(http_server_requests_seconds_count)[30d:1h]))')

python3 - "$OUT" "$MERGES" "$ROUNDS" "${SAMPLES:-0}" "${SERIES:-0}" "${OLDEST:-0}" <<'PY' | tee "$OUT/result.txt"
import re, sys
out, merges, rounds = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
samples, series, oldest = float(sys.argv[4]), float(sys.argv[5]), float(sys.argv[6])
def pct(xs, p):
    xs = sorted(xs); k = (len(xs) - 1) * p; f = int(k)
    return xs[f] + (xs[min(f + 1, len(xs) - 1)] - xs[f]) * (k - f)
for source, label in (("buckets", "C (5-minute buckets)"), ("raw", "A (raw 30 d PromQL)")):
    ms = [int(m.group(1)) / 1000 for l in open(f"{out}/{source}.log")
          if (m := re.search(r"latency_ms=(\d+)", l))]
    if not ms:
        print(f"{label}: no samples")
        continue
    print(f"{label:22} n={len(ms):3}  p50={pct(ms, .5):5.2f}s  p95={pct(ms, .95):5.2f}s  max={max(ms):5.2f}s")
print(f"storm: {merges} concurrent merges x {rounds} rounds across 4 services; Prometheus window: "
      f"{oldest / 86400:.1f} days, {series:.0f} request-count series, {samples:,.0f} samples in the 30 d count window")
PY
rm -rf "$OUT"
