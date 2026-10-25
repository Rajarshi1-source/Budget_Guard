package dev.budgetguard.orders;

import dev.budgetguard.common.faults.FaultState;
import dev.budgetguard.common.http.DownstreamClient;
import io.github.resilience4j.circuitbreaker.CircuitBreakerRegistry;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/** One client per dependency; the name is the breaker name and the error_origin value (dep:&lt;name&gt;). */
@Configuration(proxyBeanMethods = false)
class DownstreamConfig {

    @Bean
    DownstreamClient authClient(@Value("${bg.downstream.auth-url}") String url, CircuitBreakerRegistry r, FaultState f) {
        return new DownstreamClient("auth-service", url, r, f);
    }

    @Bean
    DownstreamClient inventoryClient(@Value("${bg.downstream.inventory-url}") String url, CircuitBreakerRegistry r,
                                     FaultState f) {
        return new DownstreamClient("inventory-service", url, r, f);
    }

    @Bean
    DownstreamClient notificationsClient(@Value("${bg.downstream.notifications-url}") String url,
                                         CircuitBreakerRegistry r, FaultState f) {
        return new DownstreamClient("notifications-service", url, r, f);
    }
}
