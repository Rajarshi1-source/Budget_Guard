package dev.budgetguard.notifications;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import io.micrometer.core.instrument.MeterRegistry;
import java.time.Duration;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.micrometer.metrics.test.autoconfigure.AutoConfigureMetrics;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;

@SpringBootTest
@AutoConfigureMockMvc
@AutoConfigureMetrics
class NotificationsServiceTest {

    @Autowired
    MockMvc mvc;

    @Autowired
    MeterRegistry meters;

    static void await(java.util.function.BooleanSupplier condition) throws InterruptedException {
        long deadline = System.nanoTime() + Duration.ofSeconds(10).toNanos();
        while (!condition.getAsBoolean() && System.nanoTime() < deadline) {
            Thread.sleep(50);
        }
    }

    @Test
    void acceptedMessageIsDeliveredAndMeasuredFromEnqueue() throws Exception {
        mvc.perform(post("/notify").contentType(MediaType.APPLICATION_JSON)
                .content("{\"channel\":\"SMS\",\"to\":\"+911234567890\",\"message\":\"hi\"}")).andExpect(status().isAccepted());
        await(() -> meters.find("notification.delivery").tag("outcome", "delivered").timer() != null);

        String prom = mvc.perform(get("/actuator/prometheus")).andReturn().getResponse().getContentAsString();
        // the async SLI reads exactly this bucket: delivered within 2 minutes (budgetguard-spring-services §5)
        assertThat(prom.lines().filter(l -> l.startsWith("notification_delivery_seconds_bucket{")
                && l.contains("le=\"120.0\"") && l.contains("outcome=\"delivered\"")
                && l.contains("service=\"notifications-service\"")).toList()).isNotEmpty();
    }

    @Test
    void invalidRequestsAre400() throws Exception {
        mvc.perform(post("/notify").contentType(MediaType.APPLICATION_JSON)
                .content("{\"channel\":\"FAX\",\"to\":\"x\",\"message\":\"hi\"}")).andExpect(status().isBadRequest());
        mvc.perform(post("/notify").contentType(MediaType.APPLICATION_JSON)
                .content("{\"channel\":\"EMAIL\",\"to\":\"\",\"message\":\"hi\"}")).andExpect(status().isBadRequest());
    }
}
