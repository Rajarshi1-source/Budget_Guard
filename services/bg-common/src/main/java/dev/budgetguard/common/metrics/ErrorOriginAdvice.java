package dev.budgetguard.common.metrics;

import io.github.resilience4j.circuitbreaker.CallNotPermittedException;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.beans.TypeMismatchException;
import org.springframework.http.HttpStatus;
import org.springframework.http.HttpStatusCode;
import org.springframework.http.ProblemDetail;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.web.ErrorResponse;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

/**
 * Classifies every failure for the error_origin label. Only real server faults count as "self";
 * a dependency failure is "dep:&lt;service&gt;"; client errors (400, 404, 405, 409 …) are "none" and
 * keep their status, so a bad request or an unknown path never burns the service's budget.
 */
@RestControllerAdvice
public class ErrorOriginAdvice {

    /** Thrown by DownstreamClient on dependency 5xx, timeout, connection failure or injected partition. */
    public static final class DownstreamException extends RuntimeException {
        private final String dependency;

        public DownstreamException(String dependency, String message, Throwable cause) {
            super(message, cause);
            this.dependency = dependency;
        }

        public String dependency() {
            return dependency;
        }
    }

    @ExceptionHandler(DownstreamException.class)
    ResponseEntity<ProblemDetail> downstream(DownstreamException ex, HttpServletRequest req) {
        return fail(req, "dep:" + ex.dependency(), HttpStatus.BAD_GATEWAY, "dependency failed: " + ex.dependency());
    }

    @ExceptionHandler(CallNotPermittedException.class)
    ResponseEntity<ProblemDetail> breakerOpen(CallNotPermittedException ex, HttpServletRequest req) {
        // Convention: breaker name == dependency service name
        return fail(req, "dep:" + ex.getCausingCircuitBreakerName(), HttpStatus.SERVICE_UNAVAILABLE,
                "circuit open: " + ex.getCausingCircuitBreakerName());
    }

    @ExceptionHandler({HttpMessageNotReadableException.class, TypeMismatchException.class})
    ResponseEntity<ProblemDetail> unreadable(Exception ex, HttpServletRequest req) {
        return fail(req, "none", HttpStatus.BAD_REQUEST, "malformed request");
    }

    @ExceptionHandler(Exception.class)
    ResponseEntity<ProblemDetail> other(Exception ex, HttpServletRequest req) {
        // ResponseStatusException, validation errors, unknown paths and wrong methods all implement
        // ErrorResponse: keep their status; only a 5xx among them is self-inflicted.
        if (ex instanceof ErrorResponse er) {
            HttpStatusCode status = er.getStatusCode();
            req.setAttribute(ErrorOriginObservationConvention.ATTR, status.is5xxServerError() ? "self" : "none");
            return ResponseEntity.status(status).body(er.getBody());
        }
        return fail(req, "self", HttpStatus.INTERNAL_SERVER_ERROR, "internal error");
    }

    private static ResponseEntity<ProblemDetail> fail(HttpServletRequest req, String origin, HttpStatus status,
                                                      String detail) {
        req.setAttribute(ErrorOriginObservationConvention.ATTR, origin);
        return ResponseEntity.status(status).body(ProblemDetail.forStatusAndDetail(status, detail));
    }
}
