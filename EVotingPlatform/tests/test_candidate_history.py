import pytest

from app import db
from app.models import Candidate, CandidateHistory, AuditLog, User
from app.seed import seed_demo
from app.utils import constants as C
from tests.test_api_flow import login, exclude_with_approval

REFUSED_USERS = ['voter1', 'officer', 'admin']


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def new_house_candidate_payload(election_id):
    return {'election_id': election_id, 'contest_type': 'house', 'division_id': 1,
            'name': 'Test Candidate', 'party': 'Test Party', 'ballot_position': 9}


def first_house_candidate():
    return Candidate.query.filter_by(contest_type='house', division_id=1).first()


def open_and_close_election(client, election_id):
    admin = login(client, 'admin')
    assert client.put(f'/api/elections/{election_id}/status', json={'status': 'open'},
                      headers=admin).status_code == 200
    assert client.put(f'/api/elections/{election_id}/status', json={'status': 'closed'},
                      headers=admin).status_code == 200


def attempt_candidate_changes(client, headers, election_id):
    """Try to add, edit and exclude a candidate with the given headers.
    Add and edit need a draft election; exclusion needs a closed one, so the
    election is closed (by the admin) between the two stages."""
    cand_id = first_house_candidate().id
    codes = {
        'add': client.post('/api/candidates/', json=new_house_candidate_payload(election_id),
                           headers=headers).status_code,
        'edit': client.put(f'/api/candidates/{cand_id}', json={'party': 'Changed Party'},
                           headers=headers).status_code,
    }
    open_and_close_election(client, election_id)
    codes['exclude'] = client.post(f'/api/candidates/{cand_id}/exclude', json={'reason': 'Court order 123'},
                                   headers=headers).status_code
    return codes


def history_for(candidate_id):
    db.session.expire_all()
    return (CandidateHistory.query.filter_by(candidate_id=candidate_id)
            .order_by(CandidateHistory.version).all())


# ---------------------------------------------------------------------------
# Only a delegate can manage candidates
# ---------------------------------------------------------------------------
def test_only_delegate_can_add_candidate(client, app):
    eid = seed_demo(election_status=C.ELECTION_DRAFT)['election_id']
    before = Candidate.query.count()
    r = client.post('/api/candidates/', json=new_house_candidate_payload(eid),
                    headers=login(client, 'delegate'))
    assert r.status_code == 201
    assert r.get_json()['candidate']['name'] == 'Test Candidate'
    assert Candidate.query.count() == before + 1


def test_only_delegate_can_edit_candidate(client, app):
    seed_demo(election_status=C.ELECTION_DRAFT)
    cand_id = first_house_candidate().id
    r = client.put(f'/api/candidates/{cand_id}', json={'party': 'Changed Party'},
                   headers=login(client, 'delegate'))
    assert r.status_code == 200
    assert r.get_json()['candidate']['party'] == 'Changed Party'


def test_only_delegate_can_exclude_candidate(client, app):
    eid = seed_demo(election_status=C.ELECTION_DRAFT)['election_id']
    cand_id = first_house_candidate().id
    open_and_close_election(client, eid)
    assert exclude_with_approval(client, cand_id, 'Court order 123').status_code == 200
    db.session.expire_all()
    assert db.session.get(Candidate, cand_id).is_excluded is True


@pytest.mark.parametrize('username', REFUSED_USERS)
def test_other_roles_are_refused_and_nothing_changes(client, app, username):
    eid = seed_demo(election_status=C.ELECTION_DRAFT)['election_id']
    cand = first_house_candidate()
    cand_id, party_before = cand.id, cand.party
    count_before = Candidate.query.count()

    codes = attempt_candidate_changes(client, login(client, username), eid)

    assert codes == {'add': 403, 'edit': 403, 'exclude': 403}
    db.session.expire_all()
    assert Candidate.query.count() == count_before
    cand = db.session.get(Candidate, cand_id)
    assert cand.party == party_before
    assert cand.is_excluded is False


