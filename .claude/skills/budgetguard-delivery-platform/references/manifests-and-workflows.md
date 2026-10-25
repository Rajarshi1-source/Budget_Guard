# BudgetGuard — manifests and workflows (Rev 2 §9, §10, §11.4 with fixes)

Every non-template YAML file here parses (checked with PyYAML). Helm templates were not
rendered in this session (no Helm binary reachable): run `helm lint` and
`helm template deploy/helm/service -f values-ci.yaml | kubeconform -strict` in `ci.yml`,
which is where they are meant to be checked anyway.

## Contents

1. docker-compose (local)
2. Makefile (demo targets)
3. Helm chart `deploy/helm/service` (Rollout, Traefik weights, analysis, PodMonitor)
4. Helm chart `deploy/helm/platform` (API, workers, dashboard, NetworkPolicy)
5. Cluster bootstrap (k3s, Argo Rollouts, kube-prometheus-stack, deployer RBAC)
6. Cloudflare Tunnel
7. GitHub setup (App, environments, secrets)
8. Workflows: `ci.yml`, `build-push.yml`, `deploy.yml`
9. Chaos drill of BudgetGuard itself

---

## 1. docker-compose

`deploy/compose/.env.versions` — the only place image tags live (re-check each at kickoff):

```bash
PROMETHEUS_TAG=v3.13.2
ALERTMANAGER_TAG=v0.34.1
GRAFANA_TAG=13.2.2
TIMESCALE_TAG=2.27.0-pg18        # TSL build (never -oss): continuous aggregates and compression need it
VALKEY_TAG=9.1
BLACKBOX_TAG=pin-at-kickoff
CLOUDFLARED_TAG=pin-at-kickoff
```

`deploy/compose/docker-compose.yml` (Rev 2 §9.2 plus: Valkey volume, Alertmanager and
blackbox config mounts, two snapshotter replicas to prove the advisory lock, a `public`
profile for the tunnel):

```yaml
name: budgetguard
x-svc: &svc
  restart: unless-stopped
  environment:
    JAVA_TOOL_OPTIONS: "-XX:MaxRAMPercentage=75"
  healthcheck:
    # Rev 2 uses wget; confirm the temurin image ships it (docker run --rm eclipse-temurin:21-jre which wget)
    test: ["CMD", "wget", "-qO-", "http://localhost:8080/actuator/health/readiness"]
    interval: 10s
    retries: 12

x-platform: &platform
  build: ../../platform
  env_file: .env
  restart: unless-stopped
  depends_on:
    timescaledb: { condition: service_healthy }
    valkey: { condition: service_healthy }

services:
  auth-service:          { <<: *svc, build: ../../services/auth-service, ports: ["8081:8080"] }
  inventory-service:     { <<: *svc, build: ../../services/inventory-service, ports: ["8083:8080"] }
  notifications-service: { <<: *svc, build: ../../services/notifications-service, ports: ["8084:8080"] }
  orders-service:
    <<: *svc
    build: ../../services/orders-service
    ports: ["8082:8080"]
    depends_on:
      auth-service: { condition: service_healthy }
      inventory-service: { condition: service_healthy }
      notifications-service: { condition: service_healthy }

  timescaledb:
    image: timescale/timescaledb:${TIMESCALE_TAG}
    environment: { POSTGRES_DB: budgetguard, POSTGRES_USER: bg, POSTGRES_PASSWORD: "${PG_PASSWORD:?set in .env}" }
    volumes: ["pg:/home/postgres/pgdata/data"]
    healthcheck: { test: ["CMD-SHELL", "pg_isready -U bg -d budgetguard"], interval: 5s, retries: 20 }

  migrate:                                   # Alembic owns the schema; no initdb scripts
    <<: *platform
    restart: "no"
    command: ["alembic", "upgrade", "head"]

  valkey:
    image: valkey/valkey:${VALKEY_TAG}
    command: ["valkey-server", "--appendonly", "yes"]
    volumes: ["valkey:/data"]
    healthcheck: { test: ["CMD", "valkey-cli", "ping"], interval: 5s, retries: 20 }

  prometheus:
    image: prom/prometheus:${PROMETHEUS_TAG}
    command:
      - --config.file=/etc/prometheus/prometheus.yml
      - --storage.tsdb.retention.time=35d
      - --web.enable-lifecycle
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - ../../rules/generated:/etc/prometheus/rules:ro
      - prom:/prometheus
    ports: ["9090:9090"]

  alertmanager:
    image: prom/alertmanager:${ALERTMANAGER_TAG}
    volumes: ["./alertmanager.yml:/etc/alertmanager/alertmanager.yml:ro"]
    ports: ["9093:9093"]

  blackbox:
    image: prom/blackbox-exporter:${BLACKBOX_TAG}
    volumes: ["./blackbox.yml:/etc/blackbox_exporter/config.yml:ro"]

  grafana:
    image: grafana/grafana:${GRAFANA_TAG}
    environment:
      GF_AUTH_ANONYMOUS_ENABLED: "true"
      GF_AUTH_ANONYMOUS_ORG_ROLE: Viewer
    volumes: ["../../grafana:/etc/grafana/provisioning:ro", "grafana:/var/lib/grafana"]
    ports: ["3001:3000"]

  platform-api:
    <<: *platform
    command: ["uvicorn", "budgetguard.main:app", "--host", "0.0.0.0", "--port", "8000"]
    ports: ["8000:8000"]
  gate-worker:
    <<: *platform
    command: ["python", "-m", "budgetguard.workers.gate_worker"]
  brief-writer:
    <<: *platform
    command: ["python", "-m", "budgetguard.workers.brief_writer"]
  snapshotter:
    <<: *platform
    command: ["python", "-m", "budgetguard.workers.snapshotter"]
    deploy: { replicas: 2 }                  # one holds pg_try_advisory_lock(724001); the other waits

  dashboard:
    build: ../../dashboard
    environment: { BUDGETGUARD_API: "http://platform-api:8000" }
    env_file: .env.dashboard
    ports: ["3000:3000"]

  cloudflared:
    profiles: ["public"]
    image: cloudflare/cloudflared:${CLOUDFLARED_TAG}
    command: ["tunnel", "--no-autoupdate", "run"]
    environment: { TUNNEL_TOKEN: "${CF_TUNNEL_TOKEN:?set for the public profile}" }

volumes: { pg: {}, prom: {}, grafana: {}, valkey: {} }
```

