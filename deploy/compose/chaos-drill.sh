#!/usr/bin/env bash
# Chaos drill of BudgetGuard itself (plan W8.6; delivery reference §9 with the corrected redelivery
# row). Runs against the compose stack with signed deployment_protection_rule webhooks, the way
# GitHub sends them. Needs no GitHub App: the callback to GitHub fails (placeholder App key) after
# the decision is recorded, which is the order the gate guarantees.
#
#   bash deploy/compose/chaos-drill.sh          # inside WSL (Ubuntu-D), stack up, ~6 minutes
#
# GitHub-side behaviour (a failed delivery stays failed until the reconciler redelivers it) cannot be
# reproduced here; row 1 re-sends the same signed delivery, which is what a redelivery is.
set -uo pipefail
cd "$(dirname "$0")"
E=secrets/local.env
C=(docker compose --env-file .env.versions --env-file "$E" -f docker-compose.yml)
SECRET=$(sed -n 's/^BG_GITHUB_WEBHOOK_SECRET=//p' "$E")
DASH=$(sed -n 's/^BG_DASHBOARD_TOKEN=//p' "$E")
PG=$(sed -n 's/^PG_PASSWORD=//p' "$E")
VK=$(sed -n 's/^VALKEY_PASSWORD=//p' "$E")
REPO=${BG_GITHUB_REPO:-Rajarshi1-source/Budget_Guard}

psql_() { docker exec -e PGPASSWORD="$PG" budgetguard-timescaledb-1 psql -U bg -d budgetguard -tAc "$1"; }
vk() { docker exec budgetguard-valkey-1 valkey-cli --no-auth-warning -a "$VK" "$@"; }
ready() { for _ in $(seq 90); do [ "$(curl -s -o /dev/null -w '%{http_code}' localhost:8000/readyz)" = 200 ] && return; sleep 1; done; }
pg_up() { for _ in $(seq 90); do docker exec budgetguard-timescaledb-1 pg_isready -U bg -q && return; sleep 1; done; }
new_ids() { RUN=$(( $(date +%s%N) / 1000 % 1000000000 )); DEP=$(( RUN + 7 )); SHA=$(printf '%040x' "$RUN"); }

