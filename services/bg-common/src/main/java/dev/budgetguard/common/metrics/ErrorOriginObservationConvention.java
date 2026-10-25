package dev.budgetguard.common.metrics;

import io.micrometer.common.KeyValues;
import org.springframework.http.server.observation.DefaultServerRequestObservationConvention;
import org.springframework.http.server.observation.ServerRequestObservationContext;

/**
 * Adds error_origin to http.server.requests (D-3). Read at observation stop, after the advice or
 * the fault filter set the request attribute. Bounded values: none, self, dep:&lt;service&gt;.
 */
public class ErrorOriginObservationConvention extends DefaultServerRequestObservationConvention {

    public static final String ATTR = "bg.error_origin";

    @Override
    public KeyValues getLowCardinalityKeyValues(ServerRequestObservationContext context) {
        Object origin = context.getCarrier().getAttribute(ATTR);
        return super.getLowCardinalityKeyValues(context)
                .and("error_origin", origin instanceof String s ? s : "none");
    }
}