`deploy/compose/blackbox.yml`:

```yaml
modules:
  http_2xx:
    prober: http
    timeout: 5s
    http:
      valid_status_codes: [200]
      method: GET
      preferred_ip_protocol: ip4
```

---

## 2. Makefile

```makefile
COMPOSE := docker compose --env-file deploy/compose/.env.versions -f deploy/compose/docker-compose.yml
K6      := docker run --rm -i --network budgetguard_default -v $(PWD)/load:/load grafana/k6:2.2.0

SLOTH   := docker run --rm -v $(PWD):/w -w /w ghcr.io/slok/sloth:v0.16.0
PROMTOOL:= docker run --rm -v $(PWD):/w -w /w --entrypoint promtool prom/prometheus:v3.13.2

.PHONY: up down rules test sloth-parity demo-fast-burn demo-dependency-outage demo-dead-service demo-reset

up: rules
	$(COMPOSE) up -d --build
	$(COMPOSE) run --rm migrate

down:
	$(COMPOSE) down

rules:                                  ## regenerate rules; CI fails if committed rules differ
	cd platform && uv run bg generate ../slo -o ../rules/generated

test:
	cd platform && uv run pytest -q
	$(PROMTOOL) test rules rules/tests/*.yaml

sloth-parity:                           ## budgetguard-slo-rules §4: same ratios as Sloth on the same input
	for f in slo/*.yaml; do \
	  s=$$(basename $$f .yaml); \
	  (cd platform && uv run bg export sloth ../$$f) > rules/parity/$$s.sloth.yaml; \
	  $(SLOTH) generate -i rules/parity/$$s.sloth.yaml -o rules/parity/$$s.sloth.rules.yaml; \
	done
	$(PROMTOOL) test rules rules/parity/tests/*.yaml

FAULT = curl -fsS -H "X-BG-Fault-Token: $${BG_FAULTS_TOKEN:?export BG_FAULTS_TOKEN}" -H 'Content-Type: application/json'

demo-fast-burn:                         ## orders 20x self-inflicted for 15 min (99.9% SLO, 2% errors)
	$(FAULT) -X POST localhost:8082/admin/faults -d '{"errorRate":0.02,"ttlSeconds":900}'
	$(K6) run -e BASE=http://orders-service:8080 /load/steady.js

demo-dependency-outage:                 ## auth fails; orders must NOT be frozen (D-3)
	$(FAULT) -X POST localhost:8081/admin/faults -d '{"errorRate":0.5,"ttlSeconds":900}'
	$(K6) run -e BASE=http://orders-service:8080 /load/steady.js

demo-dead-service:                      ## inventory stops: UNKNOWN, never HEALTHY (D-4)
	$(COMPOSE) stop inventory-service

demo-reset:
	for p in 8081 8082 8083 8084; do $(FAULT) -X DELETE localhost:$$p/admin/faults || true; done
	$(COMPOSE) start inventory-service
```

The fault endpoint contract (`ttlSeconds` auto-revert, token header) is defined in
`budgetguard-spring-services` §4.

---

## 3. Helm chart `deploy/helm/service`

