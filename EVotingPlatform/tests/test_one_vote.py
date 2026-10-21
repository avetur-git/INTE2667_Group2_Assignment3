"""REQ-13 - one electronic ballot per voter per election, across all channels.

These prove:
  1. A second vote is refused whichever channel it comes through.
  2. Even if two requests both pass the "already voted?" check at the same
     moment (a race), the database lets only one through - and the refused
     one leaves no ballot, token or vote record behind.
  3. The rule is enforced by the database itself, not only by app code.
  4. Refused attempts are logged; other voters are not affected.
"""
import pytest
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import AuditLog, Ballot, SpentToken, Voter, VoteRecord
from app.security.hooks import hooks
from app.seed import seed_demo
from tests.test_api_flow import login, full_vote


@pytest.fixture()
def eid(client, app):
    return seed_demo()['election_id']


def filled_ballot(client, headers, eid):
    """A complete, formal ballot for this voter. Built BEFORE they vote,
    because the ballot paper is (correctly) refused once they have voted."""
    paper = client.get(f'/api/voting/ballot-paper?election_id={eid}', headers=headers).get_json()
    groups = [g['group_code'] for g in paper['senate_groups']]
    return {'election_id': eid,
            'house_preferences': [c['id'] for c in paper['house_candidates']],
            'senate_atl': {g: i + 1 for i, g in enumerate(groups[:6])}}


def cast(client, headers, ballot, channel, terminal_id=None):
    return client.post('/api/voting/cast', headers=headers,
                       json={**ballot, 'channel': channel, 'terminal_id': terminal_id})


def counts():
    return Ballot.query.count(), VoteRecord.query.count(), SpentToken.query.count()


def test_second_vote_through_another_channel_is_refused(client, eid):
    """Vote online, then try again at a polling station."""
    voter = login(client, 'voter1')
    ballot = filled_ballot(client, voter, eid)
    assert cast(client, voter, ballot, 'online').status_code == 201

    r = cast(client, voter, ballot, 'polling_station', 'PS-0001')
    assert r.status_code == 409
    assert r.get_json()['reason_code'] == 'already_voted'
    assert counts() == (1, 1, 1)


def test_race_where_both_requests_pass_the_check(client, eid):
    """Simulates two requests arriving together: the second one's
    'already voted?' check runs before the first has saved anything, so the
    check says no. The database constraint must still refuse it."""
    voter = login(client, 'voter1')
    ballot = filled_ballot(client, voter, eid)
    assert cast(client, voter, ballot, 'online').status_code == 201

    hooks.register('has_already_voted', lambda voter, election: False)
    try:
        r = cast(client, voter, ballot, 'polling_station', 'PS-0001')
    finally:
        hooks.reset('has_already_voted')

    assert r.status_code == 409
    assert r.get_json()['reason_code'] == 'already_voted'
    # Nothing from the refused attempt was kept: no ballot, no token, no record.
    assert counts() == (1, 1, 1)


def test_database_refuses_a_second_record_directly(app, eid):
    """Defence in depth: code that skips the voting service entirely still
    cannot mark the same voter twice."""
    voter = Voter.query.join(Voter.user).filter_by(username='voter2').one()
    db.session.add(VoteRecord(voter_id=voter.id, election_id=eid, channel='online'))
    db.session.commit()

    db.session.add(VoteRecord(voter_id=voter.id, election_id=eid, channel='polling_station'))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()
    assert VoteRecord.query.filter_by(voter_id=voter.id).count() == 1


def test_refused_attempts_are_logged(client, eid):
    voter = login(client, 'voter1')
    ballot = filled_ballot(client, voter, eid)
    cast(client, voter, ballot, 'online')
    cast(client, voter, ballot, 'polling_station', 'PS-0002')

    entry = AuditLog.query.filter_by(action='DUPLICATE_VOTE_REFUSED').one()
    assert entry.username == 'voter1' and entry.success is False
    assert entry.details == {'channel': 'polling_station'}


def test_other_voters_are_not_affected(client, eid):
    voter1 = login(client, 'voter1')
    ballot = filled_ballot(client, voter1, eid)
    cast(client, voter1, ballot, 'online')
    assert cast(client, voter1, ballot, 'online').status_code == 409

    assert full_vote(client, login(client, 'voter2'), eid, 'online').status_code == 201
    assert counts() == (2, 2, 2)
