"""All reading and writing of ballots goes through this module.

Base: ballots live in the same database as everything else.
REQ-01 moves this behind a separate ballot system, and REQ-14 encrypts the
stored values, by changing ONLY this file (and the protect_data hooks)."""
from app import db
from app.models.ballot import Ballot
from app.security.hooks import hooks
from app.utils.helpers import generate_uuid, generate_receipt_code

def save_ballot(*, election, division_id, state,
                house_order, senate_atl, senate_btl, senate_order, senate_source):
    ballot = Ballot(
        ballot_uid=generate_uuid(),
        election_id=election.id,
        division_id=division_id,
        state=state,
        house_order=hooks.run('protect_data', house_order),
        senate_atl=hooks.run('protect_data', senate_atl),
        senate_btl=hooks.run('protect_data', senate_btl),
        senate_order=hooks.run('protect_data', senate_order),
        senate_source=senate_source,
        receipt_code=generate_receipt_code(),
    )
    db.session.add(ballot)
    db.session.flush()   # get ballot.id without committing
    return ballot


def _open(ballot):
    """Return a plain dict with decoded preference lists."""
    return {
        'ballot_uid': ballot.ballot_uid,
        'division_id': ballot.division_id,
        'state': ballot.state,
        'house_order': hooks.run('unprotect_data', ballot.house_order),
        'senate_order': hooks.run('unprotect_data', ballot.senate_order),
        'ballot_hash': ballot.ballot_hash,
    }


def all_ballots(election_id):
    return [ _open(b) for b in Ballot.query.filter_by(election_id=election_id).all() ]


def raw_ballots(election_id):
    """The Ballot rows themselves (used by hash verification, REQ-02)."""
    return Ballot.query.filter_by(election_id=election_id).all()


def count_ballots(election_id):
    return Ballot.query.filter_by(election_id=election_id).count()
