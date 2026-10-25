package dev.budgetguard.common;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;

/** Outside the demo profile the fault endpoint does not advertise itself (security-and-api.md T-7). */
@SpringBootTest(classes = TestApp.class, properties = "budgetguard.faults.admin-enabled=false")
@AutoConfigureMockMvc
class FaultAdminDisabledTest {

    @Autowired
    MockMvc mvc;

    @Test
    void endpointIs404EvenWithTheRightToken() throws Exception {
        mvc.perform(get("/admin/faults").header("X-BG-Fault-Token", "test-fault-token")).andExpect(status().isNotFound());
        mvc.perform(post("/admin/faults").header("X-BG-Fault-Token", "test-fault-token")
                .contentType(MediaType.APPLICATION_JSON).content("{\"errorRate\":1.0}")).andExpect(status().isNotFound());
    }
}
