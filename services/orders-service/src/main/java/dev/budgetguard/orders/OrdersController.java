package dev.budgetguard.orders;

import dev.budgetguard.common.http.DownstreamClient;
import dev.budgetguard.common.metrics.ErrorOriginAdvice.DownstreamException;
import jakarta.validation.Valid;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import java.util.Map;
import java.util.UUID;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.client.HttpClientErrorException;
import org.springframework.web.server.ResponseStatusException;

/**
 * POST /orders → validate (auth) → reserve (inventory) → notify (notifications).
 * SLO: 99.9% availability, 99% under 500 ms. When a dependency fails, the 5xx is labelled
 * dep:&lt;service&gt; by ErrorOriginAdvice, so the culprit's budget pays (D-3); expected client errors
 * from a dependency (bad token, no stock) are translated to the same 4xx here.
 */
@RestController
public class OrdersController {

    public record OrderRequest(@NotBlank String sku, @Min(1) @Max(100) int qty) {}

    public record Order(String orderId, String sku, int qty, String customer) {}

    record Validation(String subject) {}

    private final DownstreamClient auth;
    private final DownstreamClient inventory;
    private final DownstreamClient notifications;

    public OrdersController(@Qualifier("authClient") DownstreamClient auth,
                            @Qualifier("inventoryClient") DownstreamClient inventory,
                            @Qualifier("notificationsClient") DownstreamClient notifications) {
        this.auth = auth;
        this.inventory = inventory;
        this.notifications = notifications;
    }

    @PostMapping("/orders")
    @ResponseStatus(HttpStatus.CREATED)
    public Order create(@RequestHeader(value = HttpHeaders.AUTHORIZATION, required = false) String authorization,
                        @Valid @RequestBody OrderRequest req) {
        if (authorization == null || !authorization.startsWith("Bearer ")) {
            throw new ResponseStatusException(HttpStatus.UNAUTHORIZED, "missing bearer token");
        }
        String customer = validate(authorization);
        reserve(req);
        notifyCustomer(customer, req);
        return new Order(UUID.randomUUID().toString(), req.sku(), req.qty(), customer);
    }

    private String validate(String authorization) {
        try {
            Validation v = auth.call(() -> auth.http().get().uri("/validate")
                    .header(HttpHeaders.AUTHORIZATION, authorization)            // header, never ?t=
                    .retrieve().body(Validation.class));
            return v == null ? "unknown" : v.subject();
        } catch (HttpClientErrorException.Unauthorized e) {
            throw new ResponseStatusException(HttpStatus.UNAUTHORIZED, "invalid token");
        } catch (HttpClientErrorException e) {
            throw unexpected(auth, e);
        }
    }

    private void reserve(OrderRequest req) {
        try {
            inventory.call(() -> inventory.http().post().uri("/reserve").contentType(MediaType.APPLICATION_JSON)
                    .body(Map.of("sku", req.sku(), "qty", req.qty()))
                    .retrieve().toBodilessEntity());
        } catch (HttpClientErrorException.Conflict e) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "insufficient stock");
        } catch (HttpClientErrorException.NotFound e) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "unknown sku");
        } catch (HttpClientErrorException e) {
            throw unexpected(inventory, e);
        }
    }

    private void notifyCustomer(String customer, OrderRequest req) {
        try {
            notifications.call(() -> notifications.http().post().uri("/notify").contentType(MediaType.APPLICATION_JSON)
                    .body(Map.of("channel", "EMAIL", "to", customer,
                            "message", "Order placed: " + req.qty() + " x " + req.sku()))
                    .retrieve().toBodilessEntity());
        } catch (HttpClientErrorException e) {
            throw unexpected(notifications, e);
        }
    }

    /** A 4xx we did not expect means the two services disagree on the contract: charge the dependency. */
    private static DownstreamException unexpected(DownstreamClient client, HttpClientErrorException e) {
        return new DownstreamException(client.dependency(), "unexpected " + e.getStatusCode(), e);
    }
}
