# REQ-08: 99.9% availability during polling, automatic failover, paper fallback

Report section 2.8, Option B: active-active deployment across regions with
automatic health-check failover and a documented paper fallback.

**Prototype scope (partial).** Multi-region cannot be shown on one machine. Two
API replicas behind a health-checking load balancer show the same pattern: a
failed replica is detected and taken out of rotation automatically, and voting
continues on the other. The paper fallback is in
[REQ-08_PAPER_FALLBACK.md](REQ-08_PAPER_FALLBACK.md).

## Why failover must be automatic

99.9% of a 10-hour polling day allows **36 seconds** of downtime. No on-call
person can notice, diagnose and act in that time, so detection and failover
have to be done by health checks, without people.

## What is built

```
voter ─> nginx :9001 ─> haproxy (lb) ─┬─> web  :5000 ─┐
         REQ-07 limits   checks /health├─> web2 :5000 ─┼─> data/evoting.db, data/ballots.db
                         every 2 s     │               └─> instance/ (signing keys)
                                       └─ status page :8404 (local only)
```

| Part | File | Role |
|---|---|---|
| Two API replicas | `docker-compose.yml` (`web`, `web2`) | Same image and settings; `INSTANCE_NAME` tells them apart |
| Load balancer | `docker/haproxy/haproxy.cfg` | Round-robin over healthy replicas; active health checks; retries a failed connection on the other replica |
| Health endpoint | `GET /health` (`app/routes/main.py`) | Runs `SELECT 1` on both databases; `200 healthy` or `503 unhealthy`, with the replica name and each check |
| Demo | `scripts/failover_demo.py`, `flask seed-demo-voters` | Keeps voting while a replica is killed and shows which replica recorded each vote |

### Health checks and timing

| Setting | Value | Effect |
|---|---|---|
| `option httpchk GET /health`, `expect status 200` | | A replica that answers 503, errors or does not answer fails the check |
| `inter 2s` | check every 2 s | |
| `timeout check 1s` | | A hung replica fails within 1 s |
| `fall 2` | 2 failures in a row | **Removed within about 5 s** (2 intervals plus the check timeout; measured 4.8–5.6 s with `docker compose kill web`) |
| `rise 2` | 2 passes in a row | Returned to rotation about 4 s after it is healthy again (measured 4.5–5.0 s after `docker compose start web`) |
| `retries 2`, `option redispatch 1`, `timeout connect 1s` | | Before a dead replica is marked down, a request that cannot connect to it is retried on the other one. Retries happen only when the connection failed, so a vote is never sent twice |

A replica is also unhealthy when it is running but cannot reach a database
(`/health` returns 503), not only when it is down.

### Shared state between replicas

| State | How replicas share it |
|---|---|
| Voter, election and audit data; ballots | Same `evoting.db` and `ballots.db` on the shared `./data` volume (`BALLOT_DATABASE_URL` is now set explicitly, so the ballot database is no longer inside one container) |
| One vote per voter (REQ-13) | Unique constraint in the shared database: a voter who voted through `web` is refused by `web2` |
| Ballot hash chain (REQ-02) | The ballot store takes the ballot database's write lock (spent-token insert) before reading the chain head, so two replicas cannot append at the same position; a unique `(election_id, seq)` constraint backs this up |
| Signing keys (REQ-01, 02, 20) | Shared `app-instance` volume. A key is created at most once: written to a temp file and hard-linked into place, so if two replicas race, both use the first one written |
| Logins | JWTs signed with the shared `JWT_SECRET_KEY`: a token from one replica works on the other |
| WebAuthn challenges (REQ-10) | Already stored in the database |
| REQ-07 rate-limit counters | **Per replica** (in memory). With two replicas a client can make up to twice the per-endpoint limit; nginx's per-IP limits in front still apply to the total. Production uses a shared store (below) |

`web2` starts only after `web` is healthy, so the two do not create the
database tables at the same time.

## Production design (documented only)

