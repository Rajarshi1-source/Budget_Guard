#!/usr/bin/env bash
# D-7 evidence (plan W8.1): does a "5%" canary step carry ~5% of requests? Runs the real service chart
# on a throwaway k3s (Traefik v3 bundled, as on the VM) with Argo Rollouts, the internal Traefik
# entrypoint and the CoreDNS rewrite from deploy/k3s/traefik-internal.yaml. The workload is
# traefik/whoami (it names the pod that answered), so the share is counted per pod, not estimated.
#
# Compared on the same pods at the same moment:
#   via Traefik   http://orders.bg-apps.internal   (how services call each other in k3s)
#   per replica   a plain Service over every pod   (what a canary without a traffic router gets)
#
#   bash load/canary-share.sh [requests=2000]      # inside WSL (Ubuntu-D); ~6 minutes; cleans up
#   ROLLOUTS_YAML=... PODMONITOR_CRD=... use local copies of the two manifests (offline)
set -euo pipefail
cd "$(dirname "$0")/.."
N=${1:-2000}
K3S=rancher/k3s:v1.36.5-k3s1                    # docs/decisions.md pin (W5)
ROLLOUTS=v1.10.1
PO_CRDS=v0.86.2                                 # prometheus-operator CRDs: only PodMonitor is needed here
CURL=curlimages/curl:8.11.1
export PATH="$HOME/.local/bin:$PATH" KUBECONFIG=/tmp/bg-k3s.yaml
k() { docker exec -i bg-k3s kubectl "$@"; }
until_ok() { local n=$1; shift; for _ in $(seq "$n"); do "$@" >/dev/null 2>&1 && return 0; sleep 2; done; echo "timed out: $*"; return 1; }
digest() { docker pull -q "$1" >/dev/null && docker inspect --format '{{index .RepoDigests 0}}' "$1" | cut -d@ -f2; }
cleanup() { docker rm -f bg-k3s >/dev/null 2>&1 || true; rm -f "$KUBECONFIG"; }
trap cleanup EXIT

STABLE=$(digest traefik/whoami:v1.10.4)
CANARY=$(digest traefik/whoami:v1.11.0)

echo "== k3s $K3S"
docker rm -f bg-k3s >/dev/null 2>&1 || true
docker run -d --name bg-k3s --privileged --tmpfs /run --tmpfs /var/run -p 127.0.0.1:16443:6443 "$K3S" \
  server --disable=servicelb --disable=metrics-server --write-kubeconfig-mode=644 >/dev/null
until_ok 90 k get --raw /readyz
until_ok 150 k -n kube-system rollout status deploy/traefik --timeout=5s
docker exec bg-k3s cat /etc/rancher/k3s/k3s.yaml | sed 's#127.0.0.1:6443#127.0.0.1:16443#' > "$KUBECONFIG"

echo "== internal entrypoint, CoreDNS rewrite, Argo Rollouts $ROLLOUTS"
k apply -f - < deploy/k3s/traefik-internal.yaml >/dev/null
k -n kube-system rollout restart deploy/coredns >/dev/null
k create namespace argo-rollouts >/dev/null; k create namespace bg-apps >/dev/null
fetch() {        # fetch <local override> <url>: a local copy wins (offline runs), else download with retries
  if [ -n "$1" ] && [ -f "$1" ]; then cat "$1"
  else curl -sSfL --retry 5 --retry-all-errors --connect-timeout 20 "$2"; fi
}
# The tag's manifests/install.yaml is the release asset with the image left at :latest: pin it.
fetch "${ROLLOUTS_YAML:-}" "https://raw.githubusercontent.com/argoproj/argo-rollouts/$ROLLOUTS/manifests/install.yaml" \
  | sed "s#quay.io/argoproj/argo-rollouts:latest#quay.io/argoproj/argo-rollouts:$ROLLOUTS#" \
  | k apply -n argo-rollouts --server-side -f - >/dev/null
