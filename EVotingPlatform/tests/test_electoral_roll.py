"""REQ-12 - real-time eligibility check against the authoritative electoral roll.

These prove:
  1. The roll is asked live: a voter removed after logging in cannot vote.
  2. The roll reveals only what an eligibility decision needs.
  3. Fail closed: if the roll is down, or its answer is forged, replayed or
     out of date, the voter is refused - never waved through - and it is logged.
  4. A local voter record that disagrees with the roll is refused.
"""
import json

import pytest

from app import db
from app.models import AuditLog, Ballot, Voter
from app.security import electoral_roll
from app.security.electoral_roll import _roll_key, lookup_electoral_roll, roll_service
from app.seed import seed_demo
from tests.test_api_flow import login

ELIGIBILITY_FIELDS = {'enrolled', 'is_citizen', 'date_of_birth', 'division_id', 'state'}


@pytest.fixture()
def eid(client, app):
    return seed_demo()['election_id']


def voter_record(username):
    return Voter.query.join(Voter.user).filter_by(username=username).one()


def filled_ballot(client, headers, eid):
    paper = client.get(f'/api/voting/ballot-paper?election_id={eid}', headers=headers).get_json()
    groups = [g['group_code'] for g in paper['senate_groups']]
    return {'election_id': eid, 'channel': 'online',
            'house_preferences': [c['id'] for c in paper['house_candidates']],
            'senate_atl': {g: i + 1 for i, g in enumerate(groups[:6])}}


def cast(client, headers, ballot):
    return client.post('/api/voting/cast', headers=headers, json=ballot)


def signed_by_roll(answer):
    """An answer genuinely signed by the roll (to test checks other than the signature)."""
    answer_bytes = json.dumps(answer, sort_keys=True).encode()
    return answer_bytes, _roll_key().sign(answer_bytes).hex()


def test_voter_removed_after_login_cannot_vote(client, eid):
    """Real time: the roll is asked again at the moment of casting."""
    voter = login(client, 'voter1')
    ballot = filled_ballot(client, voter, eid)          # eligible when the paper is shown

    officer = login(client, 'officer')
    voter_id = voter_record('voter1').id
    assert client.put(f'/api/voters/{voter_id}/remove', headers=officer).status_code == 200

    r = cast(client, voter, ballot)
    assert r.status_code == 403 and r.get_json()['reason_code'] == 'not_enrolled'
    assert Ballot.query.count() == 0


def test_roll_reveals_only_eligibility_fields(app, eid):
    sent = []
    real_transport = electoral_roll.transport

    def watching_transport(request_bytes):
        answer_bytes, signature = real_transport(request_bytes)
        sent.append((json.loads(request_bytes), json.loads(answer_bytes)))
        return answer_bytes, signature

    electoral_roll.transport = watching_transport
    try:
        result = lookup_electoral_roll(voter_record('voter1'))
    finally:
        electoral_roll.transport = real_transport

    request, answer = sent[0]
    assert set(request) == {'roll_ref', 'request_id'}        # no name, no birth date
    assert set(answer) == ELIGIBILITY_FIELDS | {'request_id', 'issued_at', 'found'}
    assert set(result) == ELIGIBILITY_FIELDS and result['enrolled'] is True


def test_roll_unreachable_fails_closed(client, eid, monkeypatch):
    voter = login(client, 'voter1')
    ballot = filled_ballot(client, voter, eid)

    def roll_down(request_bytes):
        raise ConnectionError('roll service timed out')
    monkeypatch.setattr(electoral_roll, 'transport', roll_down)

    r = cast(client, voter, ballot)
    assert r.status_code == 403 and r.get_json()['reason_code'] == 'roll_unavailable'
    assert Ballot.query.count() == 0
    entry = AuditLog.query.filter_by(action='ROLL_LOOKUP_FAILED').one()
    assert entry.success is False and 'could not be reached' in entry.details['reason']


def test_forged_answer_is_rejected(client, eid, monkeypatch):
    """Someone between the platform and the roll flips 'enrolled' to true for a
    removed voter. The roll's signature no longer matches, so it is refused."""
    voter = login(client, 'voter1')
    ballot = filled_ballot(client, voter, eid)
    voter_record('voter1').enrolment_status = 'removed'
    db.session.commit()

    def forging_transport(request_bytes):
        answer_bytes, signature = roll_service(request_bytes)
        answer = json.loads(answer_bytes)
        answer['enrolled'] = True
        return json.dumps(answer, sort_keys=True).encode(), signature   # old signature
    monkeypatch.setattr(electoral_roll, 'transport', forging_transport)

    r = cast(client, voter, ballot)
    assert r.get_json()['reason_code'] == 'roll_unavailable'
    assert Ballot.query.count() == 0


def test_replayed_answer_is_rejected(client, eid, monkeypatch):
    """A genuine 'enrolled' answer is recorded, the voter is then removed, and
    the old answer is replayed. It belongs to a different request, so it fails."""
    voter = login(client, 'voter1')
    ballot = filled_ballot(client, voter, eid)
    recorded = roll_service(json.dumps({'roll_ref': voter_record('voter1').id,
                                        'request_id': 'an-earlier-request'}).encode())
    voter_record('voter1').enrolment_status = 'removed'
    db.session.commit()

    monkeypatch.setattr(electoral_roll, 'transport', lambda request_bytes: recorded)
    r = cast(client, voter, ballot)
    assert r.get_json()['reason_code'] == 'roll_unavailable'
    assert Ballot.query.count() == 0


def test_out_of_date_answer_is_rejected(client, eid, monkeypatch):
    """Correctly signed and for this request, but issued two minutes ago."""
    voter = login(client, 'voter1')
    ballot = filled_ballot(client, voter, eid)

    def stale_transport(request_bytes):
        answer = json.loads(roll_service(request_bytes)[0])
        answer['issued_at'] -= 120
        return signed_by_roll(answer)
    monkeypatch.setattr(electoral_roll, 'transport', stale_transport)

    r = cast(client, voter, ballot)
    assert r.get_json()['reason_code'] == 'roll_unavailable'


def test_local_record_that_disagrees_with_roll_is_refused(client, eid, monkeypatch):
    """The roll says the voter is in a different division (they moved, or the
    local record was tampered with). Their ballot paper would be wrong."""
    voter = login(client, 'voter1')
    ballot = filled_ballot(client, voter, eid)

    def moved_transport(request_bytes):
        answer = json.loads(roll_service(request_bytes)[0])
        answer['division_id'] += 1
        return signed_by_roll(answer)
    monkeypatch.setattr(electoral_roll, 'transport', moved_transport)

    r = cast(client, voter, ballot)
    assert r.status_code == 403 and r.get_json()['reason_code'] == 'roll_mismatch'
    assert Ballot.query.count() == 0


def test_eligible_voter_still_votes_normally(client, eid):
    voter = login(client, 'voter1')
    assert cast(client, voter, filled_ballot(client, voter, eid)).status_code == 201
