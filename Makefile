# Run inside WSL `Ubuntu-D` from the repo root (plan §6 W0.6). Build the jars on Windows first:
#   cd services && mvnw.cmd -DskipTests package
# Week 1 targets; rules, test and sloth-parity arrive with the platform (W2+).

VERSIONS := deploy/compose/.env.versions
COMPOSE  := docker compose --env-file $(VERSIONS) $(if $(wildcard .env),--env-file .env) -f deploy/compose/docker-compose.yml
K6_TAG   := $(shell sed -n 's/^K6_TAG=\([^ #]*\).*/\1/p' $(VERSIONS))
K6       := docker run --rm -i --network budgetguard_default -v "$(CURDIR)/load:/load:ro" grafana/k6:$(K6_TAG)
FAULT     = curl -fsS -H "X-BG-Fault-Token: $${BG_FAULTS_TOKEN:?export BG_FAULTS_TOKEN}" -H 'Content-Type: application/json'

.PHONY: up down ps logs load demo-fast-burn demo-dependency-outage demo-dead-service demo-reset cluster-up

up:                                     ## build images from services/*/target and start the stack
	$(COMPOSE) up -d --build --wait

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
