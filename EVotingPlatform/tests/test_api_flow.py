from app import db
from app.seed import seed_demo, DEMO_PASSWORD
from app.models import Ballot, VoteRecord, Candidate, AuditLog, Voter, Division
from app.security.hooks import hooks
from app.utils import constants as C


def login(client, username, password=DEMO_PASSWORD):
    r = client.post('/api/auth/login', json={'username': username, 'password': password})
    assert r.status_code == 200, r.get_json()
    return {'Authorization': f"Bearer {r.get_json()['access_token']}"}

def exclude_with_approval(client, candidate_id, reason, requester='delegate', approver='delegate2'):
    """REQ-18: exclusion is two steps. One delegate asks, a different delegate approves.
    Returns the approval response (200 means the candidate is now excluded)."""
    asked = client.post(f'/api/candidates/{candidate_id}/exclude', json={'reason': reason},
                        headers=login(client, requester))
    assert asked.status_code == 200, asked.get_json()
    return client.post(f"/api/approval-requests/{asked.get_json()['request_id']}/approve",
                       json={}, headers=login(client, approver))

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
    assert t.get_json()['senate_contests'] == len(C.STATES)   # demo data covers every state

    res = client.get(f'/api/results/{eid}').get_json()
    assert len(res['house']) == Division.query.count()
    vic = next(s for s in res['senate'] if s['state'] == 'VIC')
    assert len(vic['elected']) == 12
    # Nobody voted in the other states: no one is elected there (no winner from zero votes)
    assert all(h['winner'] is None for h in res['house'] if h['division']['state'] != 'VIC')
    assert all(s['elected'] == [] for s in res['senate'] if s['state'] != 'VIC')
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
    division = client.get('/api/elections/divisions?state=VIC').get_json()['divisions'][0]
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
    assert exclude_with_approval(client, cand.id, 'Court order 123').status_code == 200
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
