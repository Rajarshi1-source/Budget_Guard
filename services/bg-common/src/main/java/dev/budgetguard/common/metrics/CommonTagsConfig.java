package dev.budgetguard.common.metrics;

import io.micrometer.core.instrument.MeterRegistry;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.micrometer.metrics.autoconfigure.MeterRegistryCustomizer;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/** Adds service="<spring.application.name>" to every meter; the SLO rules select on it. */
@Configuration(proxyBeanMethods = false)
public class CommonTagsConfig {

    @Bean
    MeterRegistryCustomizer<MeterRegistry> serviceTag(@Value("${spring.application.name}") String app) {
        return registry -> registry.config().commonTags("service", app);
    }
}