```text
deploy/helm/service/
├── Chart.yaml
├── values.yaml
└── templates/
    ├── rollout.yaml
    ├── services.yaml            # stable + canary
    ├── traefikservice.yaml      # weighted; Argo Rollouts sets the weights
    ├── ingressroute.yaml        # internal entrypoint: service-to-service calls go through Traefik
    ├── analysistemplate.yaml
    ├── podmonitor.yaml
    └── pdb.yaml
```

`Chart.yaml`:

```yaml
apiVersion: v2
name: bg-service
description: One BudgetGuard service under test, shipped as an Argo Rollouts canary
type: application
version: 0.3.0
appVersion: "rev2"
```

`values.yaml`:

```yaml
service: orders-service             # overridden per release
image:
  repository: docker.io/CHANGE_ME/orders-service
  digest: ""                        # always deploy by digest; tags are for humans
replicas: 4
port: 8080
resources:
  requests: { cpu: 100m, memory: 384Mi }
  limits: { memory: 512Mi }
canary:
  weights: [5, 25, 50, 100]         # from the gate's canary_profile; conservative default
  bake: 10m
  analysis:
    maxRatio: 2                     # canary self-burn may be at most 2x stable
    minRequestsPerMinute: 30
internalHost: orders.bg-apps.internal
env: {}
```

`templates/rollout.yaml`:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Rollout
metadata:
  name: {{ .Values.service }}
  labels: { app: {{ .Values.service }} }
spec:
  replicas: {{ .Values.replicas }}
  revisionHistoryLimit: 5
  selector:
    matchLabels: { app: {{ .Values.service }} }
  template:
    metadata:
      labels: { app: {{ .Values.service }} }
    spec:
      securityContext: { runAsNonRoot: true, runAsUser: 10001, seccompProfile: { type: RuntimeDefault } }
      containers:
        - name: app
          image: "{{ .Values.image.repository }}@{{ required "image.digest is required" .Values.image.digest }}"
          ports: [{ name: http, containerPort: {{ .Values.port }} }]
          env:
            - { name: SPRING_APPLICATION_NAME, value: {{ .Values.service | quote }} }
            {{- range $k, $v := .Values.env }}
            - { name: {{ $k }}, value: {{ $v | quote }} }
            {{- end }}
          readinessProbe:
            httpGet: { path: /actuator/health/readiness, port: http }
            periodSeconds: 5
          livenessProbe:
            httpGet: { path: /actuator/health/liveness, port: http }
            initialDelaySeconds: 30
            periodSeconds: 10
          resources: {{- toYaml .Values.resources | nindent 12 }}
          securityContext: { allowPrivilegeEscalation: false, readOnlyRootFilesystem: true, capabilities: { drop: [ALL] } }
          volumeMounts: [{ name: tmp, mountPath: /tmp }]
      volumes: [{ name: tmp, emptyDir: {} }]
  strategy:
    canary:
      stableService: {{ .Values.service }}-stable
      canaryService: {{ .Values.service }}-canary
      trafficRouting:
        traefik:
          weightedTraefikServiceName: {{ .Values.service }}-weighted
      analysis:                                   # background analysis for the whole rollout
        startingStep: 1
        templates: [{ templateName: budget-burn-canary }]
        args:
          - { name: service, value: {{ .Values.service | quote }} }
          - name: canary-hash
            valueFrom: { podTemplateHashValue: Latest }
          - name: stable-hash
            valueFrom: { podTemplateHashValue: Stable }
      steps:
        {{- range $w := .Values.canary.weights }}
        - setWeight: {{ $w }}
        {{- if lt (int $w) 100 }}
        - pause: { duration: {{ $.Values.canary.bake }} }
        {{- end }}
        {{- end }}
```

`templates/services.yaml`:

```yaml
{{- range $role := list "stable" "canary" }}
---
apiVersion: v1
kind: Service
metadata:
  name: {{ $.Values.service }}-{{ $role }}
spec:
  selector: { app: {{ $.Values.service }} }      # Argo Rollouts adds the pod-template-hash selector
  ports: [{ name: http, port: 80, targetPort: http }]
{{- end }}
```

`templates/traefikservice.yaml` (Argo Rollouts' Traefik integration defaults to the
`traefik.io` group, i.e. Traefik v3, which current k3s bundles; no `weight` fields, the
controller owns them):

```yaml
apiVersion: traefik.io/v1alpha1
kind: TraefikService
metadata:
  name: {{ .Values.service }}-weighted
spec:
  weighted:
    services:
      - { name: {{ .Values.service }}-stable, port: 80 }
      - { name: {{ .Values.service }}-canary, port: 80 }
```

`templates/ingressroute.yaml` — the internal route other services call (see the "internal
traffic" flag in SKILL.md):

```yaml
apiVersion: traefik.io/v1alpha1
kind: IngressRoute
metadata:
  name: {{ .Values.service }}-internal
