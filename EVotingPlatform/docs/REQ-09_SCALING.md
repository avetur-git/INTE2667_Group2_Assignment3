# REQ-09: Scale from about 7,000 to 40,000 concurrent terminals

Report section 2.9, Option B: cloud autoscaling behind a load balancer,
validated by load testing.

**Prototype scope (partial).** 40,000 terminals cannot be tested on one
machine. The prototype shows the approach: a stateless API that scales by
adding replicas behind the load balancer, and a load test of the full vote flow
at 1, 2 and 3 replicas. The cloud autoscaling design and a capacity estimate for
40,000 terminals are below.

## What is built

| Task | Where |
|---|---|
| Stateless API, so replicas can be added | Shared databases and signing keys (REQ-08); REQ-07 rate-limit counters moved to Redis (`app/security/rate_limit.py`, `RATE_LIMIT_STORAGE_URL`); stateless JWT logins |
| Add replicas without changing configuration | `docker-compose.yml`: `docker compose up -d --scale web2=N` gives N+1 replicas; `docker/haproxy/haproxy.cfg` fills up to 8 slots from Docker DNS (`server-template`) |
| Fixed-size replicas | `cpus: 1.0` per replica, like a cloud instance or task: capacity grows by adding replicas |
| Load test of the vote flow | `loadtest/vote_flow.js` (k6) |
| Run at several replica counts and compare | `scripts/run_load_test.py`; results in `loadtest/results/` |

### What a replica keeps, and where it now lives

| State | Shared through |
|---|---|
| Voter, election, audit data; ballots | The same `evoting.db` / `ballots.db` (REQ-08) |
| Signing keys (ballot tokens, chain checkpoints, receipts, roll answers) | Shared `instance/` volume, each key created once (REQ-08) |
| Logins | JWTs signed with the shared secret: any replica accepts them |
| WebAuthn challenges (REQ-10) | Database |
| Rate-limit counters (REQ-07) | **Redis** (`ratelimit` service). Before REQ-09 each replica counted on its own, so with 3 replicas a client could make 3 times the login limit. The counting runs as one atomic Lua script on the Redis server's clock. If Redis is unreachable, each replica falls back to its own counters instead of failing |

Tests: `tests/test_scaling.py` (three replicas sharing one Redis: the limit
holds across replicas, a throttled client is audited once, fallback when Redis
is down) and `tests/test_availability.py` (two replicas taking concurrent votes:
one-vote rule, ballot chain and audit chain all intact).

## The load test

Each k6 virtual user is one polling terminal. Each iteration is one voter doing
the whole vote flow: **log in, get the ballot paper, cast the ballot**. Every
voter is new (`load001`, `load002`, ...) and every terminal has its own address,
as real terminals would. The test sends requests to the load balancer behind the
nginx edge: nginx limits each client address, and all simulated terminals run
on one machine. In production the edge (CloudFront and WAF, REQ-07) scales on
its own; this test measures the API tier, which is what replicas add capacity to.

Recorded per vote: time from login to receipt (vote flow), time to cast, votes
recorded and failed. Throughput = votes recorded / run time.

### Results

Run 23 October 2026, Docker Desktop on one Windows PC (16 cores), 50 concurrent
terminals, 300 votes per run, each replica limited to 1 CPU, SQLite database,
password hashing at production cost (bcrypt 12):

| Replicas | Votes recorded | Failed | Time (s) | Throughput (votes/s) | Speed-up | Vote flow median (ms) | Vote flow p95 (ms) | Cast median (ms) |
|---|---|---|---|---|---|---|---|---|
| 1 | 300 | 0 | 192.1 | 1.56 | 1.00x | 31,180 | 34,277 | 7,538 |
| 2 | 300 | 0 | 116.5 | 2.57 | 1.65x | 18,404 | 22,705 | 6,826 |
| 3 | 300 | 0 | 93.5 | 3.21 | 2.05x | 14,306 | 21,094 | 5,917 |

(Raw numbers: `loadtest/results/replicas-*.json` and `results.md`.)

**What the results show**

