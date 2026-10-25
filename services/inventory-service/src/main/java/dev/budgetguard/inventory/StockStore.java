package dev.budgetguard.inventory;

import java.util.Map;
import java.util.OptionalLong;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicLong;

/** The stock "system of record". In-memory for the demo; the interface lets a test make it fail. */
public interface StockStore {

    /** Units available, or empty if the SKU is unknown. May throw if the store is unavailable. */
    OptionalLong available(String sku);

    /** Atomically take qty units; false when the SKU is unknown or not enough are left. */
    boolean reserve(String sku, long qty);

    /** Default implementation, seeded from bg.inventory.seed. */
    final class InMemory implements StockStore {
        private final Map<String, AtomicLong> stock = new ConcurrentHashMap<>();

        public InMemory(Map<String, Long> seed) {
            seed.forEach((sku, units) -> stock.put(sku, new AtomicLong(units)));
        }

        @Override
        public OptionalLong available(String sku) {
            AtomicLong units = stock.get(sku);
            return units == null ? OptionalLong.empty() : OptionalLong.of(units.get());
        }

        @Override
        public boolean reserve(String sku, long qty) {
            AtomicLong units = stock.get(sku);
            if (units == null) {
                return false;
            }
            while (true) {
                long current = units.get();
                if (current < qty) {
                    return false;
                }
                if (units.compareAndSet(current, current - qty)) {
                    return true;
                }
            }
        }
    }
}