spec:
  entryPoints: [internal]                       # a ClusterIP-only Traefik entrypoint
  routes:
    - match: Host(`{{ .Values.internalHost }}`)
      kind: Rule
      services:
        - name: {{ .Values.service }}-weighted
          kind: TraefikService
```

`templates/analysistemplate.yaml` (Rev 2 §9.5 plus `initialDelay`, so an empty first
window is not counted as an error, and an explicit error budget for measurement errors):

```yaml
apiVersion: argoproj.io/v1alpha1
kind: AnalysisTemplate
metadata:
  name: budget-burn-canary
spec:
  args:
    - name: service
    - name: canary-hash
    - name: stable-hash
  metrics:
    - name: canary-has-traffic                  # D-4: no traffic is not a pass
      initialDelay: 2m
      interval: 1m
      count: 5
      failureLimit: 1
      consecutiveErrorLimit: 3
      successCondition: len(result) > 0 && result[0] >= {{ .Values.canary.analysis.minRequestsPerMinute }}
      provider:
        prometheus:
          address: http://kube-prometheus-stack-prometheus.monitoring:9090
          query: |
            sum(rate(http_server_requests_seconds_count{service="{{"{{"}}args.service{{"}}"}}",rollouts_pod_template_hash="{{"{{"}}args.canary-hash{{"}}"}}"}[2m])) * 60
    - name: canary-vs-stable-self-burn
      initialDelay: 2m
      interval: 1m
      count: 10
      failureLimit: 2
      consecutiveErrorLimit: 3
      successCondition: len(result) > 0 && result[0] <= {{ .Values.canary.analysis.maxRatio }}
      provider:
        prometheus:
          address: http://kube-prometheus-stack-prometheus.monitoring:9090
          query: |
            (
              (sum(rate(http_server_requests_seconds_count{service="{{"{{"}}args.service{{"}}"}}",rollouts_pod_template_hash="{{"{{"}}args.canary-hash{{"}}"}}",status=~"5..",error_origin="self"}[2m])) or vector(0))
              / sum(rate(http_server_requests_seconds_count{service="{{"{{"}}args.service{{"}}"}}",rollouts_pod_template_hash="{{"{{"}}args.canary-hash{{"}}"}}"}[2m]))
            )
            /
            clamp_min(
              (sum(rate(http_server_requests_seconds_count{service="{{"{{"}}args.service{{"}}"}}",rollouts_pod_template_hash="{{"{{"}}args.stable-hash{{"}}"}}",status=~"5..",error_origin="self"}[2m])) or vector(0))
              / sum(rate(http_server_requests_seconds_count{service="{{"{{"}}args.service{{"}}"}}",rollouts_pod_template_hash="{{"{{"}}args.stable-hash{{"}}"}}"}[2m])),
              0.0005
            )
```

The `{{"{{"}}args.x{{"}}"}}` dance makes Helm emit Argo's literal `{{args.x}}`. The
`or vector(0)` on the error numerators matters: a healthy canary has **no** 5xx series, and
without it the ratio is an empty vector and the measurement errors instead of passing.

`templates/podmonitor.yaml`:

```yaml
apiVersion: monitoring.coreos.com/v1
kind: PodMonitor
metadata:
  name: {{ .Values.service }}
  labels: { release: kube-prometheus-stack }
spec:
  selector:
    matchLabels: { app: {{ .Values.service }} }
  podTargetLabels: [rollouts-pod-template-hash]   # becomes rollouts_pod_template_hash
  podMetricsEndpoints:
    - port: http
      path: /actuator/prometheus
      interval: 15s
```

`templates/pdb.yaml`:

```yaml
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: {{ .Values.service }}
spec:
  minAvailable: 2
  selector:
    matchLabels: { app: {{ .Values.service }} }
```

---

## 4. Helm chart `deploy/helm/platform` (key templates)

```yaml
# templates/api.yaml
apiVersion: apps/v1
kind: Deployment
metadata: { name: platform-api }
spec:
  replicas: 2
  strategy: { type: RollingUpdate, rollingUpdate: { maxUnavailable: 0, maxSurge: 1 } }
  selector: { matchLabels: { app: platform-api } }
  template:
    metadata: { labels: { app: platform-api } }
    spec:
      containers:
        - name: api
          image: "{{ .Values.image.repository }}@{{ .Values.image.digest }}"
          args: ["uvicorn", "budgetguard.main:app", "--host", "0.0.0.0", "--port", "8000"]
          envFrom: [{ secretRef: { name: budgetguard-platform } }]
          ports: [{ name: http, containerPort: 8000 }]
          readinessProbe: { httpGet: { path: /readyz, port: http }, periodSeconds: 5 }
          livenessProbe: { httpGet: { path: /livez, port: http }, periodSeconds: 10 }
          resources: { requests: { cpu: 100m, memory: 256Mi }, limits: { memory: 512Mi } }