- All 900 votes were recorded, none failed, at every replica count.
- Throughput rises with each replica: 1.65x with 2, 2.05x with 3. Response
  times fall as replicas are added (median vote flow 31 s to 14 s; with 50
  terminals and one replica, most of that time is waiting in the load
  balancer's queue).
- The gain is less than linear. Logging in (bcrypt) is CPU work and spreads
  over the replicas, but every replica writes to one SQLite database, which
  accepts one writer at a time; the cast step stays at 6 to 7.5 s in every run.
  **The API tier scales; the shared database becomes the limit.** This is why
  the production design scales the database separately (below).

### Problems the load test found, and fixes

The first full run recorded almost no votes. Each problem below was found by
the load test, fixed, and the test run again:

| # | Under load | Cause | Fix |
|---|---|---|---|
| 1 | Almost every request failed with 503 (no server) | Replicas busy with logins answered `/health` too slowly; haproxy took every one of them out of rotation | Admission control: `maxconn 4` per replica, the rest queue in haproxy (`timeout queue 30s`); health-check timeout 2 s. Health checks do not count against `maxconn`, so a busy replica stays in rotation |
| 2 | Some votes failed with 500 "Could not append to the audit chain" | The REQ-04 audit chain read the last entry, then waited for the database write lock; by then others had appended, so it collided again on every retry | Take the write lock before reading the chain head (as the REQ-02 ballot chain already does), and a short random wait between retries |
| 3 | "database is locked" | SQLite's default 5 s wait for the writer lock | 30 s wait (SQLite only) |
| 4 | Some voters refused as already voted | Load test runner reused voters after a run with failures | Runner continues after the highest voter used |

Problem 2 is worth noting: a vote that failed there had already been stored,
so the voter saw an error for a vote that counted. Also seen under load, and
left to REQ-05: two workers archiving the same batch of audit entries at once
("UNIQUE constraint failed: audit_archive_batches.first_id"); it is logged and
does not affect voting.

After the fixes the REQ-08 failover was measured again: 16 of 16 votes
recorded with `web` killed mid-run, removed from rotation after 4.7 s and back
after 4.9 s.

## Capacity for 40,000 terminals (estimate)

From the measured 1.56 votes/s on one 1-CPU replica, one vote costs about
**0.64 CPU-seconds** in the API, most of it the deliberately slow password check.

| | 7,000 terminals | 40,000 terminals |
|---|---|---|
| Peak votes per second (each terminal finishing a voter every 3 minutes) | 39 | 222 |
| API CPU at peak (0.64 CPU-s per vote) | 25 vCPU | 142 vCPU |
| With 50% headroom | about 38 vCPU | about 215 vCPU |
| Database writes per second (about 6 per vote: vote record, spent token, ballot, receipt, audit entries) | about 230 | about 1,300 |

These are estimates: the 3-minute cycle is an assumption, the CPU cost is from a
development server on a PC, and the real figure has to come from a full-scale
test (below). They show the order of magnitude: 40,000 terminals need around a
hundred to two hundred API tasks, which autoscaling provides, and a database
that handles about 1,300 writes per second.

**Serialisation points.** The REQ-02 ballot chain and the REQ-04 audit chain
each accept one append at a time. At 222 votes/s that is 222 or more appends per
second on each chain head. PostgreSQL can lock and append a row in about a
millisecond, so this fits, but with limited room. If the full-scale test shows
the chains as the limit, the chains can be split (for example one per
electoral division) without weakening tamper evidence.

## Production design (documented only)

| Part | Design |
|---|---|
| API | Container image on ECS Fargate (or EKS), 1 vCPU / 2 GB tasks, in every availability zone of both regions (REQ-08); production WSGI server (gunicorn) instead of the Flask development server |
| Load balancer | ALB per region (REQ-06 `infra/`) with target-group health checks on `/health`; connection limits per target, as haproxy's `maxconn` here |
| Autoscaling | Target tracking on CPU (60%) and on ALB requests per target; minimum 2 tasks per zone (REQ-08), maximum sized from the capacity estimate plus margin |
| Known peaks | Scheduled scaling: scale out before polls open at 8 am and for the evening peak, rather than waiting for load to arrive; scale-in protection while polls are open |
| Shared state | ElastiCache for Redis (rate limits), RDS Proxy to pool database connections from hundreds of tasks |
| Database | Aurora PostgreSQL (REQ-08): writer sized for about 1,300 writes/s at peak with headroom, read replicas for results and audit reads; advisory locks for the chain heads |
| Validation | Before each election, a load test in a staging copy at full scale (40,000 virtual terminals, distributed k6 runners), including a zone failure during the test |

## Running it

```bash
cd EVotingPlatform
docker compose up --build -d
docker compose exec web flask --app run seed-demo            # once
python scripts/run_load_test.py                               # 1, 2 and 3 replicas, 400 votes each
python scripts/run_load_test.py --replicas 1,3 --votes 300 --terminals 50
```

The runner creates the load-test voters, scales the API, waits until haproxy
reports the right number of healthy replicas, runs k6 (Docker image
`grafana/k6:1.2.3`, nothing to install), writes `loadtest/results/results.md`,
and puts the stack back to two replicas. To scale by hand:
`docker compose up -d --scale web2=2`, and watch http://localhost:8404.

## Limits of the prototype

- One machine: replicas share its disk and memory bus; at 40,000 terminals the
  bottlenecks may be different ones.
- SQLite instead of PostgreSQL. It is the limit seen in the results; the
  production database scales differently.
- Flask's development server, not a production WSGI server.
- The load test bypasses the nginx edge (see above), and all load-test voters
  share one password hash so that thousands can be created quickly (each login
  still pays the full bcrypt cost).
- Autoscaling is manual here (`--scale`); the automatic policy is the
  production design above.
