package dev.budgetguard.common.faults;

import static org.assertj.core.api.Assertions.assertThat;

import dev.budgetguard.common.faults.FaultState.Faults;
import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import java.time.Duration;
import org.junit.jupiter.api.Test;

class FaultStateTest {

    /** A clock the test can move. */
    static final class MovableClock extends Clock {
        private Instant now = Instant.parse("2026-10-26T10:00:00Z");
        void advance(Duration d) { now = now.plus(d); }
        @Override public ZoneOffset getZone() { return ZoneOffset.UTC; }
        @Override public Clock withZone(java.time.ZoneId zone) { return this; }
        @Override public Instant instant() { return now; }
    }

    @Test
    void overrideExpiresBackToTheEnvDefaults() {
        var clock = new MovableClock();
        var defaults = new Faults(0.0, 0, null, false);
        var state = new FaultState(defaults, clock);

        state.replace(new Faults(0.02, 0, null, false), 900);
        assertThat(state.current().errorRate()).isEqualTo(0.02);
        assertThat(state.secondsLeft()).isEqualTo(900);

        clock.advance(Duration.ofSeconds(899));
        assertThat(state.current().errorRate()).isEqualTo(0.02);

        clock.advance(Duration.ofSeconds(1));                  // a forgotten demo fault never burns budget overnight
        assertThat(state.current()).isEqualTo(defaults);
        assertThat(state.secondsLeft()).isZero();
    }

    @Test
    void resetReturnsToDefaultsImmediately() {
        var defaults = new Faults(0.0, 0, "auth-service", false);
        var state = new FaultState(defaults, new MovableClock());
        state.replace(Faults.NONE, 600);
        state.reset();
        assertThat(state.current().dependency()).isEqualTo("auth-service");
    }
}