---
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata: { name: platform-api }
spec: { minAvailable: 1, selector: { matchLabels: { app: platform-api } } }
```

`gate-worker` (2 replicas, consumer group), `brief-writer` (1), `snapshotter` (2, advisory
lock — no Lease, no RBAC) and `dashboard` (2) follow the same shape with their own `args`.
`/readyz` checks PostgreSQL and Valkey; `/livez` checks only the event loop, so a database
blip does not restart every pod.

```yaml
# templates/networkpolicy.yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: { name: platform-api-ingress }
spec:
  podSelector: { matchLabels: { app: platform-api } }
  policyTypes: [Ingress]
  ingress:
    - from:                                     # cloudflared (public paths are filtered there)
        - namespaceSelector: { matchLabels: { kubernetes.io/metadata.name: edge } }
    - from:                                     # dashboard server-side calls
        - podSelector: { matchLabels: { app: dashboard } }
    - from:                                     # Alertmanager webhook
        - namespaceSelector: { matchLabels: { kubernetes.io/metadata.name: monitoring } }
      ports: [{ port: 8000 }]
```

---

Verified 25 Oct 2026: both charts (the platform chart with a minimal `Chart.yaml` and
`values.yaml` added) pass `helm lint` with Helm 4.2.4, and the 11 rendered objects pass
`kubeconform -strict` against Kubernetes 1.36 and the CRD catalog, the same check `ci.yml` (§8) runs.

## 5. Cluster bootstrap (Oracle A1, ARM64)

```bash
# k3s (Traefik v3 bundled). Pin the exact patch at kickoff.
curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION="v1.36.x+k3s1" sh -s - \
  --write-kubeconfig-mode 600 --disable servicelb

for ns in bg-apps bg-platform monitoring argo-rollouts data edge; do kubectl create namespace "$ns"; done

# Argo Rollouts 1.10.0 + the kubectl plugin (deploy job uses `kubectl argo rollouts status`)
kubectl apply -n argo-rollouts -f https://github.com/argoproj/argo-rollouts/releases/download/v1.10.0/install.yaml

# kube-prometheus-stack (pin the chart version at kickoff)
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm upgrade --install kube-prometheus-stack prometheus-community/kube-prometheus-stack \
  -n monitoring -f deploy/k3s/kps-values.yaml
```

`deploy/k3s/kps-values.yaml`:

```yaml
prometheus:
  prometheusSpec:
    retention: 35d                       # a 30-day SLO needs more than the default
    retentionSize: 8GB
    scrapeInterval: 15s
    podMonitorSelectorNilUsesHelmValues: false
    ruleSelectorNilUsesHelmValues: false
    resources: { requests: { memory: 768Mi }, limits: { memory: 1280Mi } }
    storageSpec:
      volumeClaimTemplate:
        spec: { accessModes: [ReadWriteOnce], resources: { requests: { storage: 15Gi } } }
alertmanager:
  alertmanagerSpec:
    secrets: [budgetguard-am]            # slack URLs + bearer token, mounted under /etc/alertmanager/secrets/
  config:                                # same tree as budgetguard-slo-rules §5 (compose)
    route:
      receiver: slack-tickets
      group_by: [service, slo, severity]
      routes:
        - { matchers: ['severity="page"'], receiver: page, continue: true }
        - { matchers: ['severity="page"'], receiver: budgetguard }
        - { matchers: ['severity="ticket"'], receiver: budgetguard, continue: true }
    receivers:
      - name: page
        slack_configs: [{ api_url_file: /etc/alertmanager/secrets/budgetguard-am/slack_page_url, channel: "#bg-page", send_resolved: true }]
      - name: slack-tickets
        slack_configs: [{ api_url_file: /etc/alertmanager/secrets/budgetguard-am/slack_ticket_url, channel: "#bg-tickets", send_resolved: true }]
      - name: budgetguard
        webhook_configs:
          - url: http://platform-api.bg-platform:8000/v1/webhooks/alertmanager
            send_resolved: true
            http_config: { authorization: { credentials_file: /etc/alertmanager/secrets/budgetguard-am/bg_token } }
    inhibit_rules:
      - source_matchers: ['severity="page"']
        target_matchers: ['severity="ticket"']
        equal: [service, slo]
grafana:
  enabled: false                         # Grafana runs from the platform chart with provisioned JSON
