from sqlalchemy import inspect
from app import db
from app.models import Ballot, VoteRecord


def test_ballot_model_uses_ballots_bind():
    assert Ballot.__bind_key__ == 'ballots'
    assert Ballot.__table__.metadata is db.metadatas['ballots']
    assert VoteRecord.__table__.metadata is db.metadata


def test_ballot_table_lives_only_in_ballot_database(app):
    assert inspect(db.engines['ballots']).has_table('ballots')
    assert not inspect(db.engine).has_table('ballots')


def test_ballot_has_no_foreign_keys_to_voter_side():
    assert not Ballot.__table__.foreign_keys


def test_ballot_has_no_identifying_columns():
    columns = {c.name for c in Ballot.__table__.columns}
    forbidden = {'voter_id', 'user_id', 'username', 'session_id',
                 'channel', 'terminal_id', 'created_at', 'voted_at'}
    assert not columns & forbidden
