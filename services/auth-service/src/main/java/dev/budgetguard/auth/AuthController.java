package dev.budgetguard.auth;

import io.github.resilience4j.ratelimiter.RateLimiter;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

/** POST /login and GET /validate. SLO: 99.95% availability, 99% of requests under 300 ms. */
@RestController
public class AuthController {

    public record LoginRequest(@NotBlank @Size(max = 100) String username, @NotBlank @Size(max = 200) String password) {}

    public record LoginResponse(String token, long expiresInSeconds) {}

    public record Validation(String subject) {}

    private final TokenStore tokens;
    private final RateLimiter loginLimiter;
    private final byte[] demoPassword;

    public AuthController(TokenStore tokens, RateLimiter loginLimiter,
                          @Value("${bg.auth.demo-password}") String demoPassword) {
        this.tokens = tokens;
        this.loginLimiter = loginLimiter;
        this.demoPassword = demoPassword.getBytes(StandardCharsets.UTF_8);
    }

    @PostMapping("/login")
    public LoginResponse login(@Valid @RequestBody LoginRequest req) {
        if (!loginLimiter.acquirePermission()) {
            // 429 is a 4xx: credential stuffing is rate limited without burning the availability budget
            throw new ResponseStatusException(HttpStatus.TOO_MANY_REQUESTS, "login rate limit");
        }
        if (!MessageDigest.isEqual(demoPassword, req.password().getBytes(StandardCharsets.UTF_8))) {
            throw new ResponseStatusException(HttpStatus.UNAUTHORIZED, "bad credentials");
        }
        return new LoginResponse(tokens.issue(req.username()), tokens.ttl().toSeconds());
    }

    /** The token travels in the Authorization header, never in the query string (security-and-api.md §4.1). */
    @GetMapping("/validate")
    public Validation validate(@RequestHeader(value = HttpHeaders.AUTHORIZATION, required = false) String authorization) {
        if (authorization == null || !authorization.startsWith("Bearer ")) {
            throw new ResponseStatusException(HttpStatus.UNAUTHORIZED, "missing bearer token");
        }
        return tokens.subjectFor(authorization.substring("Bearer ".length()))
                .map(Validation::new)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.UNAUTHORIZED, "invalid token"));
    }
}
