# E-Voting Platform - base build

A Flask REST application for electronic voting in Australian federal elections, built from the
structure of the Python REST banking app (blueprints, SQLAlchemy models, JWT login, Bootstrap pages,
Docker). It covers the functions in the "Electronic Voting Platform" systems description and is
**intended as the base for adding the 20 security solutions later**. It contains no security
solutions of its own beyond what the banking app already had (bcrypt passwords, JWT login, a role check).

> For assignment use only. Not for real elections.

## What it does

| Function in the systems description | Where |
|---|---|
| AEC employees register citizens | `POST /api/voters/` (role `aec_officer`) |
| Citizens check enrolment and enrol themselves | `GET /api/voters/me`, `POST /api/voters/enrol` (approved by an officer) |
| Enrolled voters update their address | `PUT /api/voters/me/address` |
| Commissioner's delegates enter candidates and group order | `POST /api/candidates/` (role `commissioner_delegate`) |
| Eligibility check before voting | `GET /api/voters/eligibility`, `app/services/eligibility.py` |
| House vote: preference order | `app/services/ballot_validation.py` `validate_house` |
| Senate vote: above, below or both | `validate_senate` (below the line wins if complete) |
| Delegates exclude candidates for a court-ordered recount | `POST /api/candidates/<id>/exclude`, then re-run the tally |
| Polling station terminals, online voting, overseas missions | channels `polling_station`, `online`, `diplomatic_mission` plus `/api/terminals` |
| Compute results | `POST /api/elections/<id>/tally` (`app/services/counting.py`) |
| Share results with external systems | `GET /api/results/<id>/export` |

Roles: `voter`, `aec_officer`, `commissioner_delegate`, `election_admin`.

## Structure

```
app/
  __init__.py          app factory, blueprints, config
  models/              User, Voter, VoteRecord, Election, Division, Candidate, Ballot, Terminal, AuditLog, TallyResult
  routes/              auth, voters, elections, candidates, voting, terminals, results, admin, main (pages)
  services/            eligibility, ballot_validation, ballot_store, voting, counting, tallying, audit
  security/hooks.py    the extension points for REQ-01..REQ-20  (see docs/SECURITY_HOOKS.md)
  utils/               constants and helpers (roles_required, request metadata)
  seed.py, cli.py      demo data (flask seed-demo)
templates/             Bootstrap pages: home, login, register, dashboard, vote, staff, results
tests/                 counting and API tests (pytest)
scripts/               simulate_election.py, flood_test.py (REQ-07 rate-limit demo)
docker/nginx/          reverse proxy with per-IP rate limits (REQ-07)
infra/                 Terraform: network, firewall rules, DB users (REQ-06, see infra/README.md)
docs/SECURITY_HOOKS.md requirement-by-requirement map of where each solution goes
```

Design choices that keep the security work easy:

- Ballots and voter records are in **separate tables with no link** (`ballots` has no voter id; `vote_records` has no ballot id). All ballot access goes through `app/services/ballot_store.py`.
- Counting code is **pure functions** with no database, so it can be tested and replaced.
- Every security-relevant decision (terminal check, duplicate check, roll lookup, MFA, dual approval, receipt, hashing, audit) calls a **named hook** with a plain default.

## Run it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
flask --app run seed-demo        # demo election, staff, voters, terminals
python run.py                    # http://localhost:3000
```

Docker: `docker compose up --build` then open http://localhost:9001 (run `docker compose exec web flask --app run seed-demo` for demo data). Requests go through the nginx proxy, which rate-limits per client (REQ-07, see [docs/REQ-07_DDOS.md](docs/REQ-07_DDOS.md)).

Demo logins (password `ChangeMe123!`): `admin`, `officer`, `delegate`, `voter1` to `voter5`, and `voter_os` (overseas, must use the diplomatic mission channel with terminal `DM-0001`). Polling station terminals are `PS-0001` to `PS-0010`.

Try it: log in as `voter1`, open **Vote**, number the House and Senate papers, submit, and note the receipt code. Log in as `admin`, close the election, run the tally, and open **Results**.

## Tests and simulation

```bash
pip install -r requirements-dev.txt
pytest
python scripts/simulate_election.py --voters 200
```

## API summary

| Method and path | Role | Purpose |
|---|---|---|
| `POST /api/auth/register`, `/login`; `GET /profile` | public / any | voter account, login |
| `GET /api/voters/me`, `POST /enrol`, `PUT /me/address`, `GET /eligibility` | voter | enrolment, address, eligibility |
| `POST/GET /api/voters/`, `PUT /<id>/approve`, `/<id>/remove` | aec_officer, admin | register, list, approve |
| `GET /api/elections/`, `/divisions` | public | elections, divisions |
| `POST /api/elections/`, `PUT /<id>/status`, `POST /<id>/tally`, `GET /<id>/turnout` | admin | run an election |
| `GET /api/candidates/` | public | candidates |
| `POST/PUT /api/candidates/`, `POST /<id>/exclude` | commissioner_delegate | candidates, exclusion |
| `GET /api/voting/ballot-paper`, `POST /cast` | voter | ballot papers, cast vote |
| `GET/POST /api/terminals/`, `PUT /<id>/deactivate`, `POST /<id>/heartbeat` | admin (heartbeat open) | terminals |
| `GET /api/results/<id>`, `/detail`, `/export` | public / admin | results |
| `POST /api/admin/users`, `GET /api/admin/audit-logs` | admin | staff accounts, audit log |

Cast body: `{"election_id": 1, "channel": "online", "terminal_id": null, "house_preferences": [ids most preferred first], "senate_atl": {"A": 1, "B": 2}, "senate_btl": {"<candidate id>": 1}}`.

## Adding the security solutions

See [docs/SECURITY_HOOKS.md](docs/SECURITY_HOOKS.md) for the full requirement map, hook signatures, an example, and the known limits of this base.
