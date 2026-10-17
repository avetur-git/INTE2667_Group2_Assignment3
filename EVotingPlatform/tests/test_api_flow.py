from app import db
from app.seed import seed_demo, DEMO_PASSWORD
from app.models import Ballot, VoteRecord, Candidate, AuditLog, Voter
from app.security.hooks import hooks
from app.utils import constants as C
import pytest


def login(client, username, password=DEMO_PASSWORD):
    r = client.post('/api/auth/login', json={'username': username, 'password': password})
    assert r.status_code == 200, r.get_json()
    return {'Authorization': f"Bearer {r.get_json()['access_token']}"}


def full_vote(client, headers, election_id, channel='online', terminal_id=None):
    paper = client.get(f'/api/voting/ballot-paper?election_id={election_id}', headers=headers).get_json()
    house = [c['id'] for c in paper['house_candidates']]
    groups = [g['group_code'] for g in paper['senate_groups']]
    payload = {'election_id': election_id, 'channel': channel, 'terminal_id': terminal_id,
               'house_preferences': house,
               'senate_atl': {g: i + 1 for i, g in enumerate(groups[:6])}}
    return client.post('/api/voting/cast', json=payload, headers=headers)


def test_health(client):
    assert client.get('/health').get_json()['status'] == 'healthy'


def test_full_election_lifecycle(client, app):
    result = seed_demo()
    eid = result['election_id']

    # Voters vote through different channels
    assert full_vote(client, login(client, 'voter1'), eid).status_code == 201
    assert full_vote(client, login(client, 'voter3'), eid, 'polling_station', 'PS-0001').status_code == 201
    r = full_vote(client, login(client, 'voter_os'), eid, 'diplomatic_mission', 'DM-0001')
    assert r.status_code == 201
    assert 'receipt_code' in r.get_json()['receipt']

    assert Ballot.query.count() == 3 and VoteRecord.query.count() == 3
    # Ballots carry no voter identity at all
    assert not any('voter' in c.name for c in Ballot.__table__.columns)

    admin = login(client, 'admin')
    turnout = client.get(f'/api/elections/{eid}/turnout', headers=admin).get_json()
    assert turnout['voters_who_voted'] == 3

    # Tally needs a closed election
    assert client.post(f'/api/elections/{eid}/tally', headers=admin).status_code == 409
    client.put(f'/api/elections/{eid}/status', json={'status': 'closed'}, headers=admin)
    t = client.post(f'/api/elections/{eid}/tally', headers=admin)
    assert t.status_code == 200, t.get_json()
    assert t.get_json()['senate_contests'] == 1

    res = client.get(f'/api/results/{eid}').get_json()
    assert len(res['house']) == 3
    assert len(res['senate'][0]['elected']) == 12
    assert client.get(f'/api/results/{eid}/export', headers=admin).status_code == 200


def test_duplicate_vote_is_refused(client, app):
    eid = seed_demo()['election_id']
    h = login(client, 'voter1')
    assert full_vote(client, h, eid).status_code == 201
    r = client.post('/api/voting/cast', json={'election_id': eid}, headers=h)
    assert r.status_code == 409
    assert r.get_json()['reason_code'] == 'already_voted'


def test_channel_rules(client, app):
    eid = seed_demo()['election_id']
    # in-Australia voter cannot use a diplomatic mission; overseas voter cannot vote online
    assert full_vote(client, login(client, 'voter2'), eid, 'diplomatic_mission', 'DM-0001').status_code == 403
    assert full_vote(client, login(client, 'voter_os'), eid, 'online').status_code == 403
    # terminal channels need a real terminal
    assert full_vote(client, login(client, 'voter2'), eid, 'polling_station', 'NOPE').status_code == 403


def test_informal_votes_are_rejected(client, app):
    eid = seed_demo()['election_id']
    h = login(client, 'voter1')
    paper = client.get(f'/api/voting/ballot-paper?election_id={eid}', headers=h).get_json()
    house = [c['id'] for c in paper['house_candidates']]
    base = {'election_id': eid, 'channel': 'online'}
    # missing a House candidate
    r = client.post('/api/voting/cast', headers=h, json={**base, 'house_preferences': house[:-1],
                    'senate_atl': {'A': 1, 'B': 2, 'C': 3, 'D': 4, 'E': 5, 'F': 6}})
    assert r.status_code == 422
    # too few Senate boxes
    r = client.post('/api/voting/cast', headers=h, json={**base, 'house_preferences': house,
                    'senate_atl': {'A': 1, 'B': 2}})
    assert r.status_code == 422
    assert Ballot.query.count() == 0


