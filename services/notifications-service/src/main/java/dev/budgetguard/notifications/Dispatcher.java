package dev.budgetguard.notifications;

import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Timer;
import java.time.Duration;
import java.time.Instant;
import java.util.EnumMap;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.Semaphore;
import java.util.concurrent.ThreadLocalRandom;
import java.util.concurrent.TimeUnit;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.SmartLifecycle;
import org.springframework.stereotype.Component;

/**
 * In-memory queue + virtual-thread dispatcher (the durable path would be a Valkey stream or Kafka).
 * The SLI is end-to-end delivery from enqueue, not the HTTP 202: notification.delivery is recorded
 * with outcome=delivered|failed and a 2-minute SLO bucket, so a fast failure is never "good".
 */
@Component
public class Dispatcher implements SmartLifecycle {

    public enum Channel { EMAIL, SMS, PUSH }

    public record Message(String id, Channel channel, String to, String body, Instant enqueuedAt) {}

    private static final Logger log = LoggerFactory.getLogger(Dispatcher.class);

    private final BlockingQueue<Message> queue;
    private final Map<Channel, Semaphore> bulkheads = new EnumMap<>(Channel.class);
    private final MeterRegistry meters;
    private final int maxAttempts;
    private final double providerFailureRate;
    private volatile Thread loop;
    private volatile boolean running;

    public Dispatcher(MeterRegistry meters,
                      @Value("${bg.notifications.queue-capacity}") int capacity,
                      @Value("${bg.notifications.per-channel-concurrency}") int perChannel,
                      @Value("${bg.notifications.max-attempts}") int maxAttempts,
                      @Value("${bg.notifications.provider-failure-rate}") double providerFailureRate) {
        this.meters = meters;
        this.queue = new LinkedBlockingQueue<>(capacity);
        this.maxAttempts = maxAttempts;
        this.providerFailureRate = providerFailureRate;
        for (Channel c : Channel.values()) {
            bulkheads.put(c, new Semaphore(perChannel));                // one bulkhead per channel
        }
    }

    /** False when the queue is full; the controller turns that into a 503. */
    public boolean enqueue(Channel channel, String to, String body) {
        return queue.offer(new Message(UUID.randomUUID().toString(), channel, to, body, Instant.now()));
    }

    public int depth() {
        return queue.size();
    }

    @Override
    public void start() {
        running = true;
        loop = Thread.ofVirtual().name("notifications-dispatcher").start(() -> {
            while (running) {
                try {
                    Message m = queue.poll(500, TimeUnit.MILLISECONDS);
                    if (m != null) {
                        Thread.ofVirtual().start(() -> deliver(m));
                    }
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                    return;
                }
            }
        });
        meters.gauge("notification.queue.depth", queue, BlockingQueue::size);
    }

    private void deliver(Message m) {
        boolean delivered = false;
        Semaphore bulkhead = bulkheads.get(m.channel());
        for (int attempt = 1; attempt <= maxAttempts && !delivered; attempt++) {
            try {
                if (!bulkhead.tryAcquire(5, TimeUnit.SECONDS)) {
                    continue;                                            // channel saturated: retry
                }
                try {
                    delivered = send(m);
                } finally {
                    bulkhead.release();
                }
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                break;
            }
        }
        Timer.builder("notification.delivery")
                .description("End-to-end delivery time from enqueue")
                .serviceLevelObjectives(Duration.ofMinutes(2))
                .tag("channel", m.channel().name().toLowerCase())
                .tag("outcome", delivered ? "delivered" : "failed")
                .register(meters)
                .record(Duration.between(m.enqueuedAt(), Instant.now()));
        if (!delivered) {
            log.warn("notification {} to {} failed after {} attempts", m.id(), m.channel(), maxAttempts);
        }
    }

    /** Simulated provider call: 5–30 ms, failing at the configured rate. */
    private boolean send(Message m) throws InterruptedException {
        Thread.sleep(ThreadLocalRandom.current().nextLong(5, 31));
        return ThreadLocalRandom.current().nextDouble() >= providerFailureRate;
    }

    @Override
    public void stop() {
        running = false;
        if (loop != null) {
            loop.interrupt();
        }
    }

    @Override
    public boolean isRunning() {
        return running;
    }
}
