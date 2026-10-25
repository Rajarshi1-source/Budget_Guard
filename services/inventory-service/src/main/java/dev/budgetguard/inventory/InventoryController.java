package dev.budgetguard.inventory;

import jakarta.validation.Valid;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import java.util.Map;
import java.util.OptionalLong;
import java.util.concurrent.ConcurrentHashMap;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

/**
 * GET /stock/{sku} and POST /reserve. SLO: 99.5% availability, 99% under 800 ms. Reads fall back to
 * the last value seen, marked stale, when the store fails (Rev 2 §8: stale stock for seconds is
 * acceptable). Reservations never use the cache: selling stock we may not have is worse than a 5xx.
 */
@RestController
public class InventoryController {

    private static final Logger log = LoggerFactory.getLogger(InventoryController.class);

    public record Stock(String sku, long available, boolean stale) {}

    public record ReserveRequest(@NotBlank String sku, @Min(1) @Max(100) long qty) {}

    public record Reservation(String sku, long qty) {}

    private final StockStore store;
    private final Map<String, Long> lastKnown = new ConcurrentHashMap<>();

    public InventoryController(StockStore store) {
        this.store = store;
    }

    @GetMapping("/stock/{sku}")
    public Stock stock(@PathVariable String sku) {
        OptionalLong units;
        try {
            units = store.available(sku);
        } catch (RuntimeException e) {
            Long cached = lastKnown.get(sku);
            if (cached == null) {
                throw e;                                        // nothing to fall back to: a real 5xx (self)
            }
            log.warn("stock store failed for {}; serving stale value", sku);
            return new Stock(sku, cached, true);
        }
        if (units.isEmpty()) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "unknown sku");
        }
        lastKnown.put(sku, units.getAsLong());
        return new Stock(sku, units.getAsLong(), false);
    }

    @PostMapping("/reserve")
    public Reservation reserve(@Valid @RequestBody ReserveRequest req) {
        if (store.available(req.sku()).isEmpty()) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "unknown sku");
        }
        if (!store.reserve(req.sku(), req.qty())) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "insufficient stock");
        }
        return new Reservation(req.sku(), req.qty());
    }
}
