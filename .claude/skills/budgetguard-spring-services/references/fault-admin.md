# BudgetGuard — fault state and admin endpoint (complete)

The two classes `SKILL.md` §4 describes. Compiled on 25 Oct 2026 together with every other `bg-common` class in the skill against Spring Boot 4.1.2, Resilience4j 2.4.0 and Java 21 (`mvn compile`, 11 classes). Not yet unit-tested: write the §8 tests for them in week 1 (no token → 401; admin profile off → 404; `ttlSeconds` over 3600 → 400; expiry reverts to the env defaults).

Implemented in W1 (`services/bg-common`): `FAULT_DEPENDENCY` inside `DownstreamClient` (throws
`DownstreamException` before sending, so the breaker and the `dep:` label behave as in a real
partition) and `FAULT_CRASH` in `FaultCrash` (readiness `REFUSING_TRAFFIC` at once, exit after
`budgetguard.faults.crash-grace`, default 10 s; the controller calls it when `crash` is true).
`FaultState` there carries `@Autowired` on its public constructor (two constructors).

## `bg-common/src/main/java/dev/budgetguard/common/faults/FaultState.java`

```java
package dev.budgetguard.common.faults;

import java.time.Clock;
import java.time.Instant;
import java.util.concurrent.atomic.AtomicReference;
import org.springframework.core.env.Environment;
import org.springframework.stereotype.Component;

/** Current fault set: env-seeded defaults, replaced by POST /admin/faults until expiresAt. */
@Component
public class FaultState {

    public record Faults(double errorRate, long latencyMs, String dependency, boolean crash) {
        public static final Faults NONE = new Faults(0, 0, null, false);
    }

    private record Override(Faults faults, Instant expiresAt) {}

    private final Faults defaults;
    private final Clock clock;
    private final AtomicReference<Override> override = new AtomicReference<>();

    public FaultState(Environment env) {
        this(new Faults(env.getProperty("FAULT_ERROR_RATE", Double.class, 0.0),
                        env.getProperty("FAULT_LATENCY_MS", Long.class, 0L),
                        env.getProperty("FAULT_DEPENDENCY"),
                        env.getProperty("FAULT_CRASH", Boolean.class, false)),
             Clock.systemUTC());
    }

    FaultState(Faults defaults, Clock clock) {
        this.defaults = defaults;
        this.clock = clock;
    }

    /** Defaults once the override has expired, so no scheduler thread is needed. */
    public Faults current() {
        Override o = override.get();
        return o != null && clock.instant().isBefore(o.expiresAt()) ? o.faults() : defaults;
    }

    public long secondsLeft() {
        Override o = override.get();
        return o == null ? 0 : Math.max(0, o.expiresAt().getEpochSecond() - clock.instant().getEpochSecond());
    }

    public void replace(Faults faults, long ttlSeconds) {
        override.set(new Override(faults, clock.instant().plusSeconds(ttlSeconds)));
    }

    public void reset() {
        override.set(null);
    }
}
```

## `bg-common/src/main/java/dev/budgetguard/common/faults/FaultAdminController.java`

Needs `spring-boot-starter-validation` on `bg-common` (the Initializr services already have it).

```java
package dev.budgetguard.common.faults;

import dev.budgetguard.common.faults.FaultState.Faults;
import jakarta.validation.Valid;
import jakarta.validation.constraints.DecimalMax;
import jakarta.validation.constraints.DecimalMin;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Map;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

/** Demo-only fault switch (security-and-api.md §4.1, T-7): 404 when disabled, constant-time token check. */
@RestController
@RequestMapping("/admin/faults")
public class FaultAdminController {

    public record FaultRequest(@DecimalMin("0.0") @DecimalMax("1.0") Double errorRate,
                               @Min(0) @Max(10_000) Long latencyMs,
                               String dependency,
                               Boolean crash,
                               @Min(1) @Max(3600) Long ttlSeconds) {}

    private final FaultState state;
    private final boolean enabled;
    private final byte[] token;

    public FaultAdminController(FaultState state,
                                @Value("${budgetguard.faults.admin-enabled:false}") boolean enabled,
                                @Value("${budgetguard.faults.admin-token:}") String token) {
        this.state = state;
        this.enabled = enabled && !token.isBlank();
        this.token = token.getBytes(StandardCharsets.UTF_8);
    }

    private void authorize(String presented) {
        if (!enabled) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND);          // do not advertise the endpoint
        }
        byte[] p = presented == null ? new byte[0] : presented.getBytes(StandardCharsets.UTF_8);
        if (!MessageDigest.isEqual(token, p)) {
            throw new ResponseStatusException(HttpStatus.UNAUTHORIZED);
        }
    }

    @GetMapping
    public Map<String, Object> get(@RequestHeader(value = "X-BG-Fault-Token", required = false) String t) {
        authorize(t);
        return Map.of("faults", state.current(), "secondsLeft", state.secondsLeft());
    }

    @PostMapping
    public Map<String, Object> replace(@RequestHeader(value = "X-BG-Fault-Token", required = false) String t,
                                       @Valid @RequestBody FaultRequest r) {
        authorize(t);
        var f = new Faults(r.errorRate() == null ? 0 : r.errorRate(), r.latencyMs() == null ? 0 : r.latencyMs(),
                           r.dependency(), Boolean.TRUE.equals(r.crash()));
        state.replace(f, r.ttlSeconds() == null ? 900 : r.ttlSeconds());   // missing fields mean "off"
        return get(t);
    }

    @DeleteMapping
    public Map<String, Object> reset(@RequestHeader(value = "X-BG-Fault-Token", required = false) String t) {
        authorize(t);
        state.reset();
        return get(t);
    }
}
```
