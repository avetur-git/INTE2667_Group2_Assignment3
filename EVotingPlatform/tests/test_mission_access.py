import pytest

from app import create_app, db
from app.models import AuditLog, Ballot, User, Voter
from app.seed import seed_demo, DEMO_PASSWORD
from app.security import geoip
from app.security.mission_access import GENERIC_REFUSAL, DEMO_FINGERPRINT
from app.utils import constants as C
from tests.test_api_flow import login

GOOD_FP = DEMO_FINGERPRINT
OTHER_FP = 'cd' * 32
MISSION_IP = '198.51.100.20'      # inside the mission range; table says JP
AUSTRALIA_IP = '192.0.2.10'       # table says AU
FOREIGN_IP = '198.51.100.99'      # table says JP
UNKNOWN_IP = '100.64.1.5'         # not in the geo-IP table


@pytest.fixture()
def app():
    app = create_app({'TESTING': True, 'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
                    'SQLALCHEMY_BINDS': {'ballots': 'sqlite:///:memory:'},
                    'BCRYPT_ROUNDS': 4, 'ENFORCE_MISSION_ACCESS': True})
    with app.app_context():
        yield app
        db.session.remove()
        db.drop_all()
    geoip.reset_provider()


@pytest.fixture()
def world(client, app):
    """Seeded election plus one verified mission (Japan) with terminal DM-0001."""
    eid = seed_demo()['election_id']
    admin = login(client, 'admin')
    r = make_mission(client, admin)
    assert r.status_code == 201, r.get_json()
    return {'eid': eid, 'admin': admin, 'mission_id': r.get_json()['mission']['id']}


def make_mission(client, admin, **overrides):
    body = {'name': 'Demo Australian Embassy', 'country': 'JP', 'allowed_cidrs': ['198.51.100.0/24'],
            'cert_fingerprints': [GOOD_FP], 'terminal_ids': ['DM-0001']}
    body.update(overrides)
    return client.post('/api/missions/', json=body, headers=admin)


def add_overseas_voter(username):
    user = User(username, f'{username}@example.test', DEMO_PASSWORD, C.ROLE_VOTER)
    db.session.add(user)
    db.session.flush()
    template = Voter.query.filter_by(user_id=User.query.filter_by(username='voter_os').first().id).first()
    db.session.add(Voter(user_id=user.id, first_name='Over', last_name=username, date_of_birth=template.date_of_birth,
                         is_overseas=True, address_line='1 Abroad St', suburb='Abroadville', state='VIC',
                         postcode='3000', division_id=template.division_id,
                         enrolment_status=C.ENROLMENT_ENROLLED))
    db.session.commit()


def vote(client, username, eid, *, channel='diplomatic_mission', terminal='DM-0001', ip=MISSION_IP,
         fingerprint=GOOD_FP, extra_headers=None):
    headers = login(client, username)
    paper = client.get(f'/api/voting/ballot-paper?election_id={eid}', headers=headers).get_json()
    payload = {'election_id': eid, 'channel': channel, 'terminal_id': terminal,
               'house_preferences': [c['id'] for c in paper['house_candidates']],
               'senate_atl': {g['group_code']: i + 1 for i, g in enumerate(paper['senate_groups'][:6])}}
    if fingerprint:
        headers['X-Device-Cert-Fingerprint'] = fingerprint
    headers.update(extra_headers or {})
    return client.post('/api/voting/cast', json=payload, headers=headers,
                       environ_overrides={'REMOTE_ADDR': ip})


def last_refusal():
    db.session.expire_all()
    return AuditLog.query.filter_by(action='VOTE_ACCESS_REFUSED').order_by(AuditLog.id.desc()).first()


# ---------------------------------------------------------------------------
# Accepted
# ---------------------------------------------------------------------------
def test_vote_accepted_from_verified_mission_terminal(client, world):
    r = vote(client, 'voter_os', world['eid'])
    assert r.status_code == 201, r.get_json()
    assert Ballot.query.count() == 1


