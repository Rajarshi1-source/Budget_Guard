---
name: budgetguard-spring-services
description: Build the four BudgetGuard services under test — auth, orders, inventory and notifications — on Spring Boot 4.1 with Java 21 and Maven — the bg-common module, Micrometer metrics with SLO histogram buckets and a service tag, the error_origin label separating self-inflicted from dependency-inflicted 5xx (D-3), the RestClient downstream wrapper with Resilience4j circuit breakers and timeouts, fault-injection flags and the admin fault endpoint, the notifications end-to-end delivery SLI, virtual threads, layered Dockerfiles and the k6 scripts that drive the demo. Use for ANY Java, pom.xml, application.yml, controller, filter, ObservationConvention, Resilience4j or k6 work on the BudgetGuard microservices, or when a latency SLI has no buckets, error_origin is missing, or orders is blamed for an auth outage, even if Spring is not named. MANDATE - Spring Boot 4.1.x, Java 21, Maven, never 3.x; label cardinality stays bounded; the services emit truth and never decide anything.
---

# BudgetGuard — the four Spring Boot services (Rev 2)

These services are the **system under test**. They exist to emit honest SLIs — request
counts, histogram buckets, and *who caused each error* — and to be breakable on demand for
the demo. They contain no BudgetGuard logic.

