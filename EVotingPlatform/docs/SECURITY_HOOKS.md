# Where each security requirement plugs in

The base build has **no security solutions**. Every requirement from Part Two has a
defined place to attach. "Hook" means a named extension point in
`app/security/hooks.py`: you write a function with the same signature and call
`hooks.register('<name>', your_function)` (for example from `create_app`). The routes
and services never need to change.

| Req | Needs | Plugs in at | What the base does today |
|---|---|---|---|
| REQ-01 | Keep voter identity apart from ballots | `app/services/ballot_store.py` (all ballot reads/writes) and the vote step in `app/services/voting.py` | Two tables in one database. `ballots` has no voter id; `vote_records` has no ballot id. Replace `ballot_store` with a call to a separate ballot system and add the one-time anonymous token in `cast_vote`. |
| REQ-02 | Hash every ballot, verify when counting | Hooks `on_ballot_stored` (set `ballot.ballot_hash`) and `verify_ballots` (return failed `ballot_uid`s). Column: `Ballot.ballot_hash` | Hash column is empty; nothing is verified. Flagged ballots are already left out of the count and reported by the tally endpoint. |
| REQ-03 | Verify terminal software signature | Hook `verify_terminal`. Column: `Terminal.software_signature` | Any active registered terminal is accepted. |
| REQ-04 | Tamper-evident audit logs | Hook `write_audit_record` (called by `log_action` in `app/services/audit.py`) | Plain rows in `audit_logs`. |
| REQ-05 | Protect logs from change or deletion | Triggers on `audit_logs`, plus a wrapper around whatever `write_audit_record` hook is registered (so REQ-04 can land in either order), in `app/security/audit_protection.py` | **Implemented.** UPDATE/DELETE rejected by triggers; batches archived to MinIO with COMPLIANCE object lock via an upload-only user; `flask verify-audit-archive` compares the archive with the database. See `docs/REQ-05_AUDIT_PROTECTION.md` |
| REQ-06 | Firewall/security config as reviewed Infrastructure as Code | `infra/` (Terraform), `.github/CODEOWNERS`, `.github/rulesets/main.json`, `.github/workflows/iac.yml`. See `infra/README.md` | **Implemented.** Network, firewall rules and DB users as Terraform; `main` needs a PR with code-owner approval; tfsec fails the pipeline on HIGH/CRITICAL findings; a self-test proves an open database port is caught. Not applied to a cloud account; local builds still use SQLite |
| REQ-07 | DDoS protection | `docker/nginx/nginx.conf` (per-IP limits), `app/security/rate_limit.py` (`@rate_limited` on login and voting). See `docs/REQ-07_DDOS.md` | **Partial (as scoped).** nginx and API rate limits return 429; the first refusal per client and window is audited as `RATE_LIMITED`. Cloud scrubbing, CDN and WAF are documented only. In-memory counters, one process |
| REQ-08 | 99.9% availability, failover, paper fallback | Infrastructure (second region, health checks). `/health` and `POST /api/terminals/<id>/heartbeat` already exist | Single instance. |
| REQ-09 | Scale from 7,000 to 40,000 terminals | Infrastructure (autoscaling) and the database (see limitations). `scripts/simulate_election.py` is a starting point for load tests | Single process, SQLite. |
| REQ-10 | MFA for privileged users | Hook `verify_mfa`, called in `app/routes/auth.py` `login()` for staff roles | **Implemented** in `app/security/webauthn_mfa.py` (WebAuthn security keys). Enforced only when `MFA_REQUIRED=true` (off for now). See `docs/REQ-10_WEBAUTHN.md`. |
| REQ-11 | Role-based access control | `app/security/rbac.py`: one role-to-permission table (`ROLE_PERMISSIONS`) and the `permission_required(P.X)` decorator on every protected route. Tests in `tests/test_rbac.py` | **Implemented.** Deny by default, role read from the database on every request, refusals logged as `ACCESS_DENIED`. New routes must use `permission_required`, or be added to the public list in `tests/test_rbac.py` |
| REQ-12 | Real-time eligibility against the authoritative roll | Hook `lookup_electoral_roll`, called in `app/services/eligibility.py` | Reads the local `voters` table. |
| REQ-13 | One ballot per voter, across all channels | Unique constraint on `vote_records (voter_id, election_id)` in `app/models/voter.py`, and the early flush in `cast_vote` (`app/services/voting.py`). Hook `has_already_voted` stays as a fast pre-check. Tests in `tests/test_one_vote.py` | **Implemented.** `vote_records` is the one register for every channel. Marking the voter as voted is an atomic database step, so two simultaneous attempts cannot both succeed; the refused one leaves no ballot or token and is logged as `DUPLICATE_VOTE_REFUSED`. Existing local databases must be deleted and re-seeded to pick up the constraint |
| REQ-14 | Encrypt data at rest and in transit | Hooks `protect_data` / `unprotect_data` (used by `ballot_store`), database and disk encryption and TLS in infrastructure | No encryption. Plain HTTP. |
| REQ-15 | Full details of privileged actions | `@audited` in `app/security/audit_trail.py`, directly under `@permission_required` on every staff route. Routes add detail through `audit` | **Implemented.** One fixed format with all six fields, success and failure logged, and the app refuses to start if a staff route lacks `@audited`. See `docs/REQ-15_AUDIT.md` |
| REQ-16 | Tested rollback within 15 minutes | CI/CD pipeline and `infra/` (outside the app code) | Nothing. |
| REQ-17 | Candidate management for delegates only, with change logging | `app/routes/candidates.py` (`roles_required(ROLE_DELEGATE)` and `log_action`). Add a change-history table | Delegate role only; actions logged, but no per-field history. |
| REQ-18 | Dual authorisation for sensitive actions | Hook `authorise_action`, called in `exclude_candidate` | One delegate can exclude a candidate alone. |
| REQ-19 | Overseas voting from verified missions and locations only | `app/security/mission_access.py` wraps hooks `verify_terminal` and `pre_cast` (see `docs/MISSION_ACCESS.md`); admin routes in `app/routes/missions.py`; table `missions` | Simulated and tested: mission allow-list, IP range (stands in for the VPN), device certificate fingerprint, simulated geo-IP, Australia-only for online and polling station sessions. Off unless `ENFORCE_MISSION_ACCESS` is set. Real VPN, certificate authority and geo-IP service are documented only. |
| REQ-20 | Non-identifying digital receipt | Hook `issue_receipt` | **Implemented** in `app/security/receipts.py`: Ed25519-signed receipt (code, election, date, REQ-02 ballot hash), public lookup, verification key and post-close bulletin board. See `docs/REQ-20_RECEIPTS.md` |

