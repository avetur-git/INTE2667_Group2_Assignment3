from app import db
from app.models import  Candidate
from app.security.hooks import hooks
from app.utils import constants as C
import pytest

REFUSED_USERS = ['voter1', 'officer', 'admin']


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
    r = client.post(f'/api/candidates/{cand_id}/exclude', json={'reason': 'Court order 123'},
                    headers=login(client, 'delegate'))
    assert r.status_code == 200
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

from app.seed import seed_demo
from app.utils import constants as C
from tests.test_api_flow import login


def open_and_close_election(client, election_id):
    admin = login(client, 'admin')
    assert client.put(f'/api/elections/{election_id}/status', json={'status': 'open'},
                      headers=admin).status_code == 200
    assert client.put(f'/api/elections/{election_id}/status', json={'status': 'closed'},
                      headers=admin).status_code == 200


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
    client.post(f'/api/candidates/{cid}/exclude', headers=delegate, json={'reason': 'Court order 9'})

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