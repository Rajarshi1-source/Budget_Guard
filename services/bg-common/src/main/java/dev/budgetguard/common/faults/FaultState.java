package dev.budgetguard.common.faults;

import java.time.Clock;
import java.time.Instant;
import java.util.concurrent.atomic.AtomicReference;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.core.env.Environment;

/** Current fault set: env-seeded defaults, replaced by POST /admin/faults until expiresAt. */
public class FaultState {

    public record Faults(double errorRate, long latencyMs, String dependency, boolean crash) {
        public static final Faults NONE = new Faults(0, 0, null, false);
    }

    private record Override(Faults faults, Instant expiresAt) {}

    private final Faults defaults;
    private final Clock clock;
    private final AtomicReference<Override> override = new AtomicReference<>();

    @Autowired
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