## Hook signatures

| Hook | Arguments | Must return |
|---|---|---|
| `verify_mfa` | `user, request_data` | `True` to allow login |
| `authorise_action` | `action, actor, target, request_data` | `(allowed, message)` |
| `lookup_electoral_roll` | `voter` | dict with `enrolled`, `is_citizen`, `date_of_birth`, `division_id`, `state` |
| `verify_terminal` | `terminal, request_meta` | `(allowed, message)` |
| `has_already_voted` | `voter, election` | `True` if a ballot was already cast |
| `pre_cast` | `voter, election, channel, request_meta` | `(allowed, message)` |
| `protect_data` / `unprotect_data` | `value` | the transformed value |
| `on_ballot_stored` | `ballot` (inside the open transaction) | nothing |
| `verify_ballots` | list of `Ballot` rows | list of failed `ballot_uid`s |
| `issue_receipt` | `ballot` | dict shown to the voter |
| `write_audit_record` | `entry` dict | nothing |

`request_meta` is `{ip, user_agent, session_id}`.

## Example: adding a solution

```python
# e.g. in app/security/ballot_hashing.py (REQ-02)
import hashlib, json
from app.security.hooks import hooks

def hash_ballot(ballot):
    body = json.dumps([ballot.ballot_uid, ballot.election_id, ballot.house_order,
                       ballot.senate_order], sort_keys=True)
    ballot.ballot_hash = hashlib.sha256(body.encode()).hexdigest()

hooks.register('on_ballot_stored', hash_ballot)
```

Call `hooks.reset('on_ballot_stored')` to put the base behaviour back (the tests use this).

## Known limits of the base build (deliberate or not yet addressed)

- **SQLite** is a single file and cannot scale to 40,000 terminals. Move to a server database for REQ-08/09. Counting also loads all ballots into memory.
- **No HTTPS, development secrets** in `.env` and `docker-compose.yml`. (Rate limiting: REQ-07.)
- **JWT is stored in browser localStorage** (same as the banking app) and logout does not revoke the token.
- **Terminals are just registered IDs.** Nothing proves a request came from the terminal it names (REQ-03, REQ-19).
- **Audit entries and `vote_records` name the voter and the time.** Ballots store only the date, so the two cannot be matched by timestamp, but REQ-01 should still remove any remaining ways to link them.
- **Counting simplifications:** Senate surplus transfers use fractional weights but elect one candidate at a time; ties are broken by lowest candidate id, not by the draw the real process uses. Check the formal-vote numbers in `app/utils/constants.py` (`SENATE_ATL_MIN`, `SENATE_BTL_MIN`) against current AEC rules.