```

Alertmanager stops at the first matching route unless `continue: true`, so the page route
continues into the `budgetguard` route; otherwise pages would never reach the platform's
incident table (Flow 3). The gate itself reads `ALERTS` from Prometheus, so an Alertmanager
outage never changes a verdict.

Generated rules ship as `PrometheusRule`:
`cd platform && uv run bg generate ../slo -o ../deploy/k3s/rules --format prometheus-operator`.

Deployer RBAC (what the `KUBECONFIG` environment secret can do — nothing outside `bg-apps`):

```yaml
apiVersion: v1
kind: ServiceAccount
metadata: { name: deployer, namespace: bg-apps }
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata: { name: deployer, namespace: bg-apps }
rules:
  - apiGroups: [argoproj.io]
    resources: [rollouts, rollouts/status, analysistemplates, analysisruns, experiments]
    verbs: [get, list, watch, create, update, patch]
  - apiGroups: [traefik.io]
    resources: [traefikservices, ingressroutes]
    verbs: [get, list, watch, create, update, patch]
  - apiGroups: [""]
    resources: [services, configmaps, secrets]       # secrets: Helm 4 stores release state there
    verbs: [get, list, watch, create, update, patch]
  - apiGroups: [policy]
    resources: [poddisruptionbudgets]
    verbs: [get, list, watch, create, update, patch]
  - apiGroups: [monitoring.coreos.com]
    resources: [podmonitors]
    verbs: [get, list, watch, create, update, patch]
  - apiGroups: [""]
    resources: [pods, replicasets]
    verbs: [get, list, watch]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata: { name: deployer, namespace: bg-apps }
subjects: [{ kind: ServiceAccount, name: deployer, namespace: bg-apps }]
roleRef: { apiGroup: rbac.authorization.k8s.io, kind: Role, name: deployer }
```

---

## 6. Cloudflare Tunnel (no inbound ports on the VM)

`deploy/k3s/cloudflared-config.yaml` (mounted into a `cloudflared` Deployment in `edge`):

```yaml
tunnel: budgetguard
credentials-file: /etc/cloudflared/creds/credentials.json
ingress:
  - hostname: api.budgetguard.example.dev
    path: ^/v1/(github/webhook|decisions)$       # webhook + OIDC-authenticated decision reads
    service: http://platform-api.bg-platform:8000
  - hostname: budgetguard.example.dev
    service: http://dashboard.bg-platform:3000
  - hostname: grafana.budgetguard.example.dev
    service: http://grafana.bg-platform:3000      # anonymous Viewer only
  - service: http_status:404
```

`/v1/decisions` must be public (GitHub-hosted runners call it), which Rev 2 §9.4's
NetworkPolicy note omits. It is safe because every call carries a GitHub OIDC token that the
API verifies for `repository`, `environment` and `audience` (`budgetguard-admission-gate` §6).

---

## 7. GitHub setup

App permissions: **Deployments: read and write** (answer protection rules), **Actions:
read and write** (run metadata; re-run a job after an override), **Contents: read** (trees for the rollback-lane proof), **Pull
requests: read and write** (PR file list, brief comments). Subscribe to: *Deployment
protection rule*, *Deployment status*, *Pull request*.

```bash
REPO=owner/budgetguard
APP_INTEGRATION_ID=123456          # the App's numeric id

for svc in auth-service orders-service inventory-service notifications-service; do
  env="production-$svc"
  gh api -X PUT "repos/$REPO/environments/$env" --input - <<JSON
{ "can_admins_bypass": false,
  "deployment_branch_policy": { "protected_branches": true, "custom_branch_policies": false } }
JSON
  gh api -X POST "repos/$REPO/environments/$env/deployment_protection_rules" \
    -F integration_id="$APP_INTEGRATION_ID"
  gh secret set KUBECONFIG --env "$env" < "kubeconfig-deployer.yaml"
done
gh variable set BUDGETGUARD_URL --body "https://api.budgetguard.example.dev"
gh variable set DOCKERHUB_NS --body "yourdockerhubname"
```

`can_admins_bypass: false` closes the admin-bypass hole for the demo. Keep the
admin-bypass reconciler from `budgetguard-admission-gate` anyway: a real org may need the
bypass, and then it must be recorded.

---

## 8. Workflows

### `.github/workflows/ci.yml`

```yaml
name: ci
on:
  pull_request:
  push:
    branches: [main]
permissions:
  contents: read
