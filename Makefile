# Run inside WSL `Ubuntu-D` from the repo root (plan §6 W0.6). Build the jars on Windows first:
#   cd services && mvnw.cmd -DskipTests package
# rules/test-rules need uv in WSL (curl -LsSf https://astral.sh/uv/install.sh | sh); sloth-parity is W8.

VERSIONS := deploy/compose/.env.versions
# Later env files win: generated local secrets override the placeholders an .env copied from
# .env.example still holds; .env supplies what local.env lacks (the GitHub App, OAuth app).
LOCALENV := deploy/compose/secrets/local.env
COMPOSE  := docker compose --env-file $(VERSIONS) $(if $(wildcard .env),--env-file .env) $(if $(wildcard $(LOCALENV)),--env-file $(LOCALENV)) -f deploy/compose/docker-compose.yml
K6_TAG   := $(shell sed -n 's/^K6_TAG=\([^ #]*\).*/\1/p' $(VERSIONS))
K6       := docker run --rm -i --network budgetguard_default -v "$(CURDIR)/load:/load:ro" grafana/k6:$(K6_TAG)
FAULT     = curl -fsS -H "X-BG-Fault-Token: $${BG_FAULTS_TOKEN:?export BG_FAULTS_TOKEN}" -H 'Content-Type: application/json'

PROM_TAG := $(shell sed -n 's/^PROMETHEUS_TAG=\([^ #]*\).*/\1/p' $(VERSIONS))
PROMTOOL  = docker run --rm -v "$(CURDIR)/rules:/rules" --entrypoint sh prom/prometheus:$(PROM_TAG) -c

.PHONY: up compose-up backfill down ps logs load rules test-rules reload secrets status chaos-dead-service demo-fast-burn demo-dependency-outage demo-dead-service demo-reset cluster-up

up: secrets rules                       ## build images from services/*/target and start the stack
	$(MAKE) --no-print-directory compose-up

compose-up:                             # separate target so COMPOSE re-reads the env files secrets just wrote
	$(COMPOSE) up -d --build --wait

rules:                                  ## regenerate rules and promtool tests from slo/*.yaml (CI fails on drift)
	cd platform && uv run bg generate ../slo -o ../rules/generated && uv run bg tests ../slo -o ../rules/tests/generated

test-rules:                             ## promtool from the same image tag as the server; globs expand in the container
	$(PROMTOOL) 'cd /rules && promtool check rules generated/*.yaml'
	$(PROMTOOL) 'cd /rules/tests && promtool test rules *.test.yaml generated/*.test.yaml'

reload:                                 ## make Prometheus pick up regenerated rules
	curl -fsS -X POST localhost:9090/-/reload

secrets:                                ## generate deploy/compose/secrets (git-ignored; never overwrites)
	@bash deploy/compose/gen-secrets.sh

backfill:                               ## fill sli_buckets from Prometheus history (HOURS=720), refresh the aggregates
	$(COMPOSE) stop snapshotter                     # the leader holds the advisory lock
	$(COMPOSE) run --rm --no-deps snapshotter python -m budgetguard.workers.snapshotter --once --backfill-hours $${HOURS:-720}
	$(COMPOSE) start snapshotter
	@# bg_app cannot refresh (owner only); the policies alone only reach back 3 days
	$(COMPOSE) exec -T timescaledb psql -U bg -d budgetguard -c "CALL refresh_continuous_aggregate('sli_hourly', NULL, NULL)" -c "CALL refresh_continuous_aggregate('sli_daily', NULL, NULL)"

status:                                 ## live HEALTHY / AT_RISK / UNKNOWN per service and SLO (D-4)
	cd platform && uv run bg status ../slo --prometheus http://localhost:9090

chaos-dead-service:                     ## W3.6: stop orders; integrity must go STALE/NO_DATA -> UNKNOWN, SLIIntegrityLost pages
	$(COMPOSE) stop orders-service

down:
	$(COMPOSE) down

ps:
	$(COMPOSE) ps

logs:
	$(COMPOSE) logs -f --tail=100

load:                                   ## steady traffic through orders (DURATION=24h RATE=20 by default)
	$(K6) run -e BASE=http://orders-service:8080 -e RATE=$${RATE:-20} -e DURATION=$${DURATION:-24h} /load/steady.js

demo-fast-burn:                         ## orders 20x self-inflicted for 15 min (99.9% SLO, 2% errors); needs BG_FAULTS_ADMIN=true
	$(FAULT) -X POST localhost:8082/admin/faults -d '{"errorRate":0.02,"ttlSeconds":900}'

demo-dependency-outage:                 ## auth fails; orders must NOT be frozen (D-3)
	$(FAULT) -X POST localhost:8081/admin/faults -d '{"errorRate":0.5,"ttlSeconds":900}'

demo-dead-service:                      ## inventory stops: UNKNOWN, never HEALTHY (D-4)
	$(COMPOSE) stop inventory-service

demo-reset:
	for p in 8081 8082 8083 8084; do $(FAULT) -X DELETE localhost:$$p/admin/faults || true; done
	$(COMPOSE) start inventory-service

cluster-up:                             ## start (or restart) the local kind cluster after WSL was stopped
	@docker start $$(docker ps -aq --filter label=io.x-k8s.kind.cluster=bg) >/dev/null 2>&1 \
	  || kind create cluster --name bg --image kindest/node:v1.36.4 --wait 120s
	@kubectl --context kind-bg wait --for=condition=Ready node --all --timeout=120s
