# REQ-01 assumptions: voter identity separate from ballot selections

Solution implemented: Option B (fully separate systems linked only by a one-time anonymous voting token).

## What was built
- Ballots are stored in their own database file (instance/ballots.db), separate from the voter database (instance/evoting.db). The ballots table has no foreign keys to the voter side.
- A ballot holds no voter id, user id, session id, channel or terminal id, and only the date (never the time).
- The voter side issues a random, single-use token (Ed25519 signed). The ballot side accepts a ballot only with a valid token and records each used token in the spent_tokens table, where the primary key makes a second use fail.
- Tests: tests/test_separation.py and tests/test_ballot_token.py.

## Assumptions
1. Threat model: someone with access to only one side (the voter database, ballots.db, or one database administrator) cannot link a voter to a ballot. Collusion between the operators of both sides is out of scope.
2. Separation is by database file and code boundary, because SQLite has no database users. Production would use separate hosts, network zones, database accounts and encryption keys.
3. The signing key (instance/token_key.pem) simulates an HSM or KMS. It is created on first use, is git-ignored and must never be committed.
4. The voter side and the ballot side run in one Flask process in the prototype. Production would put them in separate services, and the ballot system would hold only the public key.
5. The token is signed but not blind, so in theory the voter side could remember which token went to whom. The prototype does not store tokens. Production would use blind signatures.
6. Tokens have no expiry, because a token is issued and used in the same request.
7. The voter side (vote_records) still records that a voter voted, with channel, terminal and time. This is needed for eligibility and duplicate checks (REQ-12, REQ-13) and is never linked to a ballot.
8. Division and state stay on the ballot because counting needs them.
9. Ballot ids increase in the same order as vote_records ids. Production would shuffle ballots in batches before storing or counting them.
10. A commit across two SQLite files is not atomic. This is accepted for the prototype. Production would use a two-phase approach or a reliable queue.
11. The audit log records that a vote was cast (user, election, channel, terminal) but never the token, the receipt code or the ballot content.