def test_unauthenticated_user_cannot_manage_candidates(client, app):
    eid = seed_demo(election_status=C.ELECTION_DRAFT)['election_id']
    cand = first_house_candidate()
    cand_id, party_before = cand.id, cand.party
    count_before = Candidate.query.count()

    codes = attempt_candidate_changes(client, {}, eid)

    assert codes == {'add': 401, 'edit': 401, 'exclude': 401}
    db.session.expire_all()
    assert Candidate.query.count() == count_before
    cand = db.session.get(Candidate, cand_id)
    assert cand.party == party_before
    assert cand.is_excluded is False


# ---------------------------------------------------------------------------
# History entries
# ---------------------------------------------------------------------------
def test_add_candidate_creates_history_entry(client, app):
    eid = seed_demo(election_status=C.ELECTION_DRAFT)['election_id']
    r = client.post('/api/candidates/', json=new_house_candidate_payload(eid),
                    headers=login(client, 'delegate'))
    cid = r.get_json()['candidate']['id']

    entries = history_for(cid)
    assert len(entries) == 1
    assert entries[0].version == 1
    assert entries[0].action == 'created'
    assert entries[0].changes == {'name': {'old': None, 'new': 'Test Candidate'}}
    assert entries[0].changed_by == User.query.filter_by(username='delegate').first().id
    assert entries[0].changed_at is not None


def test_edit_candidate_records_old_and_new_values(client, app):
    seed_demo(election_status=C.ELECTION_DRAFT)
    cand = first_house_candidate()
    cand_id, old_name, old_party = cand.id, cand.name, cand.party

    r = client.put(f'/api/candidates/{cand_id}', json={'name': 'Renamed Person', 'party': 'New Party'},
                   headers=login(client, 'delegate'))
    assert r.status_code == 200

    entries = history_for(cand_id)
    assert len(entries) == 1
    assert entries[0].action == 'updated'
    assert entries[0].changes == {'name': {'old': old_name, 'new': 'Renamed Person'},
                                  'party': {'old': old_party, 'new': 'New Party'}}


def test_group_order_change_is_recorded(client, app):
    seed_demo(election_status=C.ELECTION_DRAFT)
    senator = Candidate.query.filter_by(contest_type='senate', group_code='A', group_order=1).first()
    senator_id = senator.id

    r = client.put(f'/api/candidates/{senator_id}', json={'group_order': 3, 'group_code': 'B'},
                   headers=login(client, 'delegate'))
    assert r.status_code == 200

    entries = history_for(senator_id)
    assert len(entries) == 1
    assert entries[0].changes == {'group_order': {'old': 1, 'new': 3},
                                  'group_code': {'old': 'A', 'new': 'B'}}


def test_exclusion_creates_history_entry(client, app):
    eid = seed_demo(election_status=C.ELECTION_DRAFT)['election_id']
    cand_id = first_house_candidate().id
    open_and_close_election(client, eid)

    exclude_with_approval(client, cand_id, 'Court order 123')

    entries = history_for(cand_id)
    assert [e.action for e in entries] == ['excluded']
    assert entries[0].changes == {'is_excluded': {'old': False, 'new': True},
                                  'excluded_reason': {'old': None, 'new': 'Court order 123'}}


def test_unchanged_edit_creates_no_history(client, app):
    seed_demo(election_status=C.ELECTION_DRAFT)
    cand = first_house_candidate()
    cand_id, same_party = cand.id, cand.party
    r = client.put(f'/api/candidates/{cand_id}', json={'party': same_party},
                   headers=login(client, 'delegate'))
    assert r.status_code == 200
    assert history_for(cand_id) == []


# ---------------------------------------------------------------------------
# Same transaction: the change and its history entry are saved together
# ---------------------------------------------------------------------------
def test_history_entry_saved_in_same_transaction_as_change(client, app, monkeypatch):
    eid = seed_demo(election_status=C.ELECTION_DRAFT)['election_id']
    delegate = login(client, 'delegate')
    cand = first_house_candidate()
    cand_id, party_before = cand.id, cand.party
    count_before = Candidate.query.count()

    def failing_record_change(*args, **kwargs):
        db.session.flush()          # the change reaches the open transaction ...
        raise RuntimeError('history write failed')   # ... but the history entry fails

    monkeypatch.setattr('app.routes.candidates.record_change', failing_record_change)

    with pytest.raises(RuntimeError):
        client.put(f'/api/candidates/{cand_id}', json={'party': 'Should Not Stick'}, headers=delegate)
    db.session.rollback()   # what the app does at the end of a failed request

    with pytest.raises(RuntimeError):
        client.post('/api/candidates/', json=new_house_candidate_payload(eid), headers=delegate)
    db.session.rollback()

    db.session.expire_all()
    assert db.session.get(Candidate, cand_id).party == party_before     # edit was not saved
    assert Candidate.query.count() == count_before                       # new candidate was not saved
    assert CandidateHistory.query.count() == 0


