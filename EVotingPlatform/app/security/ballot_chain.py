"""REQ-02: plug the ballot hash chain into the base build's hooks."""
from app import db
from app.models.ballot import Ballot, BallotCheckpoint
from app.security.chain import (GENESIS, ballot_payload, checkpoint_message,
                                entry_hash, find_problems)
from app.security.hooks import hooks
from app.security.keys import ledger_private_key, ledger_public_key

CHECKPOINT_EVERY = 10


def hash_ballot(ballot):
    """Hook on_ballot_stored: runs inside the open transaction, before commit."""
    last = (Ballot.query
            .filter(Ballot.election_id == ballot.election_id,
                    Ballot.id != ballot.id, Ballot.seq.isnot(None))
            .order_by(Ballot.seq.desc()).first())
    ballot.seq = last.seq + 1 if last else 1
    ballot.prev_hash = last.ballot_hash if last else GENESIS
    ballot.ballot_hash = entry_hash(ballot.prev_hash, ballot_payload(ballot))
    if ballot.seq % CHECKPOINT_EVERY == 0:
        signature = ledger_private_key().sign(
            checkpoint_message(ballot.election_id, ballot.seq, ballot.ballot_hash)).hex()
        db.session.add(BallotCheckpoint(
            election_id=ballot.election_id, seq=ballot.seq,
            head_hash=ballot.ballot_hash, signature=signature))


def verify_ballot_chain(ballots):
    """Hook verify_ballots: return the ids that fail verification (empty list = all good)."""
    by_election = {}
    for b in ballots:
        by_election.setdefault(b.election_id, []).append(b)
    failed = []
    for election_id, rows in by_election.items():
        rows.sort(key=lambda b: (b.seq is None, b.seq or 0))
        chain = [(b.seq if b.seq is not None else -1, b.ballot_uid, ballot_payload(b),
                  b.prev_hash or '', b.ballot_hash or '') for b in rows]
        checkpoints = [(c.seq, c.head_hash, c.signature) for c in
                       BallotCheckpoint.query.filter_by(election_id=election_id)
                       .order_by(BallotCheckpoint.seq)]
        for uid, _reason in find_problems(chain, checkpoints, ledger_public_key(), election_id):
            if uid not in failed:
                failed.append(uid)
    return failed


def register_ballot_chain():
    hooks.register('on_ballot_stored', hash_ballot)
    hooks.register('verify_ballots', verify_ballot_chain)
