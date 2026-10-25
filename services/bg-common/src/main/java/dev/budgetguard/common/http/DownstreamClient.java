package dev.budgetguard.common.http;

import dev.budgetguard.common.faults.FaultState;
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

/**
 * Service-to-service calls: 500 ms connect / 2 s read timeouts, one circuit breaker per dependency
 * (named after it, read by ErrorOriginAdvice), 5xx/timeout/partition → DownstreamException, 4xx
 * propagate unchanged for the caller to translate. No stacked retries: a retry inside a 2 s budget
 * multiplies load on a failing dependency.
 */
public final class DownstreamClient {

    private final String dependency;
    private final RestClient http;
    private final CircuitBreaker breaker;
    private final FaultState faults;

    public DownstreamClient(String dependency, String baseUrl, CircuitBreakerRegistry registry, FaultState faults) {
        var factory = new SimpleClientHttpRequestFactory();
        factory.setConnectTimeout(Duration.ofMillis(500));
        factory.setReadTimeout(Duration.ofSeconds(2));
        this.dependency = dependency;
        this.http = RestClient.builder().baseUrl(baseUrl).requestFactory(factory).build();
        this.breaker = registry.circuitBreaker(dependency);
        this.faults = faults;
    }

    public <T> T call(Supplier<T> request) {
        Supplier<T> guarded = CircuitBreaker.decorateSupplier(breaker, () -> {
            if (dependency.equals(faults.current().dependency())) {          // FAULT_DEPENDENCY: simulated partition
                throw new DownstreamException(dependency, "injected partition", null);
            }
            try {
                return request.get();
            } catch (HttpServerErrorException | ResourceAccessException e) {
                throw new DownstreamException(dependency, e.getMessage(), e);
            }
        });
        return guarded.get();
    }

    public RestClient http() {
        return http;
    }

    public String dependency() {
        return dependency;
    }

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