fetch "${PODMONITOR_CRD:-}" "https://raw.githubusercontent.com/prometheus-operator/prometheus-operator/$PO_CRDS/example/prometheus-operator-crd/monitoring.coreos.com_podmonitors.yaml" \
  | k apply --server-side -f - >/dev/null
until_ok 150 k -n argo-rollouts rollout status deploy/argo-rollouts --timeout=5s
until_ok 90 sh -c "docker exec bg-k3s kubectl -n kube-system get deploy traefik -o yaml | grep -q 'containerPort: 8081'"
until_ok 90 k -n kube-system rollout status deploy/traefik --timeout=5s
until_ok 60 k -n kube-system rollout status deploy/coredns --timeout=5s

release() {      # release <digest> <sha>: the same helm call deploy.yml makes, with whoami as the image
  helm upgrade --install orders-service deploy/helm/service -n bg-apps --server-side=false \
    -f deploy/helm/service/values-orders-service.yaml --set service=orders-service \
    --set image.repository=traefik/whoami --set image.digest="$1" --set-string release.sha="$2" \
    --set env.WHOAMI_PORT_NUMBER=8080 --set-json 'canary.weights=[5,25,50,100]' --set canary.bake=10m >/dev/null
}
echo "== stable release (4 replicas)"
release "$STABLE" "$(printf 'a%.0s' $(seq 40))"
until_ok 120 sh -c "docker exec bg-k3s kubectl -n bg-apps get rollout orders-service -o jsonpath='{.status.phase}' | grep -qx Healthy"

echo "== canary release: profile [5, 25, 50, 100], first step setWeight 5"
release "$CANARY" "$(printf 'b%.0s' $(seq 40))"
until_ok 90 sh -c "docker exec bg-k3s kubectl -n bg-apps get rollout orders-service -o jsonpath='{.status.availableReplicas}' | grep -qx 5"
until_ok 60 sh -c "docker exec bg-k3s kubectl -n bg-apps get traefikservice orders-service-weighted -o jsonpath='{.spec.weighted.services[1].weight}' | grep -qx 5"
k -n bg-apps create service clusterip orders-all --tcp=80:8080 >/dev/null
k -n bg-apps patch service orders-all -p '{"spec":{"selector":{"app":"orders-service"}}}' >/dev/null

CANARY_HASH=$(k -n bg-apps get rollout orders-service -o jsonpath='{.status.currentPodHash}')
echo "   weights in the TraefikService: $(k -n bg-apps get traefikservice orders-service-weighted \
  -o jsonpath='{range .spec.weighted.services[*]}{.name}={.weight} {end}')"
echo "   pods: $(k -n bg-apps get pods -l app=orders-service --no-headers | wc -l) total," \
     "$(k -n bg-apps get pods -l "app=orders-service,rollouts-pod-template-hash=$CANARY_HASH" --no-headers | wc -l) canary"

count() {        # count <url>: share of N requests answered by canary pods
  k -n bg-apps delete pod probe --ignore-not-found >/dev/null
  k -n bg-apps run probe --image="$CURL" --restart=Never --quiet --command -- sh -c \
    "for i in \$(seq $N); do curl -s --max-time 2 $1 | sed -n 's/^Hostname: //p'; done" >/dev/null
  until_ok 300 sh -c "docker exec bg-k3s kubectl -n bg-apps get pod probe -o jsonpath='{.status.phase}' | grep -qx Succeeded"
  local hosts total canary
  hosts=$(k -n bg-apps logs probe)
  total=$(printf '%s\n' "$hosts" | grep -c .)
  canary=$(printf '%s\n' "$hosts" | grep -c -- "-$CANARY_HASH-" || true)
  awk -v c="$canary" -v t="$total" -v u="$2" 'BEGIN { printf "   %-44s %4d / %4d = %5.2f%% canary\n", u, c, t, 100 * c / t }'
}
count http://orders.bg-apps.internal/ "via Traefik (orders.bg-apps.internal)"
count http://orders-all.bg-apps/ "per replica (plain Service over all pods)"
echo "   rollout: $(k -n bg-apps get rollout orders-service -o jsonpath='{.status.phase} step {.status.currentStepIndex}')"
