package dev.budgetguard.common;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.util.Arrays;
import java.util.Map;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.micrometer.metrics.test.autoconfigure.AutoConfigureMetrics;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;

/**
 * The metric contract the SLO rules read (budgetguard-slo-rules): service tag, SLO histogram
 * buckets, and error_origin for every failure class, measured through the real observation filter.
 */
@SpringBootTest(classes = TestApp.class)
@AutoConfigureMockMvc
@AutoConfigureMetrics
class MetricsContractTest {

    private static final String TOKEN = "test-fault-token";

    @Autowired
    MockMvc mvc;

    @AfterEach
    void resetFaults() throws Exception {
        mvc.perform(delete("/admin/faults").header("X-BG-Fault-Token", TOKEN));
    }

    private String scrape() throws Exception {
        return mvc.perform(get("/actuator/prometheus")).andReturn().getResponse().getContentAsString();
    }

    /** True when a http_server_requests_seconds_count series carries every given label. */
    private boolean hasSeries(String prom, Map<String, String> labels) {
        return Arrays.stream(prom.split("\n"))
                .filter(l -> l.startsWith("http_server_requests_seconds_count{"))
                .anyMatch(l -> labels.entrySet().stream().allMatch(e -> l.contains(e.getKey() + "=\"" + e.getValue() + "\"")));
    }

    @Test
    void serviceTagAndSloBucketsArePublished() throws Exception {
        mvc.perform(get("/ok")).andExpect(status().isOk());
        String prom = scrape();
        assertThat(hasSeries(prom, Map.of("uri", "/ok", "status", "200", "error_origin", "none", "service", "bg-common-test")))
                .isTrue();
        assertThat(prom).contains("http_server_requests_seconds_bucket{")
                .contains("le=\"0.3\"").contains("le=\"0.5\"").contains("le=\"0.8\"").contains("le=\"2.0\"");
    }

    @Test
    void unhandledBugIsSelfInflicted500() throws Exception {
        mvc.perform(get("/boom")).andExpect(status().isInternalServerError());
        assertThat(hasSeries(scrape(), Map.of("uri", "/boom", "status", "500", "error_origin", "self"))).isTrue();
    }

    @Test
    void dependencyFailureIsChargedToTheDependency() throws Exception {
        mvc.perform(get("/dep")).andExpect(status().isBadGateway());
        assertThat(hasSeries(scrape(), Map.of("uri", "/dep", "status", "502", "error_origin", "dep:auth-service")))
                .isTrue();
    }

    @Test
    void deliberate404AndValidationErrorsAreNotSelfInflicted() throws Exception {
        mvc.perform(get("/missing")).andExpect(status().isNotFound());
        mvc.perform(post("/validated").contentType(MediaType.APPLICATION_JSON).content("{\"name\":\"\"}"))
                .andExpect(status().isBadRequest());
        mvc.perform(post("/validated").contentType(MediaType.APPLICATION_JSON).content("not json"))
                .andExpect(status().isBadRequest());
        mvc.perform(get("/no/such/path")).andExpect(status().isNotFound());
        mvc.perform(delete("/ok")).andExpect(status().isMethodNotAllowed());
        String prom = scrape();
        assertThat(hasSeries(prom, Map.of("uri", "/missing", "status", "404", "error_origin", "none"))).isTrue();
        assertThat(hasSeries(prom, Map.of("uri", "/validated", "status", "400", "error_origin", "none"))).isTrue();
        assertThat(hasSeries(prom, Map.of("status", "405", "error_origin", "none"))).isTrue();
        assertThat(prom).doesNotContain("status=\"400\",uri=\"/validated\",error_origin=\"self\"");
        assertThat(Arrays.stream(prom.split("\n"))
                .filter(l -> l.startsWith("http_server_requests_seconds_count{") && l.contains("error_origin=\"self\""))
                .noneMatch(l -> l.contains("status=\"4"))).isTrue();
    }

    @Test
    void server5xxFromResponseStatusIsSelfInflicted() throws Exception {
        mvc.perform(get("/unavailable")).andExpect(status().isServiceUnavailable());
        assertThat(hasSeries(scrape(), Map.of("uri", "/unavailable", "status", "503", "error_origin", "self"))).isTrue();
    }

    @Test
    void injectedFaultIsMeasuredAndLabelledSelf() throws Exception {
        mvc.perform(post("/admin/faults").header("X-BG-Fault-Token", TOKEN).contentType(MediaType.APPLICATION_JSON)
                .content("{\"errorRate\":1.0,\"ttlSeconds\":60}")).andExpect(status().isOk());
        mvc.perform(get("/ok")).andExpect(status().isInternalServerError());
        mvc.perform(get("/actuator/health")).andExpect(status().isOk());       // probes are never faulted
        // The fault filter answers before handler mapping, so no URI pattern exists yet: uri="UNKNOWN".
        // Still counted by the SLO rules (they only exclude /actuator.*), and the label stays bounded.
        assertThat(hasSeries(scrape(), Map.of("uri", "UNKNOWN", "status", "500", "error_origin", "self",
                "service", "bg-common-test"))).isTrue();
    }

    @Test
    void faultEndpointRequiresTheTokenAndBoundsTheTtl() throws Exception {
        mvc.perform(get("/admin/faults")).andExpect(status().isUnauthorized());
        mvc.perform(get("/admin/faults").header("X-BG-Fault-Token", "wrong")).andExpect(status().isUnauthorized());
        mvc.perform(post("/admin/faults").header("X-BG-Fault-Token", TOKEN).contentType(MediaType.APPLICATION_JSON)
                .content("{\"errorRate\":0.5,\"ttlSeconds\":7200}")).andExpect(status().isBadRequest());
        mvc.perform(post("/admin/faults").header("X-BG-Fault-Token", TOKEN).contentType(MediaType.APPLICATION_JSON)
                .content("{\"errorRate\":1.5}")).andExpect(status().isBadRequest());
        mvc.perform(get("/admin/faults").header("X-BG-Fault-Token", TOKEN)).andExpect(status().isOk());
    }
}
