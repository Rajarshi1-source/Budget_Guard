package dev.budgetguard.auth;

import io.github.resilience4j.ratelimiter.RateLimiter;
import io.github.resilience4j.ratelimiter.RateLimiterConfig;
import java.time.Duration;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration(proxyBeanMethods = false)
class RateLimitConfig {

    @Bean
    RateLimiter loginLimiter(@Value("${bg.auth.login-rate-limit.limit-for-period}") int limit,
                             @Value("${bg.auth.login-rate-limit.refresh-period}") Duration period) {
        return RateLimiter.of("login", RateLimiterConfig.custom()
                .limitForPeriod(limit)
                .limitRefreshPeriod(period)
                .timeoutDuration(Duration.ZERO)            // never queue a login: answer 429 at once
                .build());
    }
}