jobs:
  java:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v5
      - uses: actions/setup-java@v5
        with: { distribution: temurin, java-version: "21", cache: maven }
      - run: mvn -B -ntp -f services/pom.xml verify
  python:
    runs-on: ubuntu-24.04
    defaults: { run: { working-directory: platform } }
    steps:
      - uses: actions/checkout@v5
      - uses: astral-sh/setup-uv@v6
      - run: uv sync --frozen
      - run: uv run ruff check . && uv run mypy budgetguard
      - run: uv run pytest -q
      - run: uv run bg generate ../slo -o ../rules/generated --check
  rules:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v5
      - name: promtool (same version as the server)
        run: |
          docker run --rm -v "$PWD/rules:/rules" --entrypoint promtool prom/prometheus:v3.13.2 check rules /rules/generated/*.yaml
          docker run --rm -v "$PWD/rules:/rules" -w /rules/tests --entrypoint promtool prom/prometheus:v3.13.2 test rules *.yaml
      - name: Sloth parity
        run: make sloth-parity
  helm:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v5
      - uses: azure/setup-helm@v4
      - run: helm lint deploy/helm/service deploy/helm/platform
      - name: Render and validate
        run: |
          helm template t deploy/helm/service --set image.digest=sha256:0000000000000000000000000000000000000000000000000000000000000000 > rendered.yaml
          docker run --rm -v "$PWD:/w" ghcr.io/yannh/kubeconform:v0.7.0 -strict -summary -kubernetes-version 1.36.0 \
            -schema-location default \
            -schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json' \
            /w/rendered.yaml
  dashboard:
    runs-on: ubuntu-24.04
    defaults: { run: { working-directory: dashboard } }
    steps:
      - uses: actions/checkout@v5
      - uses: actions/setup-node@v5
        with: { node-version: "24", cache: npm, cache-dependency-path: dashboard/package-lock.json }
      - run: npm ci && npm run lint && npm run typecheck && npm test && npm run build
```

### `.github/workflows/build-push.yml`

```yaml
name: build-push
on:
  push:
    branches: [main]
permissions:
  contents: read
  id-token: write                    # cosign keyless
jobs:
  image:
    runs-on: ubuntu-24.04
    strategy:
      matrix:
        component: [auth-service, orders-service, inventory-service, notifications-service, platform, dashboard]
    steps:
      - uses: actions/checkout@v5
      - uses: docker/setup-qemu-action@v3
      - uses: docker/setup-buildx-action@v3
      - uses: docker/login-action@v3
        with: { username: "${{ vars.DOCKERHUB_USER }}", password: "${{ secrets.DOCKERHUB_TOKEN }}" }
      - name: Context
        id: ctx
        env: { C: "${{ matrix.component }}" }
        run: |
          case "$C" in platform|dashboard) echo "dir=$C" ;; *) echo "dir=services/$C" ;; esac >> "$GITHUB_OUTPUT"
      - uses: docker/build-push-action@v6
        id: build
        with:
          context: ${{ steps.ctx.outputs.dir }}
          platforms: linux/amd64,linux/arm64        # the public VM is ARM (Oracle A1)
          push: true
          tags: ${{ vars.DOCKERHUB_NS }}/${{ matrix.component }}:sha-${{ github.sha }}
          sbom: true
          provenance: mode=max
          cache-from: type=gha,scope=${{ matrix.component }}
          cache-to: type=gha,mode=max,scope=${{ matrix.component }}
      - uses: sigstore/cosign-installer@v3
      - name: Sign by digest
        env:
          IMAGE: ${{ vars.DOCKERHUB_NS }}/${{ matrix.component }}@${{ steps.build.outputs.digest }}
        run: cosign sign --yes "$IMAGE"
```

### `.github/workflows/deploy.yml` (Rev 2 §10.2 with five fixes, marked)

```yaml
name: deploy
on:
  workflow_run:
    workflows: [build-push]
    types: [completed]
    branches: [main]

permissions:
  contents: read
  id-token: write

jobs:
  changes:
    if: github.event.workflow_run.conclusion == 'success'
    runs-on: ubuntu-24.04
    outputs:
      services: ${{ steps.filter.outputs.changes }}
    steps:
      - uses: actions/checkout@v5
        with:
          ref: ${{ github.event.workflow_run.head_sha }}
          fetch-depth: 2
      - id: filter
        uses: dorny/paths-filter@v3
        with:
          base: ${{ github.event.workflow_run.head_sha }}~1
          filters: |
            auth-service: services/auth-service/**
            orders-service: services/orders-service/**
            inventory-service: services/inventory-service/**
            notifications-service: services/notifications-service/**

  deploy:
    needs: changes
    if: needs.changes.outputs.services != '[]'
    strategy:
      fail-fast: false
      max-parallel: 1                    # one service at a time keeps attribution clean
      matrix:
        service: ${{ fromJSON(needs.changes.outputs.services) }}
    environment: production-${{ matrix.service }}   # protection rule runs here; KUBECONFIG lives only here
    runs-on: ubuntu-24.04
    timeout-minutes: 60
    env:
      SERVICE: ${{ matrix.service }}
      SHA: ${{ github.event.workflow_run.head_sha }}
      BUDGETGUARD_URL: ${{ vars.BUDGETGUARD_URL }}
      IMAGE_REPO: ${{ vars.DOCKERHUB_NS }}/${{ matrix.service }}
    steps:
      - uses: actions/checkout@v5
        with: { ref: "${{ env.SHA }}" }

      - name: Fetch this run's decision (canary profile and bake)
        id: decision
        run: |
          set -euo pipefail
          OIDC=$(curl -sS -H "Authorization: bearer $ACTIONS_ID_TOKEN_REQUEST_TOKEN" \
                 "$ACTIONS_ID_TOKEN_REQUEST_URL&audience=budgetguard" | jq -r .value)
          if ! curl -sS --fail --max-time 10 -H "Authorization: Bearer $OIDC" \
               "$BUDGETGUARD_URL/v1/decisions?service=$SERVICE&run_id=$GITHUB_RUN_ID" -o decision.json; then
            jq -n '{canary_profile:[5,25,50,100], bake:"10m"}' > decision.json   # never the fastest
          fi
          # FIX 1: validate before use; the values reach helm, so treat them as untrusted input
          jq -e '(.canary_profile|type=="array" and all(.[]; type=="number" and .>0 and .<=100))
                 and (.bake|test("^[0-9]{1,3}[sm]$"))' decision.json
          echo "weights=$(jq -c .canary_profile decision.json)" >> "$GITHUB_OUTPUT"
          echo "bake=$(jq -r .bake decision.json)" >> "$GITHUB_OUTPUT"

      - uses: azure/setup-helm@v4
      - uses: sigstore/cosign-installer@v3

      - name: Resolve and verify the image
        id: image
        run: |
          set -euo pipefail
          DIGEST=$(docker buildx imagetools inspect "$IMAGE_REPO:sha-$SHA" --format '{{json .Manifest}}' | jq -r .digest)
          # FIX 2: Rev 2 signs images but never verifies them
          cosign verify "$IMAGE_REPO@$DIGEST" \
            --certificate-oidc-issuer https://token.actions.githubusercontent.com \
            --certificate-identity-regexp "^https://github.com/${GITHUB_REPOSITORY}/.github/workflows/build-push.yml@refs/heads/main$" > /dev/null
          echo "digest=$DIGEST" >> "$GITHUB_OUTPUT"

      - name: Canary rollout
        env:
          KUBECONFIG_DATA: ${{ secrets.KUBECONFIG }}
          WEIGHTS: ${{ steps.decision.outputs.weights }}     # FIX 3: env vars, not ${{ }} inside run:
          BAKE: ${{ steps.decision.outputs.bake }}
          DIGEST: ${{ steps.image.outputs.digest }}
        run: |
          set -euo pipefail
          echo "$KUBECONFIG_DATA" > "$RUNNER_TEMP/kubeconfig"
          export KUBECONFIG="$RUNNER_TEMP/kubeconfig"
          curl -sSL -o "$RUNNER_TEMP/kubectl-argo-rollouts" \
            https://github.com/argoproj/argo-rollouts/releases/download/v1.10.0/kubectl-argo-rollouts-linux-amd64
          chmod +x "$RUNNER_TEMP/kubectl-argo-rollouts"
          helm upgrade --install "$SERVICE" deploy/helm/service \
            --namespace bg-apps \
            --set service="$SERVICE" \
            --set image.repository="$IMAGE_REPO" \
            --set image.digest="$DIGEST" \
            --set-json "canary.weights=$WEIGHTS" \
            --set canary.bake="$BAKE"
          # FIX 4: helm --wait does not wait for canary analysis; the rollout status does.
          # Non-zero on abort or Degraded, so a failed canary fails the job and the deployment.
          "$RUNNER_TEMP/kubectl-argo-rollouts" status "$SERVICE" -n bg-apps --timeout 45m
```

FIX 5 lives in the API, not the YAML: Rev 2's job reads `.bake`, but the gate's `Decision`
has only `canary_profile`. `GET /v1/decisions` derives it:

```python
BAKE = {(100,): "0s", (25, 100): "2m", (10, 50, 100): "5m", (5, 25, 50, 100): "10m"}
bake = BAKE.get(tuple(decision.canary_profile), "10m")
```

---

## 9. Chaos drill of BudgetGuard itself (week 8)

| Kill | Expected | Proof to capture |
| --- | --- | --- |
| Both `platform-api` pods during a deploy | GitHub keeps the job waiting. GitHub does **not** redeliver a failed webhook by itself: the redelivery reconciler (`budgetguard-admission-gate`, `security-and-api.md` T-1) lists failed deliveries and redelivers them; `X-GitHub-Delivery` and `deployment_id` dedupe | One decision row after recovery |
| `gate-worker` mid-evaluation | `XAUTOCLAIM` after 60 s; verdict posted once | One `decisions` row per delivery |
| TimescaleDB | Gate falls back to `bg:error_ratio:increase30d`; decisions buffer in `bg:records-pending` | Replayed rows after recovery, inputs hashes match |
| Valkey | No cache, no single-flight: every evaluation goes to Prometheus; still correct | p95 verdict latency rises; verdicts unchanged |
| Prometheus | Integrity STALE → fail-open `ADMIT_UNVERIFIED` + page (or BLOCK for fail-closed) | Audit row with `SLI_UNKNOWN` |
| Snapshotter leader | Standby takes the advisory lock; 6-hour backfill fills the gap | No missing 5-minute buckets |
