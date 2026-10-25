package dev.budgetguard.common.faults;

import java.time.Duration;
import java.util.concurrent.atomic.AtomicBoolean;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.availability.AvailabilityChangeEvent;
import org.springframework.boot.availability.ReadinessState;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.ApplicationContext;
import org.springframework.context.event.EventListener;

/**
 * FAULT_CRASH: readiness goes DOWN at once (probes and Prometheus see it), then the process exits
 * after the grace period, so the SLI integrity check reports STALE (D-4). With FAULT_CRASH=true in
 * the environment the service crash-loops; set through /admin/faults it crashes once and the
 * restart comes back healthy.
 */
public class FaultCrash {

    private static final Logger log = LoggerFactory.getLogger(FaultCrash.class);

    private final ApplicationContext context;
    private final FaultState state;
    private final Duration grace;
    private final AtomicBoolean scheduled = new AtomicBoolean();

    public FaultCrash(ApplicationContext context, FaultState state, Duration grace) {
        this.context = context;
        this.state = state;
        this.grace = grace;
    }

    @EventListener(ApplicationReadyEvent.class)
    public void crashIfSeeded() {
        if (state.current().crash()) {
            schedule();
        }
    }

    public void schedule() {
        if (!scheduled.compareAndSet(false, true)) {
            return;
        }
        log.warn("FAULT_CRASH: readiness DOWN now, exiting in {}", grace);
        AvailabilityChangeEvent.publish(context, ReadinessState.REFUSING_TRAFFIC);
        Thread.ofVirtual().name("fault-crash").start(() -> {
            try {
                Thread.sleep(grace);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
            System.exit(SpringApplication.exit(context, () -> 1));
        });
    }
}
