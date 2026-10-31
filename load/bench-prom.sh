#!/usr/bin/env bash
# A throwaway Prometheus for load/bench-ab.sh holding a FULL 30-day window (plan W8.7). Option A's cost
# grows with the samples in the window: a local stack with hours of history would make A look
# free. This one gets 30 days of synthetic 15 s counters (backfilled with promtool) for exactly the
# series the gate's 30-day queries select. It scrapes the same targets with the same rules, so the
# live signals match the main Prometheus. The main Prometheus and its data are not touched.
#
#   bash load/bench-prom.sh up      # ~5 minutes; then: PROM=http://bg-bench-prom:9090 bash load/bench-ab.sh
#   bash load/bench-prom.sh down
set -euo pipefail
cd "$(dirname "$0")/.."
TAG=$(sed -n 's/^PROMETHEUS_TAG=\([^ #]*\).*/\1/p' deploy/compose/.env.versions)
WORK=$HOME/.cache/bg-bench-prom        # on disk: the file is ~2 GB, and WSL's /tmp is RAM (tmpfs)

if [ "${1:-up}" = down ]; then
  docker rm -f bg-bench-prom >/dev/null 2>&1 || true
  docker volume rm bg-bench-prom >/dev/null 2>&1 || true
  rm -rf "$WORK"; echo "removed"; exit 0
fi

rm -rf "$WORK"; mkdir -p "$WORK"
echo "== synthetic 30 d of the series the gate reads (same labels and current rates as the live ones)"
python3 - "$WORK/backfill.om" <<'PY'
import json, sys, time, urllib.parse, urllib.request

LIVE = "http://localhost:9090/api/v1/"
def get(path, **params):
    return json.load(urllib.request.urlopen(LIVE + path + "?" + urllib.parse.urlencode(params, doseq=True)))["data"]

selectors = ['http_server_requests_seconds_count', 'http_server_requests_seconds_bucket{le=~"0.3|0.5|0.8"}',
             'notification_delivery_seconds_count', 'notification_delivery_seconds_bucket{le="120.0"}']
series = [s for sel in selectors for s in get("series", **{"match[]": sel})]
STEP, END = 15, int(time.time()) - 3 * 3600          # stop 3 h ago: the live head fills the rest
START = END - 30 * 86400
n = 0
with open(sys.argv[1], "w") as out:
    for family in sorted({s["__name__"] for s in series}):
        out.write(f"# TYPE {family} unknown\n")
        for s in (x for x in series if x["__name__"] == family):
            labels = {k: v for k, v in s.items() if k != "__name__"}
            sel = family + "{" + ",".join(f'{k}="{v}"' for k, v in sorted(labels.items())) + "}"
            r = get("query", query=f"rate({sel}[1h])")
            rate = max(float(r["result"][0]["value"][1]) if r["result"] else 0.0, 0.01)
            lab = "{" + ",".join(f'{k}="{v}"' for k, v in sorted(labels.items())) + "}"
            value, rows = 0.0, []
            for t in range(START, END, STEP):
                value += rate * STEP
                rows.append(f"{family}{lab} {value:.3f} {t}\n")
            out.writelines(rows)
            n += len(rows)
    out.write("# EOF\n")
print(f"{len(series)} series, {n:,} samples ({n // len(series):,} per series)")
PY

echo "== promtool tsdb create-blocks-from openmetrics"
docker volume rm bg-bench-prom >/dev/null 2>&1 || true
docker volume create bg-bench-prom >/dev/null
docker run --rm -u 0 -v "$WORK:/in:ro" -v bg-bench-prom:/prometheus --entrypoint promtool "prom/prometheus:$TAG" \
  tsdb create-blocks-from openmetrics --max-block-duration=24h /in/backfill.om /prometheus 2>&1 | tail -2
docker run --rm -u 0 -v bg-bench-prom:/prometheus --entrypoint chown "prom/prometheus:$TAG" -R 65534:65534 /prometheus
rm -f "$WORK/backfill.om"

echo "== start bg-bench-prom (same scrape config and generated rules, same query limits)"
docker rm -f bg-bench-prom >/dev/null 2>&1 || true
docker run -d --name bg-bench-prom --network budgetguard_default \
  -v "$PWD/deploy/compose/prometheus.yml:/etc/prometheus/prometheus.yml:ro" \
  -v "$PWD/rules/generated:/etc/prometheus/rules:ro" -v bg-bench-prom:/prometheus \
  "prom/prometheus:$TAG" --config.file=/etc/prometheus/prometheus.yml \
  --storage.tsdb.path=/prometheus --storage.tsdb.retention.time=35d \
  --query.timeout=30s --query.max-concurrency=10 --query.max-samples=30000000 >/dev/null
for _ in $(seq 60); do
  docker run --rm --network budgetguard_default curlimages/curl:8.11.1 -sf http://bg-bench-prom:9090/-/ready >/dev/null 2>&1 && break
  sleep 2
done
echo "ready: http://bg-bench-prom:9090 (on the compose network)"
