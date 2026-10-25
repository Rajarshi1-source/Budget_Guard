package dev.budgetguard.common;

import dev.budgetguard.common.metrics.ErrorOriginAdvice.DownstreamException;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

/** Minimal app for the bg-common contract tests: one endpoint per classification path. */
@SpringBootApplication
public class TestApp {

    public record Body(@NotBlank String name) {}

    @RestController
    static class Endpoints {
        @GetMapping("/ok")
        String ok() {
            return "ok";
        }

        @GetMapping("/boom")
        String boom() {
            throw new IllegalStateException("bug");
        }

        @GetMapping("/missing")
        String missing() {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "no such thing");
        }

        @GetMapping("/unavailable")
        String unavailable() {
            throw new ResponseStatusException(HttpStatus.SERVICE_UNAVAILABLE, "draining");
        }

        @GetMapping("/dep")
        String dep() {
            throw new DownstreamException("auth-service", "auth returned 503", null);
        }

        @PostMapping("/validated")
        String validated(@Valid @RequestBody Body body) {
            return body.name();
        }
    }
}