# ---------------------------------------------------------------------------
# Refused changes leave no history
# ---------------------------------------------------------------------------
@pytest.mark.parametrize('username', REFUSED_USERS)
def test_refused_change_creates_no_history(client, app, username):
    eid = seed_demo(election_status=C.ELECTION_DRAFT)['election_id']
    codes = attempt_candidate_changes(client, login(client, username), eid)
    assert set(codes.values()) == {403}
    assert CandidateHistory.query.count() == 0


def test_refused_change_creates_no_history_when_election_not_draft(client, app):
    seed_demo()   # election is open, so candidate edits are refused even for a delegate
    cand_id = first_house_candidate().id
    r = client.put(f'/api/candidates/{cand_id}', json={'party': 'Late Change'},
                   headers=login(client, 'delegate'))
    assert r.status_code == 409
    assert CandidateHistory.query.count() == 0


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------
def test_candidate_change_written_to_audit_log(client, app):
    eid = seed_demo(election_status=C.ELECTION_DRAFT)['election_id']
    delegate = login(client, 'delegate')
    cid = client.post('/api/candidates/', json=new_house_candidate_payload(eid),
                      headers=delegate).get_json()['candidate']['id']
    client.put(f'/api/candidates/{cid}', json={'party': 'Another Party'}, headers=delegate)
    open_and_close_election(client, eid)
    exclude_with_approval(client, cid, 'Court order 7')

    logs = AuditLog.query.filter_by(object_type='candidate', object_id=str(cid)).order_by(AuditLog.id).all()
    assert [l.action for l in logs] == ['CANDIDATE_ADDED', 'CANDIDATE_UPDATED', 'CANDIDATE_EXCLUDED']
    assert [l.username for l in logs] == ['delegate', 'delegate', 'delegate2']
    assert all(l.success for l in logs)

# ---------------------------------------------------------------------------
# History endpoint
# ---------------------------------------------------------------------------
def test_history_endpoint(client, app):
    eid = seed_demo(election_status=C.ELECTION_DRAFT)['election_id']
    delegate = login(client, 'delegate')
    created = client.post('/api/candidates/', headers=delegate, json={
        'election_id': eid, 'contest_type': 'house', 'division_id': 1, 'name': 'New One',
        'party': 'P', 'ballot_position': 7}).get_json()['candidate']
    cid = created['id']
    client.put(f'/api/candidates/{cid}', headers=delegate, json={'ballot_position': 2, 'party': 'P'})
    client.put(f'/api/candidates/{cid}', headers=delegate, json={'party': 'Q'})
    open_and_close_election(client, eid)
    exclude_with_approval(client, cid, 'Court order 9')

    r = client.get(f'/api/candidates/{cid}/history', headers=login(client, 'admin'))
    assert r.status_code == 200
    body = r.get_json()
    assert [h['version'] for h in body['history']] == [1, 2, 3, 4]
    assert [h['action'] for h in body['history']] == ['created', 'updated', 'updated', 'excluded']
    assert body['history'][1]['changes'] == {'ballot_position': {'old': 7, 'new': 2}}   # party unchanged -> not listed
    assert body['history'][0]['changed_by_username'] == 'delegate'

    assert client.get(f'/api/candidates/{cid}/history', headers=delegate).status_code == 200
    for u in ('voter1', 'officer'):
        assert client.get(f'/api/candidates/{cid}/history', headers=login(client, u)).status_code == 403
    assert client.get(f'/api/candidates/{cid}/history').status_code == 401
    assert client.get('/api/candidates/99999/history', headers=delegate).status_code == 404