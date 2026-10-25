package dev.budgetguard.common.faults;

import dev.budgetguard.common.faults.FaultState.Faults;
import jakarta.validation.Valid;
import jakarta.validation.constraints.DecimalMax;
import jakarta.validation.constraints.DecimalMin;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Map;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

/** Demo-only fault switch (security-and-api.md §4.1, T-7): 404 when disabled, constant-time token check. */
@RestController
@RequestMapping("/admin/faults")
public class FaultAdminController {

    public record FaultRequest(@DecimalMin("0.0") @DecimalMax("1.0") Double errorRate,
                               @Min(0) @Max(10_000) Long latencyMs,
                               String dependency,
                               Boolean crash,
                               @Min(1) @Max(3600) Long ttlSeconds) {}

    private final FaultState state;
    private final FaultCrash crash;
    private final boolean enabled;
    private final byte[] token;

    public FaultAdminController(FaultState state, FaultCrash crash,
                                @Value("${budgetguard.faults.admin-enabled:false}") boolean enabled,
                                @Value("${budgetguard.faults.admin-token:}") String token) {
        this.state = state;
        this.crash = crash;
        this.enabled = enabled && !token.isBlank();
        this.token = token.getBytes(StandardCharsets.UTF_8);
    }

    private void authorize(String presented) {
        if (!enabled) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND);          // do not advertise the endpoint
        }
        byte[] p = presented == null ? new byte[0] : presented.getBytes(StandardCharsets.UTF_8);
        if (!MessageDigest.isEqual(token, p)) {
            throw new ResponseStatusException(HttpStatus.UNAUTHORIZED);
        }
    }

    @GetMapping
    public Map<String, Object> get(@RequestHeader(value = "X-BG-Fault-Token", required = false) String t) {
        authorize(t);
        return Map.of("faults", state.current(), "secondsLeft", state.secondsLeft());
    }

    @PostMapping
    public Map<String, Object> replace(@RequestHeader(value = "X-BG-Fault-Token", required = false) String t,
                                       @Valid @RequestBody FaultRequest r) {
        authorize(t);
        var f = new Faults(r.errorRate() == null ? 0 : r.errorRate(), r.latencyMs() == null ? 0 : r.latencyMs(),
                           r.dependency(), Boolean.TRUE.equals(r.crash()));
        state.replace(f, r.ttlSeconds() == null ? 900 : r.ttlSeconds());   // missing fields mean "off"
        if (f.crash()) {
            crash.schedule();                                              // FAULT_CRASH via the endpoint
        }
        return get(t);
    }

    @DeleteMapping
    public Map<String, Object> reset(@RequestHeader(value = "X-BG-Fault-Token", required = false) String t) {
        authorize(t);
        state.reset();
        return get(t);
    }
}