def test_below_the_line_beats_above_the_line(client, app):
    eid = seed_demo()['election_id']
    h = login(client, 'voter1')
    paper = client.get(f'/api/voting/ballot-paper?election_id={eid}', headers=h).get_json()
    house = [c['id'] for c in paper['house_candidates']]
    all_senate = [c['id'] for g in paper['senate_groups'] for c in g['candidates']] + \
                 [c['id'] for c in paper['senate_ungrouped']]
    btl = {str(cid): i + 1 for i, cid in enumerate(all_senate[:12])}
    r = client.post('/api/voting/cast', headers=h, json={
        'election_id': eid, 'channel': 'online', 'house_preferences': house,
        'senate_atl': {'A': 1, 'B': 2, 'C': 3, 'D': 4, 'E': 5, 'F': 6}, 'senate_btl': btl})
    assert r.status_code == 201
    assert Ballot.query.first().senate_source == 'btl'


def test_roles_are_enforced(client, app):
    seed_demo()
    voter = login(client, 'voter1')
    assert client.post('/api/elections/', json={'name': 'x', 'election_date': '2030-01-01'},
                       headers=voter).status_code == 403
    assert client.get('/api/admin/audit-logs', headers=voter).status_code == 403
    assert client.get('/api/admin/audit-logs').status_code == 401
    delegate = login(client, 'delegate')
    assert client.get('/api/admin/audit-logs', headers=delegate).status_code == 403


def test_enrolment_and_address_update(client, app):
    seed_demo()
    r = client.post('/api/auth/register', json={'username': 'newcitizen', 'email': 'n@example.test',
                                                'password': 'secret123'})
    h = {'Authorization': f"Bearer {r.get_json()['access_token']}"}
    assert client.get('/api/voters/me', headers=h).get_json()['status'] == 'not_enrolled'
    division = client.get('/api/elections/divisions').get_json()['divisions'][0]
    e = client.post('/api/voters/enrol', headers=h, json={
        'first_name': 'New', 'last_name': 'Citizen', 'date_of_birth': '1999-01-01',
        'address_line': '1 Test St', 'suburb': 'Testville', 'state': 'VIC', 'postcode': '3000',
        'division_id': division['id']})
    assert e.status_code == 201
    voter_id = e.get_json()['voter']['id']
    assert client.put('/api/voters/me/address', headers=h, json={'suburb': 'X'}).status_code == 403  # pending
    officer = login(client, 'officer')
    assert client.put(f'/api/voters/{voter_id}/approve', headers=officer).status_code == 200
    u = client.put('/api/voters/me/address', headers=h, json={'address_line': '2 New St'})
    assert u.status_code == 200 and u.get_json()['voter']['address_line'] == '2 New St'


def test_exclusion_and_recount(client, app):
    eid = seed_demo()['election_id']
    for u in ('voter1', 'voter2'):
        assert full_vote(client, login(client, u), eid).status_code == 201
    admin, delegate = login(client, 'admin'), login(client, 'delegate')
    cand = Candidate.query.filter_by(contest_type='house', division_id=1).first()
    # cannot exclude before close
    assert client.post(f'/api/candidates/{cand.id}/exclude', json={'reason': 'court'},
                       headers=delegate).status_code == 409
    client.put(f'/api/elections/{eid}/status', json={'status': 'closed'}, headers=admin)
    client.post(f'/api/elections/{eid}/tally', headers=admin)
    assert client.post(f'/api/candidates/{cand.id}/exclude', json={'reason': 'Court order 123'},
                       headers=delegate).status_code == 200
    assert client.post(f'/api/elections/{eid}/tally', headers=admin).status_code == 200
    assert AuditLog.query.filter_by(action='CANDIDATE_EXCLUDED').count() == 1


def test_hooks_can_be_replaced(client, app):
    """Proves a security solution can be added without touching routes."""
    eid = seed_demo()['election_id']
    try:
        hooks.register('verify_terminal', lambda terminal, meta: (False, 'Software signature invalid'))
        r = full_vote(client, login(client, 'voter1'), eid, 'polling_station', 'PS-0001')
        assert r.status_code == 403 and 'signature' in r.get_json()['error']
    finally:
        hooks.reset()
    assert full_vote(client, login(client, 'voter1'), eid, 'polling_station', 'PS-0001').status_code == 201

# ---------------------------------------------------------------------------
# REQ-17: only a Commissioner's delegate can manage candidates
# ---------------------------------------------------------------------------
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