# REQ-07: DDoS detection and automatic throttling

Report section 2.7, Option B: cloud DDoS protection with traffic scrubbing, a
CDN, a web application firewall and automatic rate limiting.

**Prototype scope (partial).** Rate limiting is built and tested at the reverse
proxy (nginx) and in the API. The cloud layer (scrubbing, CDN, WAF) is the
production design below; it is documented, not deployed.

## Layers

```
voters ──> [1] Cloud edge: Shield Advanced scrubbing, CloudFront CDN, AWS WAF      production design
       ──> [2] Load balancer (REQ-06 infra/), HTTPS only                           Terraform, not applied
       ──> [3] nginx reverse proxy: per-IP limit_req / limit_conn, timeouts        built (docker/nginx/)
       ──> [4] Flask API: per-endpoint limits, 429, RATE_LIMITED audit entries     built (app/security/rate_limit.py)
```

Each layer stops a different kind of attack, and each is cheaper than the one
behind it. Volumetric floods (hundreds of Gbit/s, SYN/UDP floods) can only be
absorbed at the edge [1]. Layers [3] and [4] deal with HTTP floods and abuse
that reach our own servers: password guessing on login, scripted ballot-paper
requests, slow-request (slowloris) attacks.

## What is built

### Reverse proxy: `docker/nginx/nginx.conf`

| Zone | Applies to | Limit per client IP | Burst |
|---|---|---|---|
| `per_ip` | every request | 20 requests/second | 40 |
| `login` | `POST /api/auth/login` | 60/minute | 20 |
| `voting` | `/api/voting/*` | 120/minute | 30 |
| `conn_per_ip` | open connections | 20 at a time | |

Also: header/body/send timeouts of 10 s and a 64 KB body limit against slow or
oversized requests. Refused requests get `429` with a JSON body, and nginx logs
each one at `warn` (`limiting requests ... by zone "login"`).

Polling stations put many voters behind one public address. Their registered
NAT ranges go in the `geo $polling_network` block so the voting zone does not
throttle a busy station; those voters are still limited per voter by the API.

In Docker the app publishes no port of its own. All traffic goes through nginx
on `http://localhost:9001`, so nothing can bypass the proxy limits.

### API: `app/security/rate_limit.py`

| Rule | Routes | Counted per | Default |
|---|---|---|---|
| `login` | `POST /api/auth/login` | client IP | 20 per 60 s |
| `vote` | `GET /api/voting/ballot-paper`, `POST /api/voting/cast` | logged-in voter (IP if not logged in) | 30 per 60 s |

- Over the limit: **HTTP 429** with a `Retry-After` header (seconds until the
  client is let back in) and `{"error": ..., "retry_after": n}`. The route code
  does not run.
- A sliding window: at most N requests in any 60 seconds, not "N per clock
  minute", so a client cannot send 2N across a minute boundary.
- The nginx limits are deliberately looser than these, so the API's audited
  limit is the one a single client normally meets; nginx is the backstop for
  floods.
- Config: `RATE_LIMIT_LOGIN`, `RATE_LIMIT_VOTE` (`requests/seconds`),
  `RATE_LIMIT_ENABLED`. A malformed value stops the app at startup.

**Client address behind the proxy.** Behind nginx every request arrives from
the proxy, so all clients would share one limit. With `TRUSTED_PROXY_COUNT=1`
(set in `docker-compose.yml`) the app takes the client address from the
`X-Forwarded-For` header nginx writes. nginx overwrites that header rather than
appending to it, and the app is not reachable except through nginx, so a client
cannot forge its address. Without the setting (`run.py` directly) the header is
ignored. The same corrected address is used by the audit log and REQ-19.

### Throttling events in the audit log

The first refusal for a client in each window is written through `log_action`
(so it gets REQ-04/05 protection like every other entry):

| Field | Value |
|---|---|
| `action` | `RATE_LIMITED` |
| `success` | `false` |
| `ip_address` | client address |
| `user_id`, `username` | the voter, for the `vote` rule |
| `object_type`, `object_id` | `endpoint`, e.g. `POST /api/auth/login` |
| `details` | `{"rule", "limit", "window_seconds", "counted_by"}` |

Later refusals for the same client in the same window are not written, so an
attacker cannot use the throttle to flood the audit table. nginx refusals are
in the nginx error log.