def test_fingerprint_with_colons_and_capitals_is_accepted(client, world):
    spaced = ':'.join(GOOD_FP[i:i + 2] for i in range(0, 64, 2)).upper()
    assert vote(client, 'voter_os', world['eid'], fingerprint=spaced).status_code == 201


def test_australian_voter_can_still_vote_online_from_australia(client, world):
    r = vote(client, 'voter1', world['eid'], channel='online', terminal=None, ip=AUSTRALIA_IP, fingerprint=None)
    assert r.status_code == 201


# ---------------------------------------------------------------------------
# Refused: mission and terminal
# ---------------------------------------------------------------------------
def test_vote_refused_from_unregistered_terminal(client, world):
    # a diplomatic mission terminal that no mission lists
    client.post('/api/terminals/', headers=world['admin'], json={
        'terminal_id': 'DM-0002', 'channel': 'diplomatic_mission', 'location_name': 'Unlisted post'})
    r = vote(client, 'voter_os', world['eid'], terminal='DM-0002')
    assert r.status_code == 403
    assert last_refusal().details['reason'] == 'terminal_not_in_a_mission'
    assert Ballot.query.count() == 0


def test_vote_refused_from_inactive_mission(client, world):
    client.put(f"/api/missions/{world['mission_id']}/deactivate", headers=world['admin'])
    r = vote(client, 'voter_os', world['eid'])
    assert r.status_code == 403
    assert last_refusal().details['reason'] == 'mission_inactive'


def test_deactivated_mission_blocks_voting_immediately(client, world):
    add_overseas_voter('voter_os2')
    assert vote(client, 'voter_os', world['eid']).status_code == 201       # works before
    assert client.put(f"/api/missions/{world['mission_id']}/deactivate",
                      headers=world['admin']).status_code == 200
    assert vote(client, 'voter_os2', world['eid']).status_code == 403      # refused straight after
    assert Ballot.query.count() == 1


# ---------------------------------------------------------------------------
# Refused: network range, certificate, location
# ---------------------------------------------------------------------------
def test_vote_refused_from_ip_outside_mission_range(client, world):
    r = vote(client, 'voter_os', world['eid'], ip='198.51.101.20')
    assert r.status_code == 403
    assert last_refusal().details['reason'] == 'ip_outside_mission_range'


def test_vote_refused_with_wrong_device_certificate(client, world):
    r = vote(client, 'voter_os', world['eid'], fingerprint=OTHER_FP)
    assert r.status_code == 403
    assert last_refusal().details['reason'] == 'device_certificate_not_approved'


def test_vote_refused_with_missing_device_certificate(client, world):
    r = vote(client, 'voter_os', world['eid'], fingerprint=None)
    assert r.status_code == 403
    assert last_refusal().details['reason'] == 'device_certificate_missing'


def test_vote_refused_with_revoked_device_certificate(client, world):
    r = client.post(f"/api/missions/{world['mission_id']}/revoke-cert", json={'fingerprint': GOOD_FP},
                    headers=world['admin'])
    assert r.status_code == 200
    r = vote(client, 'voter_os', world['eid'])
    assert r.status_code == 403
    assert last_refusal().details['reason'] == 'device_certificate_revoked'


def test_vote_refused_when_geoip_country_does_not_match_mission(client, world):
    # range 203.0.113.0/24 is Japan-registered on paper but the geo-IP table says NZ
    client.post('/api/terminals/', headers=world['admin'], json={
        'terminal_id': 'DM-0003', 'channel': 'diplomatic_mission', 'location_name': 'Second post'})
    assert make_mission(client, world['admin'], name='Second Post', allowed_cidrs=['203.0.113.0/24'],
                        terminal_ids=['DM-0003']).status_code == 201
    r = vote(client, 'voter_os', world['eid'], terminal='DM-0003', ip='203.0.113.7')
    assert r.status_code == 403
    entry = last_refusal()
    assert entry.details['reason'] == 'geoip_country_mismatch'
    assert entry.details['country'] == 'NZ'


