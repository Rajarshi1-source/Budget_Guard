package dev.budgetguard.notifications;

import dev.budgetguard.notifications.Dispatcher.Channel;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

/** POST /notify → 202. SLO: 99.0% availability; 95% of messages delivered within 2 minutes. */
@RestController
public class NotifyController {

    public record NotifyRequest(@NotNull Channel channel, @NotBlank @Size(max = 200) String to,
                                @NotBlank @Size(max = 1000) String message) {}

    public record Accepted(int queueDepth) {}

    private final Dispatcher dispatcher;

    public NotifyController(Dispatcher dispatcher) {
        this.dispatcher = dispatcher;
    }

    @PostMapping("/notify")
    @ResponseStatus(HttpStatus.ACCEPTED)
    public Accepted notify(@Valid @RequestBody NotifyRequest req) {
        if (!dispatcher.enqueue(req.channel(), req.to(), req.message())) {
            throw new ResponseStatusException(HttpStatus.SERVICE_UNAVAILABLE, "queue full");   // self-inflicted
        }
        return new Accepted(dispatcher.depth());
    }
}
