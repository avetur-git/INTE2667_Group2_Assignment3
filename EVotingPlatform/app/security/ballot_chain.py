"""REQ-02: plug the ballot hash chain into the base build's hooks."""
from app import db
from app.models.ballot import Ballot, BallotCheckpoint
from app.security.chain import GENESIS, ballot_payload, checkpoint_message, entry_hash
from app.security.hooks import hooks
from app.security.keys import ledger_private_key

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


def register_ballot_chain():
    hooks.register('on_ballot_stored', hash_ballot)
