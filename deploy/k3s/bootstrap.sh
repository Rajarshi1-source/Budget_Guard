#!/usr/bin/env bash
# Public deploy on one Oracle Cloud A1 (ARM64) VM (plan W5.10). Run on the VM from the repo root as a
# user with sudo. Idempotent: re-running converges. Secrets come from deploy/k3s/.env.k3s (git-ignored;
# copy deploy/k3s/env.k3s.example) and never enter the repo, the charts or the shell history.
#
#   deploy/k3s/bootstrap.sh <platform-digest> <dashboard-digest>
#
# Pins: k3s v1.36.5+k3s1, Argo Rollouts v1.10.1, kube-prometheus-stack 92.10.1, cosign v3 (docs/decisions.md).
set -euo pipefail
cd "$(dirname "$0")/../.."
PLATFORM_DIGEST=${1:?platform image digest (sha256:...) from build-push.yml}
DASHBOARD_DIGEST=${2:?dashboard image digest}
ENV_FILE=deploy/k3s/.env.k3s
[ -f "$ENV_FILE" ] || { echo "missing $ENV_FILE (see deploy/k3s/env.k3s.example)"; exit 1; }
set -a; . "$ENV_FILE"; set +a
: "${DOCKERHUB_NS:?}" "${GITHUB_REPOSITORY:?}" "${PUBLIC_URL:?}" "${CF_CREDENTIALS_FILE:?}"

K3S_VERSION=v1.36.5+k3s1
ROLLOUTS_VERSION=v1.10.1
KPS_VERSION=92.10.1
k() { sudo k3s kubectl "$@"; }
secret() {   # secret <namespace> <name> KEY=VALUE...
  # Each value goes to a file in a private temp dir (multi-line values such as the App key survive,
  # and no value appears on a process command line), then kubectl reads the directory.
  local ns=$1 name=$2 dir; shift 2
  dir=$(mktemp -d); chmod 700 "$dir"
  for kv in "$@"; do printf '%s' "${kv#*=}" > "$dir/${kv%%=*}"; done
  k create secret generic "$name" -n "$ns" --from-file="$dir" --dry-run=client -o yaml | k apply -f - >/dev/null
  rm -rf "$dir"
}

echo "== 1. k3s (Traefik v3 bundled; no ServiceLB: the tunnel is the only way in)"
if ! command -v k3s >/dev/null || ! k3s --version | grep -q "${K3S_VERSION%%+*}"; then
  curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION="$K3S_VERSION" sh -s - \
    --write-kubeconfig-mode 600 --disable servicelb
fi
for ns in bg-apps bg-platform monitoring argo-rollouts data edge; do
  k create namespace "$ns" --dry-run=client -o yaml | k apply -f - >/dev/null
done

echo "== 2. internal Traefik entrypoint + *.bg-apps.internal DNS (canary weights for service-to-service calls)"
k apply -f deploy/k3s/traefik-internal.yaml
k -n kube-system rollout restart deployment coredns >/dev/null

echo "== 3. Argo Rollouts $ROLLOUTS_VERSION"
k apply -n argo-rollouts --server-side -f \
  "https://github.com/argoproj/argo-rollouts/releases/download/$ROLLOUTS_VERSION/install.yaml" >/dev/null

echo "== 4. kube-prometheus-stack $KPS_VERSION (35 d retention, the Alertmanager tree) + generated rules"
secret monitoring budgetguard-am "slack_page_url=$SLACK_PAGE_URL" "slack_ticket_url=$SLACK_TICKET_URL" \
  "bg_token=$BG_ALERTMANAGER_TOKEN"
sudo helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null 2>&1 || true
sudo helm upgrade --install kube-prometheus-stack prometheus-community/kube-prometheus-stack \
  --version "$KPS_VERSION" -n monitoring -f deploy/k3s/kps-values.yaml \
  --kubeconfig /etc/rancher/k3s/k3s.yaml --wait >/dev/null
k apply -f deploy/k3s/rules/                      # PrometheusRule CRs from `bg generate --format prometheus-operator`

echo "== 5. data: TimescaleDB + Valkey (password), reachable from bg-platform only"
secret data budgetguard-data "PG_PASSWORD=$PG_PASSWORD" "VALKEY_PASSWORD=$VALKEY_PASSWORD"
k apply -f deploy/k3s/data.yaml
k -n data rollout status statefulset/timescaledb --timeout 5m
k -n data rollout status statefulset/valkey --timeout 2m

echo "== 6. platform secrets (one database role per process) and Grafana provisioning"
DB=timescaledb.data:5432/budgetguard
secret bg-platform budgetguard-platform \
  "BG_DATABASE_URL=postgresql+asyncpg://bg_app:$BG_DB_APP_PASSWORD@$DB" \
  "BG_VALKEY_URL=redis://:$VALKEY_PASSWORD@valkey.data:6379/0" \
  "BG_PROMETHEUS_URL=http://kube-prometheus-stack-prometheus.monitoring:9090" \
  "BG_GITHUB_REPO=$GITHUB_REPOSITORY" "BG_GITHUB_REPO_ID=$BG_GITHUB_REPO_ID" \
  "BG_GITHUB_OWNER_ID=$BG_GITHUB_OWNER_ID" "BG_GITHUB_INSTALLATION_ID=$BG_GITHUB_INSTALLATION_ID" \
  "BG_GITHUB_APP_ID=$BG_GITHUB_APP_ID" "BG_GITHUB_APP_PRIVATE_KEY=$(cat "$BG_GITHUB_APP_PRIVATE_KEY_FILE")" \
  "BG_GITHUB_WEBHOOK_SECRET=$BG_GITHUB_WEBHOOK_SECRET" "BG_ALERTMANAGER_TOKEN=$BG_ALERTMANAGER_TOKEN" \
  "BG_DASHBOARD_TOKEN=$BG_DASHBOARD_TOKEN" "BG_APPROVER_IDS=$BG_APPROVER_IDS"
