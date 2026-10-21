from types import SimpleNamespace

from app import db
from app.models import Ballot, BallotCheckpoint
from app.security import ballot_chain
from app.security.chain import GENESIS
from app.security.hooks import hooks
from app.security.tokens import issue_token
from app.services import ballot_store


def _cast(election_id=1):
    ballot = ballot_store.save_ballot(
        token=issue_token(), election=SimpleNamespace(id=election_id),
        division_id=1, state='NSW', house_order=[1, 2],
        senate_atl=None, senate_btl=None, senate_order=[3, 4], senate_source='atl')
    hooks.run('on_ballot_stored', ballot)
    db.session.commit()
    return ballot


def test_ballots_are_chained(app):
    for _ in range(3):
        _cast()
    ballots = Ballot.query.order_by(Ballot.seq).all()
    assert [b.seq for b in ballots] == [1, 2, 3]
    assert ballots[0].prev_hash == GENESIS
    assert ballots[1].prev_hash == ballots[0].ballot_hash
    assert ballots[2].prev_hash == ballots[1].ballot_hash
    assert len({b.ballot_hash for b in ballots}) == 3


def test_each_election_has_its_own_chain(app):
    _cast(election_id=1)
    _cast(election_id=2)
    assert {b.seq for b in Ballot.query.all()} == {1}


def test_checkpoint_is_saved_every_n_ballots(app, monkeypatch):
    monkeypatch.setattr(ballot_chain, 'CHECKPOINT_EVERY', 2)
    for _ in range(4):
        _cast()
    assert [c.seq for c in BallotCheckpoint.query.order_by(BallotCheckpoint.seq)] == [2, 4]



def _verify():
    return hooks.run('verify_ballots', Ballot.query.all())


def test_untouched_election_verifies(app):
    for _ in range(3):
        _cast()
    assert _verify() == []


def test_edited_ballot_is_flagged(app):
    for _ in range(3):
        _cast()
    second = Ballot.query.filter_by(seq=2).first()
    uid = second.ballot_uid
    second.house_order = [2, 1]
    db.session.commit()
    assert uid in _verify()


def test_deleted_ballot_is_flagged(app):
    for _ in range(3):
        _cast()
    db.session.delete(Ballot.query.filter_by(seq=2).first())
    db.session.commit()
    third = Ballot.query.filter_by(seq=3).first()
    assert third.ballot_uid in _verify()


def test_deleted_last_ballot_is_caught_by_checkpoint(app, monkeypatch):
    monkeypatch.setattr(ballot_chain, 'CHECKPOINT_EVERY', 2)
    for _ in range(2):
        _cast()
    db.session.delete(Ballot.query.filter_by(seq=2).first())
    db.session.commit()
    assert 'MISSING:2' in _verify()


def test_forged_checkpoint_is_flagged(app, monkeypatch):
    monkeypatch.setattr(ballot_chain, 'CHECKPOINT_EVERY', 2)
    for _ in range(2):
        _cast()
    checkpoint = BallotCheckpoint.query.first()
    checkpoint.signature = '00' * 64
    db.session.commit()
    assert 'CHECKPOINT:2' in _verify()