# deliver <service> <delivery-guid>: POST the signed body for the current RUN/DEP/SHA; prints "code seconds"
deliver() {
  local body sig
  body=$(printf '{"action":"requested","environment":"production-%s","installation":{"id":0},"repository":{"full_name":"%s"},"deployment":{"id":%d,"sha":"%s"},"deployment_callback_url":"https://api.github.com/repos/%s/actions/runs/%d/deployment_protection_rule"}' \
    "$1" "$REPO" "$DEP" "$SHA" "$REPO" "$RUN")
  sig="sha256=$(printf '%s' "$body" | openssl dgst -sha256 -hmac "$SECRET" -r | cut -d' ' -f1)"
  curl -s -o /dev/null -w '%{http_code} %{time_total}' --max-time 20 -X POST localhost:8000/v1/github/webhook \
    -H "Content-Type: application/json" -H "X-GitHub-Event: deployment_protection_rule" \
    -H "X-GitHub-Delivery: $2" -H "X-Hub-Signature-256: $sig" --data "$body"
}
rows() { psql_ "SELECT count(*) FROM decisions WHERE github_deployment_id = $DEP"; }
wait_row() { for _ in $(seq "${1:-30}"); do [ "$(rows 2>/dev/null)" = 1 ] && return 0; sleep 1; done; return 1; }
decision() { psql_ "SELECT id || ' ' || verdict || ' ' || reason_code || ' callback=' || coalesce(callback_state, '-')
                    FROM decisions WHERE github_deployment_id = $DEP"; }
replay() {
  local id; id=$(psql_ "SELECT id FROM decisions WHERE github_deployment_id = $DEP")
  curl -s -X POST -H "Authorization: Bearer $DASH" "localhost:8000/v1/decisions/$id/replay" \
    | python3 -c 'import json,sys; r=json.load(sys.stdin); print("replay matches" if r["matches"] else "REPLAY MISMATCH")'
}

echo "== 1. platform-api down during a deploy"
new_ids
"${C[@]}" stop platform-api >/dev/null 2>&1
echo "   delivery while down: $(deliver orders-service "chaos-api-$RUN")   (GitHub marks it failed; the job keeps waiting)"
"${C[@]}" start platform-api >/dev/null 2>&1; ready
echo "   redelivery (same guid): $(deliver orders-service "chaos-api-$RUN")"
wait_row 30
echo "   second redelivery (same guid): $(deliver orders-service "chaos-api-$RUN")"
echo "   new guid, same deployment id: $(deliver orders-service "chaos-api-$RUN-b")"
sleep 3
echo "   decisions for deployment $DEP: $(rows)   [$(decision)]"

echo "== 2. gate-worker dies mid-evaluation"
new_ids
"${C[@]}" stop gate-worker >/dev/null 2>&1
echo "   delivery: $(deliver orders-service "chaos-worker-$RUN")"
vk XREADGROUP GROUP gate-workers dead-worker COUNT 1 STREAMS bg:gate-evaluations '>' >/dev/null
echo "   pending on the dead consumer: $(vk XPENDING bg:gate-evaluations gate-workers | head -1)"
T0=$(date +%s)
"${C[@]}" start gate-worker >/dev/null 2>&1
wait_row 150 && echo "   adopted by XAUTOCLAIM after $(( $(date +%s) - T0 )) s; decisions: $(rows)   [$(decision)]" \
             || echo "   NOT adopted within 150 s"
sleep 2
echo "   still pending: $(vk XPENDING bg:gate-evaluations gate-workers | head -1)"
vk XGROUP DELCONSUMER bg:gate-evaluations gate-workers dead-worker >/dev/null

echo "== 3. TimescaleDB down"
new_ids
"${C[@]}" stop timescaledb >/dev/null 2>&1
echo "   delivery: $(deliver orders-service "chaos-db-$RUN")"
for _ in $(seq 30); do [ "$(vk XLEN bg:records-pending)" -ge 1 ] && break; sleep 1; done
echo "   buffered in bg:records-pending: $(vk XLEN bg:records-pending)"
"${C[@]}" start timescaledb >/dev/null 2>&1; pg_up
wait_row 90 && echo "   replayed after recovery; decisions: $(rows)   [$(decision)]; $(replay)" \
            || echo "   NOT replayed within 90 s"
echo "   left in bg:records-pending: $(vk XLEN bg:records-pending)"

echo "== 4. Valkey down"
new_ids
UP=$(deliver orders-service "chaos-vk-up-$RUN"); wait_row 30; BEFORE=$(decision | cut -d' ' -f2-3)
new_ids
"${C[@]}" stop valkey >/dev/null 2>&1
DOWN=$(deliver orders-service "chaos-vk-down-$RUN")
wait_row 30
echo "   with Valkey: ${UP#* }s -> $BEFORE"
echo "   without Valkey (inline): code ${DOWN% *}, ${DOWN#* }s -> $(decision | cut -d' ' -f2-3); decisions: $(rows)"
"${C[@]}" start valkey >/dev/null 2>&1
for _ in $(seq 30); do vk PING >/dev/null 2>&1 && break; sleep 1; done

echo "== 5. Prometheus down"
new_ids
"${C[@]}" stop prometheus >/dev/null 2>&1
echo "   delivery: $(deliver orders-service "chaos-prom-$RUN")"
wait_row 60
echo "   decision (orders is fail_open): $(decision); $(replay)"
"${C[@]}" start prometheus >/dev/null 2>&1

echo "== 6. snapshotter leader killed"
leader=""
for r in 1 2; do
  v=$(docker exec "budgetguard-snapshotter-$r" python -c "import urllib.request as u; print([l for l in u.urlopen('http://localhost:9464').read().decode().splitlines() if l.startswith('bg_snapshotter_leader')][0].split()[-1])" 2>/dev/null)
  [ "$v" = "1.0" ] && leader=$r
done
standby=$(( 3 - ${leader:-1} ))
echo "   leader: snapshotter-$leader"
docker kill "budgetguard-snapshotter-$leader" >/dev/null
for i in $(seq 120); do
  v=$(docker exec "budgetguard-snapshotter-$standby" python -c "import urllib.request as u; print([l for l in u.urlopen('http://localhost:9464').read().decode().splitlines() if l.startswith('bg_snapshotter_leader')][0].split()[-1])" 2>/dev/null)
  [ "$v" = "1.0" ] && { echo "   snapshotter-$standby took the lock after ${i} s"; break; }
  sleep 1
done
docker start "budgetguard-snapshotter-$leader" >/dev/null
sleep 75                                       # one snapshot cycle after the handover
echo "   5-minute buckets in the last hour (closed buckets only), per SLO:"
psql_ "SELECT v.name || '/' || s.name || ' ' || count(b.*) FROM slos s JOIN services v ON v.id = s.service_id
       LEFT JOIN sli_buckets b ON b.slo_id = s.id
        AND b.bucket_start >= date_trunc('hour', now()) - interval '1 hour'
        AND b.bucket_start <  date_trunc('hour', now())
       WHERE s.active GROUP BY v.name, s.name ORDER BY 1" | sed 's/^/     /'
