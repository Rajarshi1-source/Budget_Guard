package dev.budgetguard.inventory;

import java.util.Map;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration(proxyBeanMethods = false)
@EnableConfigurationProperties(InventoryConfig.InventoryProperties.class)
class InventoryConfig {

    /** bg.inventory.seed: SKU -> starting units. */
    @ConfigurationProperties("bg.inventory")
    record InventoryProperties(Map<String, Long> seed) {}

    @Bean
    StockStore stockStore(InventoryProperties props) {
        return new StockStore.InMemory(props.seed() == null ? Map.of() : props.seed());
    }
}
