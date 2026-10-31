#!/usr/bin/env bash
# Secret rotation drill (plan W8.5, docs/runbooks/secret-rotation.md): rotate the dashboard token end
# to end on the compose stack with no failed request. Prints status codes only, never a token.
#
#   bash deploy/compose/rotation-drill.sh          # from the repo root, inside WSL (Ubuntu-D)
#
# At the end the new token is the only one in secrets/local.env; the old one is rejected.
set -euo pipefail
cd "$(dirname "$0")"
LOCAL=secrets/local.env
ROT=secrets/rotation.env
C=(docker compose --env-file .env.versions --env-file "$LOCAL" --env-file "$ROT" -f docker-compose.yml)
OLD=$(sed -n 's/^BG_DASHBOARD_TOKEN=//p' "$LOCAL")
NEW=$(openssl rand -hex 32)
[ -n "$OLD" ] || { echo "no BG_DASHBOARD_TOKEN in $LOCAL (run gen-secrets.sh)"; exit 1; }
umask 077

code() {        # HTTP status of the summary endpoint with a given bearer (empty = header with no token)
  curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $1" localhost:8000/v1/services/summary
}
dash() {        # "cards" only if the page streamed real service cards (a failed API call still returns 200)
  if curl -s localhost:3000/ | grep -q 'id="svc-orders-service"'; then echo cards; else echo error; fi
}
recreate() {
  "${C[@]}" up -d --no-deps --force-recreate platform-api dashboard >/dev/null 2>&1
  for _ in $(seq 60); do [ "$(curl -s -o /dev/null -w '%{http_code}' localhost:8000/readyz)" = 200 ] && break; sleep 1; done
  for _ in $(seq 60); do [ "$(dash)" = cards ] && break; sleep 1; done
}
row() { printf '%-44s old=%s new=%s empty=%s dashboard=%s\n' "$1" "$(code "$OLD")" "$(code "$NEW")" "$(code "")" "$(dash)"; }

: > "$ROT"
recreate
row "0. before (current=old)"

printf 'BG_DASHBOARD_TOKEN_NEXT=%s\n' "$NEW" > "$ROT"
recreate
row "1. API accepts next (current=old, next=new)"

printf 'BG_DASHBOARD_TOKEN=%s\nBG_DASHBOARD_TOKEN_NEXT=%s\n' "$NEW" "$OLD" > "$ROT"
recreate
row "2. dashboard switched (current=new, next=old)"

printf 'BG_DASHBOARD_TOKEN=%s\n' "$NEW" > "$ROT"
recreate
row "3. old retired (current=new)"

# Persist: the new token becomes the only one; the override file goes away.
sed -i "s/^BG_DASHBOARD_TOKEN=.*/BG_DASHBOARD_TOKEN=$NEW/" "$LOCAL"
: > "$ROT"
recreate
row "4. persisted in local.env"
rm -f "$ROT"
