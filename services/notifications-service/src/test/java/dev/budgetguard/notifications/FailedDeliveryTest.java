package dev.budgetguard.notifications;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import io.micrometer.core.instrument.MeterRegistry;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.micrometer.metrics.test.autoconfigure.AutoConfigureMetrics;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;

/** A fast failure must never count as good: it is in _count but not in the delivered bucket. */
@SpringBootTest(properties = {"bg.notifications.provider-failure-rate=1.0", "bg.notifications.max-attempts=2"})
@AutoConfigureMockMvc
@AutoConfigureMetrics
class FailedDeliveryTest {

    @Autowired
    MockMvc mvc;

    @Autowired
    MeterRegistry meters;

    @Test
    void failedDeliveryIsBad() throws Exception {
        mvc.perform(post("/notify").contentType(MediaType.APPLICATION_JSON)
                .content("{\"channel\":\"EMAIL\",\"to\":\"a@b.c\",\"message\":\"hi\"}")).andExpect(status().isAccepted());
        NotificationsServiceTest.await(() -> meters.find("notification.delivery").tag("outcome", "failed").timer() != null);

        String prom = mvc.perform(get("/actuator/prometheus")).andReturn().getResponse().getContentAsString();
        assertThat(prom.lines().filter(l -> l.startsWith("notification_delivery_seconds_count{")
                && l.contains("outcome=\"failed\"")).toList()).isNotEmpty();
        assertThat(prom.lines().filter(l -> l.startsWith("notification_delivery_seconds_bucket{")
                && l.contains("outcome=\"delivered\"")).toList()).isEmpty();
    }
}
