// REQ-09 load test: many polling terminals voting at once.
//
// Each iteration is one voter at one terminal, doing the whole vote flow:
//   log in  ->  get the ballot paper  ->  cast the ballot
// Every simulated terminal has its own address (X-Forwarded-For), as real
// terminals would. Run it with scripts/run_load_test.py, which starts k6 in
// Docker next to the load balancer and compares replica counts.
//
// Settings (environment variables):
//   BASE_URL     http://lb:8081   the load balancer, behind the REQ-07 nginx edge
//   TERMINALS    50               concurrent terminals (k6 virtual users)
//   VOTES        400              votes to cast in this run
//   FIRST_VOTER  1                first voter number: load001, load002, ...
//   ELECTION_ID  1
//   LABEL        run              name of this run in the results file

import http from 'k6/http';
import { check } from 'k6';
import exec from 'k6/execution';
import { Counter, Trend } from 'k6/metrics';

const BASE = __ENV.BASE_URL || 'http://lb:8081';
const TERMINALS = parseInt(__ENV.TERMINALS || '50', 10);
const VOTES = parseInt(__ENV.VOTES || '400', 10);
const FIRST = parseInt(__ENV.FIRST_VOTER || '1', 10);
const ELECTION = parseInt(__ENV.ELECTION_ID || '1', 10);
const LABEL = __ENV.LABEL || 'run';
const PASSWORD = 'ChangeMe123!';   // demo password from app/seed.py

export const options = {
  scenarios: {
    polling_day: {
      executor: 'shared-iterations',   // each voter votes exactly once
      vus: TERMINALS,
      iterations: VOTES,
      maxDuration: '20m',
    },
  },
  summaryTrendStats: ['avg', 'med', 'p(95)', 'max'],
};

const voteFlow = new Trend('vote_flow_duration', true);   // login to receipt, per voter
const castTime = new Trend('cast_duration', true);
const recorded = new Counter('votes_recorded');
const failed = new Counter('votes_failed');

function voterName(n) {
  return 'load' + String(n).padStart(3, '0');            // matches seed-demo-voters --prefix load
}

function terminalAddress(n) {
  return `10.${(n >> 16) & 255}.${(n >> 8) & 255}.${n & 255}`;
}

export default function () {
  const n = FIRST + exec.scenario.iterationInTest;
  const headers = { 'Content-Type': 'application/json', 'X-Forwarded-For': terminalAddress(n) };
  const started = Date.now();

  const login = http.post(`${BASE}/api/auth/login`,
    JSON.stringify({ username: voterName(n), password: PASSWORD }),
    { headers, tags: { step: 'login' } });
  if (!check(login, { 'login 200': (r) => r.status === 200 })) { failed.add(1); return; }
  headers.Authorization = `Bearer ${login.json('access_token')}`;

  const paper = http.get(`${BASE}/api/voting/ballot-paper?election_id=${ELECTION}`,
    { headers, tags: { step: 'ballot_paper' } });
  if (!check(paper, { 'ballot paper 200': (r) => r.status === 200 })) { failed.add(1); return; }
  const p = paper.json();

  const ballot = {
    election_id: ELECTION,
    channel: 'online',
    house_preferences: p.house_candidates.map((c) => c.id),
    senate_atl: Object.fromEntries(p.senate_groups.slice(0, 6).map((g, i) => [g.group_code, i + 1])),
  };
  const cast = http.post(`${BASE}/api/voting/cast`, JSON.stringify(ballot),
    { headers, tags: { step: 'cast' } });
  castTime.add(cast.timings.duration);
  if (check(cast, { 'cast 201': (r) => r.status === 201 })) {
    recorded.add(1);
    voteFlow.add(Date.now() - started);
  } else {
    failed.add(1);
  }
}

function metric(data, name, stat) {
  const m = data.metrics[name];
  return m ? m.values[stat] : 0;
}

export function handleSummary(data) {
  const seconds = data.state.testRunDurationMs / 1000;
  const votes = metric(data, 'votes_recorded', 'count');
  const result = {
    label: LABEL,
    terminals: TERMINALS,
    votes_requested: VOTES,
    votes_recorded: votes,
    votes_failed: metric(data, 'votes_failed', 'count'),
    duration_s: seconds,
    votes_per_second: votes / seconds,
    vote_flow_ms: {
      avg: metric(data, 'vote_flow_duration', 'avg'),
      median: metric(data, 'vote_flow_duration', 'med'),
      p95: metric(data, 'vote_flow_duration', 'p(95)'),
      max: metric(data, 'vote_flow_duration', 'max'),
    },
    cast_ms: {
      median: metric(data, 'cast_duration', 'med'),
      p95: metric(data, 'cast_duration', 'p(95)'),
    },
    http_failures: metric(data, 'http_req_failed', 'rate'),
  };
  const line = `${LABEL}: ${votes} votes in ${seconds.toFixed(1)} s = ` +
    `${result.votes_per_second.toFixed(2)} votes/s, vote flow median ` +
    `${result.vote_flow_ms.median.toFixed(0)} ms, p95 ${result.vote_flow_ms.p95.toFixed(0)} ms, ` +
    `failed ${result.votes_failed}\n`;
  return {
    stdout: line,
    [`/results/${LABEL}.json`]: JSON.stringify(result, null, 2),
  };
}