def test_vote_refused_when_geoip_lookup_fails(client, world):
    client.post('/api/terminals/', headers=world['admin'], json={
        'terminal_id': 'DM-0004', 'channel': 'diplomatic_mission', 'location_name': 'Unmapped post'})
    assert make_mission(client, world['admin'], name='Unmapped Post', allowed_cidrs=['100.64.1.0/24'],
                        terminal_ids=['DM-0004']).status_code == 201
    r = vote(client, 'voter_os', world['eid'], terminal='DM-0004', ip=UNKNOWN_IP)
    assert r.status_code == 403
    assert last_refusal().details['reason'] == 'geoip_lookup_failed'


# ---------------------------------------------------------------------------
# Other channels: Australia only, overseas voters kept to missions
# ---------------------------------------------------------------------------
def test_online_vote_refused_from_outside_australia(client, world):
    r = vote(client, 'voter1', world['eid'], channel='online', terminal=None, ip=FOREIGN_IP, fingerprint=None)
    assert r.status_code == 403
    entry = last_refusal()
    assert entry.details['reason'] == 'outside_australia' and entry.details['country'] == 'JP'
    assert Ballot.query.count() == 0


def test_online_vote_refused_when_location_unknown(client, world):
    r = vote(client, 'voter1', world['eid'], channel='online', terminal=None, ip=UNKNOWN_IP, fingerprint=None)
    assert r.status_code == 403
    assert last_refusal().details['reason'] == 'geoip_lookup_failed'


def test_polling_station_vote_refused_from_outside_australia(client, world):
    r = vote(client, 'voter1', world['eid'], channel='polling_station', terminal='PS-0001',
             ip=FOREIGN_IP, fingerprint=None)
    assert r.status_code == 403
    assert last_refusal().details['reason'] == 'outside_australia'


def test_polling_station_vote_accepted_from_australia(client, world):
    assert vote(client, 'voter1', world['eid'], channel='polling_station', terminal='PS-0001',
                ip=AUSTRALIA_IP, fingerprint=None).status_code == 201


def test_overseas_voter_cannot_vote_online(client, world):
    r = vote(client, 'voter_os', world['eid'], channel='online', terminal=None, ip=AUSTRALIA_IP, fingerprint=None)
    assert r.status_code == 403
    assert 'diplomatic missions only' in r.get_json()['error']
    assert Ballot.query.count() == 0


def test_overseas_voter_cannot_use_polling_station(client, world):
    r = vote(client, 'voter_os', world['eid'], channel='polling_station', terminal='PS-0001',
             ip=AUSTRALIA_IP, fingerprint=None)
    assert r.status_code == 403
    assert Ballot.query.count() == 0


# ---------------------------------------------------------------------------
# What the voter sees and what is logged
# ---------------------------------------------------------------------------
def test_refusal_message_does_not_reveal_which_check_failed(client, world):
    attempts = [
        dict(ip='198.51.101.20'),                       # outside range
        dict(fingerprint=OTHER_FP),                     # wrong certificate
        dict(fingerprint=None),                         # missing certificate
        dict(terminal='PS-0001', channel='polling_station', ip=FOREIGN_IP, fingerprint=None),
    ]
    messages = set()
    for kwargs in attempts:
        r = vote(client, 'voter_os' if 'channel' not in kwargs else 'voter1', world['eid'], **kwargs)
        assert r.status_code == 403
        messages.add(r.get_json()['error'])
    assert messages == {GENERIC_REFUSAL}
    for word in ('certificate', 'fingerprint', 'range', 'country', 'geo', 'mission', 'IP'):
        assert word not in GENERIC_REFUSAL


def test_refusal_reason_written_to_audit_log(client, world):
    vote(client, 'voter_os', world['eid'], fingerprint=OTHER_FP)
    entry = last_refusal()
    assert entry is not None and entry.success is False
    assert entry.username == 'voter_os'
    assert entry.object_type == 'terminal' and entry.object_id == 'DM-0001'
    assert entry.details['reason'] == 'device_certificate_not_approved'
    assert entry.details['ip'] == MISSION_IP
    assert entry.details['channel'] == 'diplomatic_mission'
    assert entry.details['mission'] == 'Demo Australian Embassy'
    assert entry.created_at is not None