| Layer | Design |
|---|---|
| Regions | **Active-active** in two AWS regions, Sydney (`ap-southeast-2`) and Melbourne (`ap-southeast-4`). Both serve voters all the time |
| Global routing | Route 53 latency-based records with health checks on each region's `/health`. A failed region is removed from DNS (TTL 30 s), and voters are sent to the other region |
| Edge | REQ-07 CloudFront and Shield Advanced in front of both regions; CloudFront origin failover between the two regional load balancers |
| Within a region | ALB (REQ-06 `infra/`) across 3 availability zones; target-group health checks on `/health` (interval 5 s, unhealthy threshold 2); at least 2 API tasks per zone on ECS Fargate; auto scaling with REQ-09 |
| Database | Aurora PostgreSQL Global Database: writer in one region, readable replica in the other, managed cross-region failover in about a minute with roughly 1 s of replication lag. The one-vote rule needs one authority for "has this voter voted", which a single writer gives. Aurora DSQL (multi-region active-active with strong consistency) is the alternative if both regions must take writes |
| Keys | KMS multi-region keys, so both regions sign and verify ballot tokens, checkpoints and receipts with the same key material |
| Shared counters | ElastiCache for Redis Global Datastore for REQ-07 rate limits |
| Monitoring | CloudWatch alarms on unhealthy host count, Route 53 health-check status and error rate; on-call paging; the paper fallback trigger (below) |

Failure handling:

| Failure | Automatic response | Voter impact |
|---|---|---|
| One API task | ALB health check removes it; ECS replaces it | None (requests retried) |
| One availability zone | ALB stops routing to it; tasks in the other zones carry the load | None |
| One region | Route 53 / CloudFront send all voters to the other region | Up to about a minute of failed requests while DNS and the database fail over |
| Both regions, or the platform cannot be trusted | Paper fallback, decided by people | Voting continues on paper |

## Tests and demonstration

Unit tests (`tests/test_availability.py`):

| Test | Shows |
|---|---|
| `test_health_endpoint_reports_status` | `/health` returns 200 with the replica name and `ok` for both databases |
| `test_health_reports_unhealthy_when_a_database_is_down` | 503 `unhealthy` when the ballot database cannot be reached, so the load balancer removes the replica |
| `test_two_replicas_accept_concurrent_votes` | Two app instances on the same databases take 12 votes at the same moment: all succeed, and the REQ-02 chain is unbroken (positions 1–12, verification passes) |
| `test_a_voter_cannot_vote_once_per_replica` | A vote through one replica blocks a second vote through the other |
| `test_login_on_one_replica_works_on_the_other` | A login token from `web` is accepted by `web2` |
| `test_signing_key_is_created_once` | Two replicas creating the same key end up with the first one written |

**Demo: kill one replica, votes still succeed.** Record the screen while doing this:

```bash
cd EVotingPlatform
docker compose up --build -d
docker compose exec web flask --app run seed-demo
docker compose exec web flask --app run seed-demo-voters --count 60

# open http://localhost:8404 (haproxy status: web and web2 green)
python scripts/failover_demo.py --voters 30         # terminal 1: votes every 4 s

docker compose kill web                             # terminal 2, after a few votes
#   -> status page: web turns red within about 5 s
#   -> demo: every following vote shows 201 recorded by web2
docker compose start web                            # later: web turns green again
#   -> votes are shared between web and web2 again
```

The demo ends with a summary such as `recorded: 30`, with no failures, and
the votes split between `web` and `web2`. A vote sent at the moment of the kill
takes about 2 s instead of 0.3 s, because haproxy retries it on `web2`.

Measured on the Docker stack (two runs of 16 votes, `web` killed mid-run and
restarted): 32 of 32 votes recorded, `web` removed after 5.5–5.6 s and back
after 4.7–5.0 s, and the ballot chain verified with no gaps afterwards. An
earlier configuration with `on-marked-down shutdown-sessions` lost one vote
that was waiting to be retried when the replica was marked down; that option
is deliberately not used.

## Limits of the prototype

- One machine: the replicas share one disk and one Docker host, so this shows
  failover between processes, not between regions.
- SQLite on a shared volume stands in for the regional database; there is no
  database failover in the prototype.
- `haproxy` and `nginx` are single instances here. In production both roles are
  managed, multi-zone services (ALB, CloudFront).
- Rate-limit counters are per replica (see above).
- Recording paper mark-offs on the platform after a fallback (section 5 of the
  paper procedure) is a staff task with no screen of its own in the prototype.
