package dev.budgetguard.auth;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.micrometer.metrics.test.autoconfigure.AutoConfigureMetrics;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;

/** Credential stuffing gets 429 (a 4xx), so the rate limiter never burns the availability budget. */
@SpringBootTest(properties = {
        "bg.auth.login-rate-limit.limit-for-period=2",
        "bg.auth.login-rate-limit.refresh-period=60s"})
@AutoConfigureMockMvc
@AutoConfigureMetrics
class LoginRateLimitTest {

    @Autowired
    MockMvc mvc;

    @Test
    void thirdLoginInTheWindowIs429WithErrorOriginNone() throws Exception {
        for (int i = 0; i < 2; i++) {
            mvc.perform(post("/login").contentType(MediaType.APPLICATION_JSON)
                    .content("{\"username\":\"a\",\"password\":\"demo\"}")).andExpect(status().isOk());
        }
        mvc.perform(post("/login").contentType(MediaType.APPLICATION_JSON)
                .content("{\"username\":\"a\",\"password\":\"demo\"}")).andExpect(status().isTooManyRequests());

        String prom = mvc.perform(get("/actuator/prometheus")).andReturn().getResponse().getContentAsString();
        assertThat(prom.lines()
                .filter(l -> l.startsWith("http_server_requests_seconds_count{") && l.contains("status=\"429\""))
                .toList())
                .singleElement().asString().contains("error_origin=\"none\"").doesNotContain("error_origin=\"self\"");
    }
}
