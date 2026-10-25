package dev.budgetguard.auth;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.jayway.jsonpath.JsonPath;
import java.util.Arrays;
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
class AuthServiceTest {

    @Autowired
    MockMvc mvc;

    private String login(String password) throws Exception {
        return mvc.perform(post("/login").contentType(MediaType.APPLICATION_JSON)
                        .content("{\"username\":\"alice\",\"password\":\"" + password + "\"}"))
                .andReturn().getResponse().getContentAsString();
    }

    @Test
    void loginIssuesATokenThatValidates() throws Exception {
        String token = JsonPath.read(login("demo"), "$.token");
        mvc.perform(get("/validate").header("Authorization", "Bearer " + token))
                .andExpect(status().isOk()).andExpect(jsonPath("$.subject").value("alice"));
    }

    @Test
    void demoTokenValidatesForLoadTraffic() throws Exception {
        mvc.perform(get("/validate").header("Authorization", "Bearer demo"))
                .andExpect(status().isOk()).andExpect(jsonPath("$.subject").value("demo"));
    }

    @Test
    void badCredentialsAndBadTokensAre401NotSelfInflicted() throws Exception {
        mvc.perform(post("/login").contentType(MediaType.APPLICATION_JSON)
                .content("{\"username\":\"alice\",\"password\":\"wrong\"}")).andExpect(status().isUnauthorized());
        mvc.perform(get("/validate")).andExpect(status().isUnauthorized());
        mvc.perform(get("/validate").header("Authorization", "Bearer nope")).andExpect(status().isUnauthorized());
        mvc.perform(get("/validate").param("t", "demo")).andExpect(status().isUnauthorized());   // query string is ignored

        String prom = mvc.perform(get("/actuator/prometheus")).andReturn().getResponse().getContentAsString();
        assertThat(Arrays.stream(prom.split("\n"))
                .filter(l -> l.startsWith("http_server_requests_seconds_count{") && l.contains("status=\"401\""))
                .allMatch(l -> l.contains("error_origin=\"none\""))).isTrue();
        assertThat(prom).contains("service=\"auth-service\"").contains("le=\"0.3\"");
    }
}
