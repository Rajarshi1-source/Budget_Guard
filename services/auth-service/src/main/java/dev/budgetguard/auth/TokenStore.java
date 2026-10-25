package dev.budgetguard.auth;

import java.security.SecureRandom;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.util.Base64;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.ConcurrentHashMap;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

/**
 * Opaque demo tokens kept in memory. auth-service is part of the system under test, not a real
 * identity provider: it exists to have an SLO and to fail on demand. The configured demo token is
 * accepted so k6 traffic needs no login.
 */
@Component
public class TokenStore {

    record Session(String subject, Instant expiresAt) {}

    private final Map<String, Session> sessions = new ConcurrentHashMap<>();
    private final SecureRandom random = new SecureRandom();
    private final String demoToken;
    private final Duration ttl;
    private final Clock clock = Clock.systemUTC();

    public TokenStore(@Value("${bg.auth.demo-token}") String demoToken, @Value("${bg.auth.token-ttl}") Duration ttl) {
        this.demoToken = demoToken;
        this.ttl = ttl;
    }

    public String issue(String subject) {
        byte[] bytes = new byte[32];
        random.nextBytes(bytes);
        String token = Base64.getUrlEncoder().withoutPadding().encodeToString(bytes);
        sessions.put(token, new Session(subject, clock.instant().plus(ttl)));
        return token;
    }

    public Optional<String> subjectFor(String token) {
        if (!demoToken.isBlank() && demoToken.equals(token)) {
            return Optional.of("demo");
        }
        Session s = sessions.get(token);
        if (s == null) {
            return Optional.empty();
        }
        if (clock.instant().isAfter(s.expiresAt())) {
            sessions.remove(token);
            return Optional.empty();
        }
        return Optional.of(s.subject());
    }

    public Duration ttl() {
        return ttl;
    }
}