## Production design (documented only)

The REQ-06 Terraform (`infra/`) puts an internet-facing ALB in front of the app.
For the election this is extended as follows. Each change is a reviewed pull
request against `infra/` like any other.

| Component | Purpose | Configuration |
|---|---|---|
| **AWS Shield Advanced** on the CloudFront distribution, ALB and Route 53 zone | Always-on detection and automatic scrubbing of network and transport layer floods (SYN, UDP, reflection); 24/7 Shield Response Team; cost protection for scaling during an attack | Protection resources and a protection group per election; proactive engagement on, with the operations contact list |
| **Amazon CloudFront** (CDN) | Absorbs floods at hundreds of edge locations, serves static files (`static/`, page templates) from cache so they never reach the app, HTTPS only, TLS 1.2+ | Origin = the ALB. API paths not cached |
| **AWS WAF** web ACL on CloudFront | Layer 7 filtering and automatic rate limiting before traffic reaches AWS region | Rate-based rules per IP over 5 minutes: 2,000 for all paths, 100 for `/api/auth/login`, 300 for `/api/voting/*`, action Block; AWS managed rules `CommonRuleSet`, `KnownBadInputsRuleSet`, `AmazonIpReputationList`; Shield Advanced automatic application-layer mitigation on |
| **Origin lock** | Stops attackers going around CloudFront to the ALB directly | ALB security group (`infra/security_groups.tf`) changed from `0.0.0.0/0` to the AWS managed prefix list `com.amazonaws.global.cloudfront.origin-facing`, plus a secret origin header checked by the ALB |
| **Detection and alerting** | Tell people an attack is happening | CloudWatch alarms on Shield `DDoSDetected`, WAF `BlockedRequests` and ALB 5xx/latency; `RATE_LIMITED` audit entries and nginx `limiting requests` lines forwarded to the SIEM; alerts to the on-call operations team |
| **Shared rate-limit store** | Keep one limit per client across several app instances (REQ-09 scaling) | Amazon ElastiCache (Redis). Built locally by REQ-09: `RATE_LIMIT_STORAGE_URL` and the `ratelimit` Redis service |

Not geo-blocked: overseas voters at diplomatic missions (REQ-19) must still
reach the platform, so country blocking is not used at the edge.

Alternative with the same layers: Cloudflare (DDoS protection, CDN, WAF and
rate limiting rules) in front of the ALB.

## Tests and demonstration

Unit tests (`tests/test_rate_limit.py`):

| Test | Shows |
|---|---|
| `test_requests_over_limit_return_429` | 4th login in the window gets 429 with `Retry-After`; the login code is not run |
| `test_requests_under_limit_succeed` | a client under the limit logs in, and other clients are unaffected while one is throttled |
| `test_throttle_event_logged` | a `RATE_LIMITED` audit entry with the client IP, endpoint and rule |
| `test_flood_is_logged_once_per_window` | 50 refusals produce 1 audit entry |
| `test_voting_is_counted_per_voter_not_per_ip` | two voters at one polling station IP are limited separately |
| `test_forwarded_header_ignored_without_trusted_proxy` | a client cannot dodge the limit with a fake `X-Forwarded-For` |
| `test_trusted_proxy_client_address_is_used` | behind nginx each real client gets its own limit |

```bash
python -m pytest tests/test_rate_limit.py -v
```

Against the running Docker stack (`docker compose up --build`):

```bash
python scripts/flood_test.py                                   # 40 logins: 401s, then 429 (app), then 429 (nginx)
python scripts/flood_test.py --path /health --requests 300 --workers 50   # per-IP flood: 429 (nginx)
docker compose logs proxy | grep "limiting requests"           # nginx throttling log
```

Then log in as `admin` and open `GET /api/admin/audit-logs` to see the
`RATE_LIMITED` entry.

## Limits of the prototype

- No cloud resources are deployed; scrubbing, CDN and WAF exist only as the
  design above.
- API counters are shared between replicas in Redis since REQ-09 (memory only,
  so they reset when Redis restarts). Without Redis each replica counts alone.
- nginx throttling is logged in nginx's own log, not the audit table.
- The polling-station address ranges are placeholders until the real ranges
  are known.
