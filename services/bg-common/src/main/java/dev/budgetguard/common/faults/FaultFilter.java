package dev.budgetguard.common.faults;

import dev.budgetguard.common.metrics.ErrorOriginObservationConvention;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.util.concurrent.ThreadLocalRandom;
import org.springframework.http.HttpStatus;
import org.springframework.web.filter.OncePerRequestFilter;

/**
 * FAULT_LATENCY_MS and FAULT_ERROR_RATE. Runs inside Spring's observation filter, so injected
 * failures are still measured; it labels them itself because exceptions thrown in a filter never
 * reach @RestControllerAdvice.
 */
public class FaultFilter extends OncePerRequestFilter {

    private final FaultState state;

    public FaultFilter(FaultState state) {
        this.state = state;
    }

    @Override
    protected boolean shouldNotFilter(HttpServletRequest req) {
        return req.getRequestURI().startsWith("/actuator") || req.getRequestURI().startsWith("/admin");
    }

    @Override
    protected void doFilterInternal(HttpServletRequest req, HttpServletResponse res, FilterChain chain)
            throws ServletException, IOException {
        var f = state.current();
        if (f.latencyMs() > 0) {
            try {
                Thread.sleep(f.latencyMs());                    // cheap on virtual threads
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
        }
        if (f.errorRate() > 0 && ThreadLocalRandom.current().nextDouble() < f.errorRate()) {
            req.setAttribute(ErrorOriginObservationConvention.ATTR, "self");
            res.sendError(HttpStatus.INTERNAL_SERVER_ERROR.value(), "injected fault");
            return;
        }
        chain.doFilter(req, res);
    }
}
