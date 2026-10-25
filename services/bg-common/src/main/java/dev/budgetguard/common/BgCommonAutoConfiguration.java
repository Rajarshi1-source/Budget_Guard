package dev.budgetguard.common;

import dev.budgetguard.common.faults.FaultAdminController;
import dev.budgetguard.common.faults.FaultCrash;
import dev.budgetguard.common.faults.FaultFilter;
import dev.budgetguard.common.faults.FaultState;
import dev.budgetguard.common.http.DownstreamClient;
import dev.budgetguard.common.metrics.CommonTagsConfig;
import dev.budgetguard.common.metrics.ErrorOriginAdvice;
import dev.budgetguard.common.metrics.ErrorOriginObservationConvention;
import io.github.resilience4j.circuitbreaker.CircuitBreakerRegistry;
import io.micrometer.core.instrument.MeterRegistry;
import java.time.Duration;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.AutoConfiguration;
import org.springframework.boot.autoconfigure.condition.ConditionalOnMissingBean;
import org.springframework.boot.autoconfigure.condition.ConditionalOnWebApplication;
import org.springframework.context.ApplicationContext;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Import;
import org.springframework.core.env.Environment;

/**
 * Everything every service under test needs, registered by Spring Boot auto-configuration (the
 * services' component scan covers dev.budgetguard.&lt;service&gt;, not dev.budgetguard.common).
 */
@AutoConfiguration
@ConditionalOnWebApplication(type = ConditionalOnWebApplication.Type.SERVLET)
@Import({CommonTagsConfig.class, ErrorOriginAdvice.class, FaultAdminController.class})
public class BgCommonAutoConfiguration {

    @Bean
    ErrorOriginObservationConvention errorOriginObservationConvention() {
        return new ErrorOriginObservationConvention();
    }

    @Bean
    FaultState faultState(Environment env) {
        return new FaultState(env);
    }

    @Bean
    FaultFilter faultFilter(FaultState state) {
        return new FaultFilter(state);
    }

    @Bean
    FaultCrash faultCrash(ApplicationContext context, FaultState state,
                          @Value("${budgetguard.faults.crash-grace:10s}") Duration grace) {
        return new FaultCrash(context, state, grace);
    }

    @Bean
    @ConditionalOnMissingBean
    CircuitBreakerRegistry circuitBreakerRegistry(MeterRegistry meters) {
        return DownstreamClient.registry(meters);
    }
}
