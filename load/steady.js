// Baseline traffic that keeps every service above its 600 events/hour traffic floor
// (budgetguard-spring-services §7). 20 req/s = 72,000/h through orders -> auth, inventory, notifications.
// Override with -e RATE=… -e DURATION=… -e BASE=…
import http from 'k6/http';
import { check } from 'k6';

const RATE = Number(__ENV.RATE || 20);
export const options = {
  scenarios: {
    steady: {
      executor: 'constant-arrival-rate',
      rate: RATE,
      timeUnit: '1s',
      duration: __ENV.DURATION || '24h',
      preAllocatedVUs: Math.max(5, RATE),
    },
  },
  thresholds: { checks: ['rate>0.0'] },   // report only; the SLOs, not k6, judge reliability
};

const BASE = __ENV.BASE || 'http://orders-service:8080';
const SKUS = ['SKU-1', 'SKU-2', 'SKU-3', 'SKU-4', 'SKU-5'];

export default function () {
  const sku = SKUS[Math.floor(Math.random() * SKUS.length)];
  const r = http.post(`${BASE}/orders`, JSON.stringify({ sku, qty: 1 }), {
    headers: { 'Content-Type': 'application/json', Authorization: 'Bearer demo' },
  });
  check(r, { 'not 5xx': (x) => x.status < 500 });
}