# compose's owner is a superuser; in k3s the migrator is the database owner role (POSTGRES_USER)
secret bg-platform budgetguard-migrate \
  "BG_DATABASE_URL=postgresql+asyncpg://bg:$PG_PASSWORD@$DB" \
  "BG_DB_APP_PASSWORD=$BG_DB_APP_PASSWORD" "BG_DB_GRAFANA_PASSWORD=$BG_DB_GRAFANA_PASSWORD" \
  "BG_DB_AUTH_PASSWORD=$BG_DB_AUTH_PASSWORD"
secret bg-platform budgetguard-dashboard \
  "BUDGETGUARD_DASHBOARD_TOKEN=$BG_DASHBOARD_TOKEN" "BETTER_AUTH_SECRET=$BETTER_AUTH_SECRET" \
  "AUTH_DATABASE_URL=postgresql://bg_auth:$BG_DB_AUTH_PASSWORD@$DB" \
  "GITHUB_CLIENT_ID=$GITHUB_CLIENT_ID" "GITHUB_CLIENT_SECRET=$GITHUB_CLIENT_SECRET"
secret bg-platform budgetguard-grafana \
  "BG_DB_GRAFANA_PASSWORD=$BG_DB_GRAFANA_PASSWORD" "GF_SECURITY_ADMIN_PASSWORD=$GRAFANA_ADMIN_PASSWORD"
for cm in "grafana-datasources grafana/provisioning/datasources" "grafana-providers grafana/provisioning/dashboards" \
          "grafana-dashboards grafana/dashboards"; do
  set -- $cm
  k create configmap "$1" -n bg-platform --from-file="$2" --dry-run=client -o yaml | k apply -f - >/dev/null
done

echo "== 7. verify signatures, then install the platform chart (migrate hook runs first)"
for pair in "platform@$PLATFORM_DIGEST" "dashboard@$DASHBOARD_DIGEST"; do
  cosign verify "$DOCKERHUB_NS/$pair" \
    --certificate-oidc-issuer https://token.actions.githubusercontent.com \
    --certificate-identity-regexp "^https://github.com/$GITHUB_REPOSITORY/.github/workflows/build-push.yml@refs/heads/main$" \
    >/dev/null
done
sudo helm upgrade --install bg-platform deploy/helm/platform -n bg-platform \
  --kubeconfig /etc/rancher/k3s/k3s.yaml --wait --timeout 10m \
  --set image.repository="$DOCKERHUB_NS/platform" --set image.digest="$PLATFORM_DIGEST" \
  --set dashboardImage.repository="$DOCKERHUB_NS/dashboard" --set dashboardImage.digest="$DASHBOARD_DIGEST" \
  --set publicUrl="$PUBLIC_URL"

echo "== 8. bg-apps: NetworkPolicy and the deployer (the only credential the deploy job gets)"
k apply -f deploy/k3s/bg-apps-networkpolicy.yaml -f deploy/k3s/rbac-deployer.yaml
TOKEN=$(k -n bg-apps get secret deployer-token -o jsonpath='{.data.token}' | base64 -d)
CA=$(k -n bg-apps get secret deployer-token -o jsonpath='{.data.ca\.crt}')
umask 077
cat > kubeconfig-deployer.yaml <<EOF
apiVersion: v1
kind: Config
clusters: [{ name: bg, cluster: { server: "${KUBE_API_URL:?public URL of the k3s API for GitHub runners}", certificate-authority-data: "$CA" } }]
users: [{ name: deployer, user: { token: "$TOKEN" } }]
contexts: [{ name: deployer, context: { cluster: bg, user: deployer, namespace: bg-apps } }]
current-context: deployer
EOF
echo "   wrote ./kubeconfig-deployer.yaml (mode 600): feed it to deploy/k3s/github-setup.sh, then delete it"

echo "== 9. Cloudflare Tunnel"
k create secret generic cloudflared-credentials -n edge --from-file=credentials.json="$CF_CREDENTIALS_FILE" \
  --dry-run=client -o yaml | k apply -f - >/dev/null
k apply -f deploy/k3s/cloudflared.yaml
k -n edge rollout status deployment/cloudflared --timeout 2m

cat <<'EOF'
== 10. manual, in the Cloudflare dashboard (needs your account):
   - DNS: api., grafana. and the apex as CNAMEs to the tunnel (cloudflared tunnel route dns budgetguard <host>)
   - Security -> WAF -> Rate limiting rules (edge rate limits, security-and-api.md T-9):
       api.<domain> /v1/github/webhook   120 requests / 1 min per IP -> block
       api.<domain> /v1/decisions          60 requests / 1 min per IP -> block
       <domain> (dashboard)               300 requests / 1 min per IP -> managed challenge
EOF