Pins (Rev 2 §C): Java **21** LTS (Ricky's machine has Oracle JDK 21.0.11 and Maven 3.9.x on
Windows 11), Spring Boot **4.1.x** (4.1.1 or newer; never 4.0.6, which carries CVEs; 4.0.7 only
as a fallback), Maven **3.9.x**, Resilience4j **2.4.0** (Rev 2 says 3.x, but no 3.x line exists:
Maven Central's newest is 2.4.0, checked 25 Oct 2026), Micrometer Prometheus registry,
k6 **2.2.x**.

Pairs with: `budgetguard-slo-rules` (consumes these metric names and labels),
`budgetguard-delivery-platform` (images, Helm, Rollouts), `budgetguard-mlops-evals`
(the synthetic incident library drives these fault flags).

---

## 1. Module layout (Maven reactor)

```text
services/
├── pom.xml                      # parent: spring-boot-starter-parent 4.1.x, Java 21, modules below
├── bg-common/                   # metrics convention, error-origin advice, faults, downstream client
├── auth-service/                # POST /login, GET /validate  (SLO 99.95%, p99 < 300 ms)
├── orders-service/              # POST /orders -> auth, inventory, notifications  (99.9%, < 500 ms)
├── inventory-service/           # GET /stock/{sku}, POST /reserve  (99.5%, < 800 ms)
└── notifications-service/       # POST /notify (202) + async dispatcher  (99.0%, delivered < 2 min)
```

Call graph (the D-3 demo depends on it): `orders → auth` (validate token),
`orders → inventory` (reserve), `orders → notifications` (enqueue). An auth outage makes
orders fail *through*; the label below makes sure auth, not orders, is charged.

### Parent `services/pom.xml` (essentials)

```xml
<parent>
  <groupId>org.springframework.boot</groupId>
  <artifactId>spring-boot-starter-parent</artifactId>
  <version>4.1.1</version>          <!-- float the 4.1 patch; re-check at kickoff -->
  <relativePath/>
</parent>
<groupId>dev.budgetguard</groupId>
<artifactId>services</artifactId>
<version>1.0.0</version>
<packaging>pom</packaging>
<properties>
  <java.version>21</java.version>
  <resilience4j.version>2.4.0</resilience4j.version>   <!-- newest on Maven Central, 25 Oct 2026; there is no 3.x -->
</properties>
<modules>
  <module>bg-common</module>
  <module>auth-service</module>
  <module>orders-service</module>
  <module>inventory-service</module>
  <module>notifications-service</module>
</modules>
```

Each service depends on: the Spring Boot web MVC starter (Boot 4 split the starters — use
`spring-boot-starter-webmvc`; the old `spring-boot-starter-web` name is a deprecated alias,
confirm against the 4.1 reference), `spring-boot-starter-actuator`,
`micrometer-registry-prometheus`, and `bg-common`. `bg-common` uses the **framework-agnostic**
Resilience4j modules (`resilience4j-circuitbreaker`, `resilience4j-micrometer`) wired by hand,
so it does not depend on a Resilience4j Spring Boot starter (none targets Boot 4 yet) — one less
thing to break on an upgrade. Verified: every `bg-common` class in this skill plus
`references/fault-admin.md` compiles against Boot 4.1.2 and Resilience4j 2.4.0 on Java 21.

Build on Windows: `mvnw.cmd -q -DskipTests package` from `services/`; CI uses `./mvnw verify`.

---

## 2. Metrics — what every service must emit

```yaml
# <service>/src/main/resources/application.yml
spring:
  application:
    name: orders-service
  threads:
    virtual:
      enabled: true
server:
  port: 8080
  shutdown: graceful
management:
  endpoints:
    web:
      exposure:
        include: health,info,prometheus
  endpoint:
    health:
      probes:
        enabled: true          # /actuator/health/liveness and /readiness for Kubernetes + blackbox
  metrics:
    distribution:
      slo:
        http.server.requests: 300ms,500ms,800ms,2s   # publishes the le buckets latency SLIs read
budgetguard:
  faults:
    admin-enabled: ${BG_FAULTS_ADMIN:false}          # demo profile only
    admin-token: ${BG_FAULTS_TOKEN:}
```

The `service` label comes from a bean rather than a property, so it works whatever Boot 4
calls the common-tags property (Rev 2 flagged `management.metrics.tags.*` for re-check):

```java
package dev.budgetguard.common.metrics;

import io.micrometer.core.instrument.MeterRegistry;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.micrometer.metrics.autoconfigure.MeterRegistryCustomizer;   // Boot 4 moved it (module spring-boot-micrometer-metrics)
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration(proxyBeanMethods = false)
public class CommonTagsConfig {
    @Bean
    MeterRegistryCustomizer<MeterRegistry> serviceTag(@Value("${spring.application.name}") String app) {
        return registry -> registry.config().commonTags("service", app);
    }
}
```

Result on `/actuator/prometheus` (what the rules expect):
`http_server_requests_seconds_count{service="orders-service",uri="/orders",method="POST",status="502",outcome="SERVER_ERROR",error_origin="dep:auth-service",…}`
and `http_server_requests_seconds_bucket{…,le="0.5"}`.

---

## 3. error_origin (D-3) — "freeze the culprit, not the victim"

Bounded values: `none`, `self`, `dep:auth-service`, `dep:inventory-service`,
`dep:notifications-service`. Never put an exception message, user id or URL in a label.

```java
package dev.budgetguard.common.metrics;

import io.micrometer.common.KeyValues;
import org.springframework.http.server.observation.DefaultServerRequestObservationConvention;
import org.springframework.http.server.observation.ServerRequestObservationContext;
import org.springframework.stereotype.Component;

/** Adds error_origin to http.server.requests. Read at observation stop, after the advice ran. */
@Component
public class ErrorOriginObservationConvention extends DefaultServerRequestObservationConvention {
    public static final String ATTR = "bg.error_origin";

    @Override
    public KeyValues getLowCardinalityKeyValues(ServerRequestObservationContext context) {
        Object origin = context.getCarrier().getAttribute(ATTR);
        return super.getLowCardinalityKeyValues(context)
                .and("error_origin", origin instanceof String s ? s : "none");
    }
}
```

```java
package dev.budgetguard.common.metrics;

import io.github.resilience4j.circuitbreaker.CallNotPermittedException;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.http.HttpStatus;
import org.springframework.http.ProblemDetail;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.server.ResponseStatusException;

@RestControllerAdvice
public class ErrorOriginAdvice {

    /** Thrown by DownstreamClient on dependency 5xx, timeout or connection failure. */
    public static final class DownstreamException extends RuntimeException {
        private final String dependency;
        public DownstreamException(String dependency, String message, Throwable cause) {
            super(message, cause);
            this.dependency = dependency;
        }
        public String dependency() { return dependency; }
    }

    @ExceptionHandler(DownstreamException.class)
    ResponseEntity<ProblemDetail> downstream(DownstreamException ex, HttpServletRequest req) {
        return fail(req, "dep:" + ex.dependency(), HttpStatus.BAD_GATEWAY, "dependency failed: " + ex.dependency());
    }

    @ExceptionHandler(CallNotPermittedException.class)
    ResponseEntity<ProblemDetail> breakerOpen(CallNotPermittedException ex, HttpServletRequest req) {
        // Convention: breaker name == dependency service name
        return fail(req, "dep:" + ex.getCausingCircuitBreakerName(), HttpStatus.SERVICE_UNAVAILABLE,
                "circuit open: " + ex.getCausingCircuitBreakerName());
    }

    @ExceptionHandler(ResponseStatusException.class)
    ResponseEntity<ProblemDetail> status(ResponseStatusException ex, HttpServletRequest req) {
        HttpStatus s = HttpStatus.valueOf(ex.getStatusCode().value());
        return fail(req, s.is5xxServerError() ? "self" : "none", s, ex.getReason());
    }

    @ExceptionHandler(Exception.class)
    ResponseEntity<ProblemDetail> own(Exception ex, HttpServletRequest req) {
        return fail(req, "self", HttpStatus.INTERNAL_SERVER_ERROR, "internal error");
    }

    private ResponseEntity<ProblemDetail> fail(HttpServletRequest req, String origin, HttpStatus status, String detail) {
        req.setAttribute(ErrorOriginObservationConvention.ATTR, origin);
        return ResponseEntity.status(status).body(ProblemDetail.forStatusAndDetail(status, detail));
    }
}
```

The `ResponseStatusException` handler matters: without it a deliberate `404` would hit the
catch-all and be counted as a self-inflicted 500.

### Downstream client — timeouts, breaker, classification

```java
package dev.budgetguard.common.http;

import dev.budgetguard.common.metrics.ErrorOriginAdvice.DownstreamException;
import io.github.resilience4j.circuitbreaker.CircuitBreaker;
import io.github.resilience4j.circuitbreaker.CircuitBreakerConfig;
import io.github.resilience4j.circuitbreaker.CircuitBreakerRegistry;
import io.github.resilience4j.micrometer.tagged.TaggedCircuitBreakerMetrics;
import io.micrometer.core.instrument.MeterRegistry;
import java.time.Duration;
import java.util.function.Supplier;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.web.client.HttpServerErrorException;
import org.springframework.web.client.ResourceAccessException;
import org.springframework.web.client.RestClient;

public final class DownstreamClient {
    private final String dependency;
    private final RestClient http;
    private final CircuitBreaker breaker;

    public DownstreamClient(String dependency, String baseUrl, CircuitBreakerRegistry registry) {
        var factory = new SimpleClientHttpRequestFactory();
        factory.setConnectTimeout(Duration.ofMillis(500));
        factory.setReadTimeout(Duration.ofSeconds(2));        // Rev 2: service-to-service 2 s
        this.dependency = dependency;
        this.http = RestClient.builder().baseUrl(baseUrl).requestFactory(factory).build();
        this.breaker = registry.circuitBreaker(dependency);   // name == dependency, read by the advice
    }

    public <T> T call(Supplier<T> request) {
        Supplier<T> guarded = CircuitBreaker.decorateSupplier(breaker, () -> {
            try {
                return request.get();
            } catch (HttpServerErrorException | ResourceAccessException e) {
                throw new DownstreamException(dependency, e.getMessage(), e);   // 4xx propagate unchanged
            }
        });
        return guarded.get();
    }

    public RestClient http() { return http; }

    public static CircuitBreakerRegistry registry(MeterRegistry meters) {
        var config = CircuitBreakerConfig.custom()
                .failureRateThreshold(50)
                .slidingWindowSize(20)
                .minimumNumberOfCalls(10)
                .waitDurationInOpenState(Duration.ofSeconds(15))
                .recordExceptions(DownstreamException.class)
                .build();
        var reg = CircuitBreakerRegistry.of(config);
        TaggedCircuitBreakerMetrics.ofCircuitBreakerRegistry(reg).bindTo(meters);
        return reg;
    }
}
```

Usage in orders: `auth.call(() -> auth.http().get().uri("/validate?t={t}", token).retrieve().body(Validation.class))`.
Base URLs come from config (`bg.downstream.auth-url`, …): `http://auth-service:8080` in
compose, `http://auth.bg-apps.internal` in k3s. The k3s value goes through Traefik's internal
entrypoint so Argo Rollouts' canary weights apply to service-to-service traffic too
(`budgetguard-delivery-platform`, flag 1).

Retries are deliberately **not** stacked here: a retry inside a 2 s budget multiplies load on a
failing dependency. If you add one, use a single retry on idempotent GETs only.

---

## 4. Fault injection (drives the demo and the synthetic incident library)

| Flag (env or admin endpoint) | Effect | Labelled as |
| --- | --- | --- |
| `FAULT_ERROR_RATE=0.02` | 2% of requests return 500 | `self` |
| `FAULT_LATENCY_MS=900` | adds 900 ms to every request | latency SLI burns |
| `FAULT_DEPENDENCY=auth-service` | orders' calls to auth throw before sending (simulated partition) | `dep:auth-service` |
| `FAULT_CRASH=true` | readiness DOWN and process exits after 10 s | integrity → STALE |

```java
package dev.budgetguard.common.faults;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.util.concurrent.ThreadLocalRandom;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Component;
import dev.budgetguard.common.metrics.ErrorOriginObservationConvention;
import org.springframework.web.filter.OncePerRequestFilter;

@Component
public class FaultFilter extends OncePerRequestFilter {
    private final FaultState state;
    public FaultFilter(FaultState state) { this.state = state; }

    @Override
    protected boolean shouldNotFilter(HttpServletRequest req) {
        return req.getRequestURI().startsWith("/actuator") || req.getRequestURI().startsWith("/admin");
    }

    @Override
    protected void doFilterInternal(HttpServletRequest req, HttpServletResponse res, FilterChain chain)
            throws ServletException, IOException {
        var f = state.current();
        if (f.latencyMs() > 0) {
            try { Thread.sleep(f.latencyMs()); }                // cheap on virtual threads
            catch (InterruptedException e) { Thread.currentThread().interrupt(); }
        }
        if (f.errorRate() > 0 && ThreadLocalRandom.current().nextDouble() < f.errorRate()) {
            // Filters run outside @RestControllerAdvice: label and answer here, don't throw.
            req.setAttribute(ErrorOriginObservationConvention.ATTR, "self");
            res.sendError(HttpStatus.INTERNAL_SERVER_ERROR.value(), "injected fault");
            return;
        }
        chain.doFilter(req, res);
    }
}
```

Why `sendError` and not `throw`: exceptions thrown in a servlet filter never reach
`@RestControllerAdvice`, so the label would be missing. Spring's observation filter runs
*outside* this one (it has the highest precedence), so the request is still observed, and the
convention reads the attribute set here.

`FaultState` is an `AtomicReference<Faults>` seeded from env and mutable via
`POST /admin/faults` — enabled only when `budgetguard.faults.admin-enabled=true` and the
`X-BG-Fault-Token` header matches. k6 scenarios flip faults through this endpoint so a demo
needs no redeploy (a redeploy would itself hit the gate).

The contract (the Makefile in `budgetguard-delivery-platform` and the k6 scripts call it):

| Call | Body | Effect |
| --- | --- | --- |
| `POST /admin/faults` | `{"errorRate":0.02,"latencyMs":0,"dependency":null,"crash":false,"ttlSeconds":900}` | Replace the fault set; missing fields default to "off"; `ttlSeconds` (max 3600) auto-reverts so a forgotten demo fault never burns budget overnight |
| `DELETE /admin/faults` | — | Back to the env-seeded defaults |
| `GET /admin/faults` | — | Current set and seconds until auto-revert |

`FaultState` stores `(Faults, Instant expiresAt)`; `current()` returns the defaults once
`expiresAt` has passed, so no scheduler thread is needed. Complete, compiled code for
`FaultState` and `FaultAdminController` (404 when disabled, `MessageDigest.isEqual` token check,
bean-validated body with `ttlSeconds` 1–3600) is in **`references/fault-admin.md`**.

---

## 5. Notifications — the async SLI (gap in Rev 2's code)

Rev 2 defines the notifications SLO as "95% delivered within 2 minutes of enqueue" but shows
no instrumentation. The HTTP 202 is not the user experience; delivery is.

```java
// in the dispatcher, after the provider call returns or retries are exhausted
Timer.builder("notification.delivery")
     .serviceLevelObjectives(Duration.ofMinutes(2))
     .tag("outcome", delivered ? "delivered" : "failed")
     .register(meters)
     .record(Duration.between(message.enqueuedAt(), Instant.now()));
```

Exposed as `notification_delivery_seconds_bucket{le="120.0",outcome=…}`. Failed deliveries
must also be *bad*: the spec uses `total = notification_delivery_seconds_count` and
`good = notification_delivery_seconds_bucket{le="120.0",outcome="delivered"}`, so a fast
failure never counts as good. The queue itself: an in-memory `LinkedBlockingQueue` with a
virtual-thread dispatcher is enough for the demo; say "Valkey stream or Kafka" is the
durable path.

---

## 6. Dockerfile (each service)

```dockerfile
FROM eclipse-temurin:21-jre AS extract
WORKDIR /app
COPY target/*.jar app.jar
RUN java -Djarmode=tools -jar app.jar extract --layers --launcher --destination extracted

FROM eclipse-temurin:21-jre
RUN useradd --uid 10001 --create-home app
WORKDIR /app
COPY --from=extract /app/extracted/dependencies/ ./
COPY --from=extract /app/extracted/spring-boot-loader/ ./
COPY --from=extract /app/extracted/snapshot-dependencies/ ./
COPY --from=extract /app/extracted/application/ ./
USER 10001
ENV JAVA_TOOL_OPTIONS="-XX:MaxRAMPercentage=75 -XX:+ExitOnOutOfMemoryError"
EXPOSE 8080
ENTRYPOINT ["java", "org.springframework.boot.loader.launch.JarLauncher"]
```

Memory budget on the free ARM VM: ~500 MB per JVM (`resources.limits.memory: 512Mi`);
four services ≈ 2 GB, matching Rev 2 §11.4.

---

## 7. k6 traffic (demo and incident library)

```javascript
// load/steady.js — baseline traffic that keeps every service above its traffic floor
import http from 'k6/http';
import { check } from 'k6';
export const options = { scenarios: { steady: { executor: 'constant-arrival-rate', rate: 20,
  timeUnit: '1s', duration: '24h', preAllocatedVUs: 20 } } };
const BASE = __ENV.BASE || 'http://orders-service:8080';
export default function () {
  const r = http.post(`${BASE}/orders`, JSON.stringify({ sku: 'SKU-1', qty: 1 }),
    { headers: { 'Content-Type': 'application/json', Authorization: 'Bearer demo' } });
  check(r, { 'not 5xx': (x) => x.status < 500 });
}
```

20 req/s = 72,000/h, far above the 600/h floor. `make demo-fast-burn` sets
`FAULT_ERROR_RATE=0.02` on orders for 15 minutes (2% on a 99.9% SLO = 20x burn → page);
`make demo-dependency-outage` sets `FAULT_ERROR_RATE=1.0` on auth for 10 minutes.

---

## 8. Tests worth having

```java
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@AutoConfigureObservability   // re-check the Boot 4.1 name; without it metrics export is off in tests
class MetricsContractTest {
    @Autowired TestRestTemplate rest;

    @Test
    void emitsErrorOriginAndSloBuckets() {
        rest.getForEntity("/boom", String.class);                   // test controller throwing IllegalStateException
        String prom = rest.getForObject("/actuator/prometheus", String.class);
        assertThat(prom).contains("error_origin=\"self\"");
        assertThat(prom).contains("service=\"orders-service\"");
        assertThat(prom).contains("le=\"0.5\"");
    }
}
```

Also: a 404 is `error_origin="none"`; an auth 503 through `DownstreamClient` is
`dep:auth-service`; an open breaker is `dep:auth-service` with status 503.

## 9. Interview soundbites

- "Every 5xx carries who caused it. When auth breaks, orders' errors are labelled
  `dep:auth-service`, and auth's budget pays — the victim keeps shipping its fixes."
- "The notifications SLI measures delivery from the queue, not the 202. Otherwise the SLI is
  green while messages rot."
- "I inject faults through an admin endpoint, not a redeploy — because in this system a
  redeploy has to get past the gate first."
