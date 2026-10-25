package dev.budgetguard.inventory;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.util.Map;
import java.util.OptionalLong;
import java.util.concurrent.atomic.AtomicBoolean;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.micrometer.metrics.test.autoconfigure.AutoConfigureMetrics;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Primary;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;

@SpringBootTest
@AutoConfigureMockMvc
@AutoConfigureMetrics
class InventoryServiceTest {

    static final AtomicBoolean STORE_DOWN = new AtomicBoolean();

    /** The real in-memory store, wrapped so a test can make reads fail. */
    @TestConfiguration
    static class FlakyStore {
        @Bean
        @Primary
        StockStore flaky() {
            StockStore real = new StockStore.InMemory(Map.of("SKU-1", 10L, "SKU-0", 0L));
            return new StockStore() {
                @Override
                public OptionalLong available(String sku) {
                    if (STORE_DOWN.get()) {
                        throw new IllegalStateException("store unavailable");
                    }
                    return real.available(sku);
                }

                @Override
                public boolean reserve(String sku, long qty) {
                    return real.reserve(sku, qty);
                }
            };
        }
    }

    @Autowired
    MockMvc mvc;

    @AfterEach
    void storeUp() {
        STORE_DOWN.set(false);
    }

    @Test
    void reserveTakesStockAndConflictsWhenEmpty() throws Exception {
        mvc.perform(post("/reserve").contentType(MediaType.APPLICATION_JSON).content("{\"sku\":\"SKU-1\",\"qty\":3}"))
                .andExpect(status().isOk());
        mvc.perform(get("/stock/SKU-1")).andExpect(jsonPath("$.available").value(7)).andExpect(jsonPath("$.stale").value(false));
        mvc.perform(post("/reserve").contentType(MediaType.APPLICATION_JSON).content("{\"sku\":\"SKU-0\",\"qty\":1}"))
                .andExpect(status().isConflict());
        mvc.perform(get("/stock/NOPE")).andExpect(status().isNotFound());
        mvc.perform(post("/reserve").contentType(MediaType.APPLICATION_JSON).content("{\"sku\":\"SKU-1\",\"qty\":0}"))
                .andExpect(status().isBadRequest());

        String prom = mvc.perform(get("/actuator/prometheus")).andReturn().getResponse().getContentAsString();
        assertThat(prom).contains("service=\"inventory-service\"").contains("le=\"0.8\"");
        assertThat(prom.lines().filter(l -> l.startsWith("http_server_requests_seconds_count{") && l.contains("status=\"4"))
                .allMatch(l -> l.contains("error_origin=\"none\""))).isTrue();
    }

    @Test
    void readsFallBackToStaleStockWhenTheStoreFails() throws Exception {
        mvc.perform(get("/stock/SKU-1")).andExpect(status().isOk());              // primes the last-known value
        STORE_DOWN.set(true);
        mvc.perform(get("/stock/SKU-1")).andExpect(status().isOk()).andExpect(jsonPath("$.stale").value(true));
        mvc.perform(get("/stock/SKU-0")).andExpect(status().isInternalServerError());   // never seen: no fallback
    }
}
