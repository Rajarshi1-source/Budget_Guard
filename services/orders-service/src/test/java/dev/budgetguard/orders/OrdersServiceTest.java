package dev.budgetguard.orders;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.sun.net.httpserver.HttpServer;
import io.github.resilience4j.circuitbreaker.CircuitBreakerRegistry;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.micrometer.metrics.test.autoconfigure.AutoConfigureMetrics;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.http.MediaType;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.ResultActions;

/**
 * The D-3 contract through real HTTP: a stub server plays auth, inventory and notifications, and
 * every failure must land on the right error_origin.
 */
@SpringBootTest(properties = {"budgetguard.faults.admin-enabled=true", "budgetguard.faults.admin-token=t0ken"})
@AutoConfigureMockMvc
@AutoConfigureMetrics
class OrdersServiceTest {

    /** Status each stub endpoint answers with; 200 by default. */
    static final Map<String, Integer> STATUS = new ConcurrentHashMap<>();
    static final AtomicInteger AUTH_CALLS = new AtomicInteger();
    static final HttpServer STUB = startStub();

    static HttpServer startStub() {
        try {
            HttpServer s = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
            for (String path : new String[] {"/validate", "/reserve", "/notify"}) {
                s.createContext(path, ex -> {
                    if (path.equals("/validate")) {
                        AUTH_CALLS.incrementAndGet();
                    }
                    int code = STATUS.getOrDefault(path, path.equals("/notify") ? 202 : 200);
                    byte[] body = (code < 300 ? (path.equals("/validate") ? "{\"subject\":\"alice\"}" : "{}") : "{}")
                            .getBytes(StandardCharsets.UTF_8);
                    ex.getResponseHeaders().add("Content-Type", "application/json");
                    ex.sendResponseHeaders(code, body.length);
                    ex.getResponseBody().write(body);
                    ex.close();
                });
            }
            s.start();
            return s;
        } catch (IOException e) {
            throw new IllegalStateException(e);
        }
    }

    @DynamicPropertySource
    static void downstream(DynamicPropertyRegistry r) {
        String base = "http://127.0.0.1:" + STUB.getAddress().getPort();
        r.add("bg.downstream.auth-url", () -> base);
        r.add("bg.downstream.inventory-url", () -> base);
        r.add("bg.downstream.notifications-url", () -> base);
    }

    @AfterAll
    static void stopStub() {
        STUB.stop(0);
    }

    @Autowired
    MockMvc mvc;

    @Autowired
    CircuitBreakerRegistry breakers;

    @AfterEach
    void reset() throws Exception {
        STATUS.clear();
        breakers.getAllCircuitBreakers().forEach(cb -> cb.reset());
        mvc.perform(delete("/admin/faults").header("X-BG-Fault-Token", "t0ken"));
    }

    private ResultActions order(String sku) throws Exception {
        return mvc.perform(post("/orders").header("Authorization", "Bearer demo")
                .contentType(MediaType.APPLICATION_JSON).content("{\"sku\":\"" + sku + "\",\"qty\":1}"));
    }

    private boolean series(String status, String origin) throws Exception {
        String prom = mvc.perform(get("/actuator/prometheus")).andReturn().getResponse().getContentAsString();
        return prom.lines().anyMatch(l -> l.startsWith("http_server_requests_seconds_count{")
                && l.contains("uri=\"/orders\"") && l.contains("status=\"" + status + "\"")
                && l.contains("error_origin=\"" + origin + "\"") && l.contains("service=\"orders-service\""));
    }

    @Test
    void happyPathCreatesTheOrder() throws Exception {
        order("SKU-1").andExpect(status().isCreated());
        assertThat(series("201", "none")).isTrue();
    }

    @Test
    void authOutageIsChargedToAuthNotOrders() throws Exception {
        STATUS.put("/validate", 503);
        order("SKU-1").andExpect(status().isBadGateway());
        assertThat(series("502", "dep:auth-service")).isTrue();
        assertThat(series("502", "self")).isFalse();
    }

    @Test
    void inventoryOutageIsChargedToInventory() throws Exception {
        STATUS.put("/reserve", 500);
        order("SKU-1").andExpect(status().isBadGateway());
        assertThat(series("502", "dep:inventory-service")).isTrue();
    }

    @Test
    void expectedClientErrorsFromDependenciesKeepTheir4xx() throws Exception {
        STATUS.put("/validate", 401);
        order("SKU-1").andExpect(status().isUnauthorized());
        STATUS.put("/validate", 200);
        STATUS.put("/reserve", 409);
        order("SKU-0").andExpect(status().isConflict());
        mvc.perform(post("/orders").contentType(MediaType.APPLICATION_JSON).content("{\"sku\":\"SKU-1\",\"qty\":1}"))
                .andExpect(status().isUnauthorized());                     // no token at all
        assertThat(series("401", "none")).isTrue();
        assertThat(series("409", "none")).isTrue();
    }

    @Test
    void injectedPartitionNeverReachesAuthAndIsChargedToAuth() throws Exception {
        mvc.perform(post("/admin/faults").header("X-BG-Fault-Token", "t0ken").contentType(MediaType.APPLICATION_JSON)
                .content("{\"dependency\":\"auth-service\",\"ttlSeconds\":60}")).andExpect(status().isOk());
        int before = AUTH_CALLS.get();
        order("SKU-1").andExpect(status().isBadGateway());
        assertThat(AUTH_CALLS.get()).isEqualTo(before);                    // FAULT_DEPENDENCY: never sent
        assertThat(series("502", "dep:auth-service")).isTrue();
    }

    @Test
    void openBreakerFailsFastAndIsStillChargedToAuth() throws Exception {
        STATUS.put("/validate", 503);
        for (int i = 0; i < 10; i++) {                                    // minimumNumberOfCalls = 10, 100% failures
            order("SKU-1").andExpect(status().isBadGateway());
        }
        int before = AUTH_CALLS.get();
        order("SKU-1").andExpect(status().isServiceUnavailable());
        assertThat(AUTH_CALLS.get()).isEqualTo(before);                    // breaker open: no call made
        assertThat(series("503", "dep:auth-service")).isTrue();
    }
}
