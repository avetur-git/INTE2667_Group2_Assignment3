# REQ-02 assumptions: hash every ballot and verify during tallying

Solution implemented: Option C (hash chain with signed checkpoints).

## What was built
- Each ballot stores seq, prev_hash and ballot_hash. The hash covers the ballot content and the previous ballot's hash, so changing or deleting a ballot breaks the chain after it.
- A checkpoint (election, seq, head hash, Ed25519 signature) is saved every 10 ballots in the ballot_checkpoints table.
- At tally time the verify_ballots hook recalculates every hash and checks the checkpoints. Any ballot that fails is flagged, and the base build leaves it out of the count.
- Tests: tests/test_ballot_chain.py (chain logic) and tests/test_ballot_chain_hooks.py (hooks with the real database).

## Assumptions
1. The hash covers the ballot id, election, division, state, House and Senate preferences, Senate source and receipt code. It does not include any timestamp, to keep REQ-01 intact.
2. There is one chain per election, with seq starting at 1.
3. The checkpoint signing key (instance/ledger_key.pem) simulates an HSM or KMS. It is created on first use, is git-ignored and must never be committed.
4. Hashes are calculated over the values as stored, so protecting the data later (REQ-14) does not break verification.
5. Detected: an edited ballot, a deleted ballot in the middle (the next ballot is flagged), a missing ballot at or before a checkpoint, and a forged or altered checkpoint.
6. Not detected in the prototype: ballots deleted from the end of the chain after the latest checkpoint (up to 9 ballots), and a roll-back of the whole database to an older state that still has valid checkpoints. Production would checkpoint when voting closes and store checkpoints outside the database.
7. Anyone who holds the signing key could rewrite the chain and its checkpoints. In production the key is held in an HSM, so it cannot be copied.
8. Sequence numbers reveal the order in which ballots were stored. This is the same limit as REQ-01 assumption 9. Production would shuffle ballots in batches before chaining.
9. The unique constraint on (election_id, seq) means two simultaneous votes may collide, and one of them fails and must be retried. This is accepted for the prototype.
10. A hash only detects change, it does not hide the vote. Confidentiality comes from REQ-01 and REQ-14.
11. The tally reports the ballot ids that fail. Investigating them is a manual process outside the prototype.