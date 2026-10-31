// Merge storm (plan W8.7, Rev 2 §11.2): many merges land at once, so GitHub sends a burst of signed
// deployment_protection_rule webhooks across every service. Each iteration is a distinct commit and
// deployment id, so the verdict cache never helps; the per-service single-flight lock serialises
// evaluations, which is exactly where a slow 30-day query hurts.
//
// The verdict latency itself is measured by the gate worker (enqueue to decision, from the stream
// entry id): load/bench-ab.sh collects it per delivery. This script only fires the burst.
//   -e SECRET=<webhook secret> -e TAG=<delivery prefix> [-e MERGES=40] [-e VUS=40] [-e BASE=...]
import http from 'k6/http';
import crypto from 'k6/crypto';
import exec from 'k6/execution';
import { check } from 'k6';

const MERGES = Number(__ENV.MERGES || 40);
const SERVICES = (__ENV.SERVICES || 'auth-service,orders-service,inventory-service,notifications-service').split(',');
const REPO = __ENV.REPO || 'Rajarshi1-source/Budget_Guard';
const BASE = __ENV.BASE || 'http://platform-api:8000';
const TAG = __ENV.TAG || 'storm';
// One seed for every VU: module code runs once per VU, so a per-VU Date.now() collides ids
// across VUs and the gate (correctly) dedupes the duplicates. bench-ab.sh passes it.
const SEED = Number(__ENV.SEED || 1000);

export const options = {
  scenarios: {
    storm: { executor: 'shared-iterations', vus: Number(__ENV.VUS || 40), iterations: MERGES, maxDuration: '2m' },
  },
  thresholds: { checks: ['rate==1.0'] },        // every delivery must be accepted (2xx)
};

export default function () {
  const i = exec.scenario.iterationInTest;
  const run = SEED + i;
  const service = SERVICES[i % SERVICES.length];
  const sha = run.toString(16).padStart(40, '0');
  const body = JSON.stringify({
    action: 'requested',
    environment: `production-${service}`,
    installation: { id: Number(__ENV.INSTALLATION_ID || 0) },
    repository: { full_name: REPO },
    deployment: { id: run * 10 + 7, sha },
    deployment_callback_url: `https://api.github.com/repos/${REPO}/actions/runs/${run}/deployment_protection_rule`,
  });
  const r = http.post(`${BASE}/v1/github/webhook`, body, {
    headers: {
      'Content-Type': 'application/json',
      'X-GitHub-Event': 'deployment_protection_rule',
      'X-GitHub-Delivery': `${TAG}-${run}`,
      'X-Hub-Signature-256': `sha256=${crypto.hmac('sha256', __ENV.SECRET, body, 'hex')}`,
    },
  });
  check(r, { accepted: (x) => x.status >= 200 && x.status < 300 });
}