def test_ip_header_cannot_be_spoofed_unless_simulation_is_on(client, world):
    # simulation is OFF in tests: a forged header must not move the caller into the mission range
    r = vote(client, 'voter_os', world['eid'], ip='192.0.2.50',
             extra_headers={'X-Simulated-Client-IP': MISSION_IP})
    assert r.status_code == 403
    assert last_refusal().details['reason'] == 'ip_outside_mission_range'
    assert last_refusal().details['ip'] == '192.0.2.50'


def test_simulated_ip_header_works_only_when_simulation_is_on(client, world, app):
    app.config['SIMULATE_CLIENT_IP'] = True
    r = vote(client, 'voter_os', world['eid'], ip='192.0.2.50',
             extra_headers={'X-Simulated-Client-IP': MISSION_IP})
    assert r.status_code == 201


def test_switch_off_leaves_base_behaviour(client, world, app):
    app.config['ENFORCE_MISSION_ACCESS'] = False
    assert vote(client, 'voter_os', world['eid'], ip='192.0.2.50', fingerprint=None).status_code == 201


def test_installing_twice_does_not_stack_the_checks(app):
    from app.security.hooks import hooks
    from app.security.mission_access import install
    install(); install()
    hook = hooks.get('verify_terminal')
    assert hook._req19 and not getattr(hook._previous, '_req19', False)


# ---------------------------------------------------------------------------
# Managing missions
# ---------------------------------------------------------------------------
@pytest.mark.parametrize('username', ['voter1', 'officer', 'delegate'])
def test_only_admin_can_manage_missions(client, world, username):
    headers = login(client, username)
    assert client.get('/api/missions/', headers=headers).status_code == 403
    assert make_mission(client, headers, name='Sneaky Post').status_code == 403
    mid = world['mission_id']
    assert client.put(f'/api/missions/{mid}/deactivate', headers=headers).status_code == 403
    assert client.post(f'/api/missions/{mid}/revoke-cert', json={'fingerprint': GOOD_FP},
                       headers=headers).status_code == 403


def test_anonymous_cannot_manage_missions(client, world):
    assert client.get('/api/missions/').status_code == 401
    assert client.post('/api/missions/', json={}).status_code == 401


def test_admin_can_list_missions(client, world):
    body = client.get('/api/missions/', headers=world['admin']).get_json()
    assert body['total'] == 1 and body['missions'][0]['terminal_ids'] == ['DM-0001']


def test_mission_input_is_validated(client, world):
    admin = world['admin']
    assert make_mission(client, admin, name='A', allowed_cidrs=['not-a-network']).status_code == 400
    assert make_mission(client, admin, name='B', cert_fingerprints=['abc']).status_code == 400
    assert make_mission(client, admin, name='C', country='JPN').status_code == 400
    assert make_mission(client, admin, name='D', terminal_ids=['PS-0001']).status_code == 400   # not a mission terminal
    assert make_mission(client, admin, name='E', terminal_ids=['NOPE']).status_code == 400
    assert make_mission(client, admin).status_code == 409                                        # duplicate name


def test_mission_changes_written_to_audit_log(client, world):
    admin, mid = world['admin'], world['mission_id']
    client.post(f'/api/missions/{mid}/revoke-cert', json={'fingerprint': GOOD_FP}, headers=admin)
    client.put(f'/api/missions/{mid}/deactivate', headers=admin)
    logs = AuditLog.query.filter_by(object_type='mission', object_id=str(mid)).order_by(AuditLog.id).all()
    assert [l.action for l in logs] == ['MISSION_CREATED', 'MISSION_CERT_REVOKED', 'MISSION_DEACTIVATED']
    assert all(l.username == 'admin' and l.success for l in logs)