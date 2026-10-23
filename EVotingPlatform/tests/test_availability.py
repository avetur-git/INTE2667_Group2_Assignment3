"""REQ-08: health reporting and running more than one API replica.

  - /health reports this replica's status and its database checks; 503 when a
    database is unreachable, so the load balancer takes the replica out
  - two replicas on the same databases accept votes at the same time without
    breaking the one-vote rule or the REQ-02 ballot chain
  - signing keys are created once and shared, never overwritten by a replica
"""
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app import create_app, db
from app.models import AuditLog, Ballot, VoteRecord, Voter
from app.security.audit_chain import find_problems
from app.security.hooks import hooks
from app.security.keys import _create_once
from app.seed import DEMO_PASSWORD, seed_demo, seed_demo_voters


# --- /health -------------------------------------------------------------------

def test_health_endpoint_reports_status(client, monkeypatch):
    monkeypatch.setenv('INSTANCE_NAME', 'web2')
    r = client.get('/health')
    assert r.status_code == 200
    assert r.get_json() == {'status': 'healthy', 'instance': 'web2',
                            'checks': {'database': 'ok', 'ballot_database': 'ok'}}


def test_health_reports_unhealthy_when_a_database_is_down(client, app, monkeypatch):
    def unreachable(*args, **kwargs):
        raise ConnectionError('database host unreachable')
    monkeypatch.setattr(db.engines['ballots'], 'connect', unreachable)

    r = client.get('/health')
    assert r.status_code == 503      # the load balancer removes this replica
    body = r.get_json()
    assert body['status'] == 'unhealthy'
    assert body['checks'] == {'database': 'ok', 'ballot_database': 'error: ConnectionError'}


def test_health_needs_no_login(client):
    assert client.get('/health').status_code == 200


# --- Two replicas ----------------------------------------------------------------

@pytest.fixture()
def replicas(tmp_path):
    """Two app instances, like web and web2 in docker-compose, sharing one pair
    of database files."""
    config = {'TESTING': True, 'BCRYPT_ROUNDS': 4, 'MFA_REQUIRED': False,
              'RATE_LIMIT_ENABLED': False,
              'SQLALCHEMY_DATABASE_URI': f"sqlite:///{tmp_path / 'evoting.db'}",
              'SQLALCHEMY_BINDS': {'ballots': f"sqlite:///{tmp_path / 'ballots.db'}"}}
    first = create_app(config)
    with first.app_context():
        election_id = seed_demo()['election_id']
        seed_demo_voters(12)
    second = create_app(config)
    yield first, second, election_id
    for app in (first, second):
        with app.app_context():
            db.session.remove()
            for engine in db.engines.values():
                engine.dispose()


def _vote(app, username, election_id, start):
    client = app.test_client()
    login = client.post('/api/auth/login', json={'username': username, 'password': DEMO_PASSWORD})
    auth = {'Authorization': f"Bearer {login.get_json()['access_token']}"}
    paper = client.get(f'/api/voting/ballot-paper?election_id={election_id}', headers=auth).get_json()
    payload = {'election_id': election_id, 'channel': 'online',
               'house_preferences': [c['id'] for c in paper['house_candidates']],
               'senate_atl': {g['group_code']: i + 1 for i, g in enumerate(paper['senate_groups'][:6])}}
    start.wait()          # all voters cast at the same moment
    return client.post('/api/voting/cast', json=payload, headers=auth).status_code


def test_two_replicas_accept_concurrent_votes(replicas):
    first, second, election_id = replicas
    voters = [f'demo{i:03d}' for i in range(1, 13)]
    start = threading.Barrier(len(voters))
    with ThreadPoolExecutor(max_workers=len(voters)) as pool:
        statuses = list(pool.map(
            lambda pair: _vote(first if pair[0] % 2 else second, pair[1], election_id, start),
            enumerate(voters)))

    assert statuses == [201] * len(voters)
    with first.app_context():
        assert VoteRecord.query.filter_by(election_id=election_id).count() == len(voters)
        ballots = Ballot.query.filter_by(election_id=election_id).order_by(Ballot.seq).all()
        # One unbroken REQ-02 chain: positions 1..n, each linked to the one before
        assert [b.seq for b in ballots] == list(range(1, len(voters) + 1))
        assert hooks.run('verify_ballots', ballots) == []
        # Both replicas also wrote audit entries at once (CAST_BALLOT): the
        # REQ-04 audit hash chain must still be one unbroken chain
        assert AuditLog.query.filter_by(action='CAST_BALLOT').count() == len(voters)
        assert find_problems() == []


def test_a_voter_cannot_vote_once_per_replica(replicas):
    """Each replica sees the other's vote records, so REQ-13 still holds."""
    first, second, election_id = replicas
    assert _vote(first, 'demo001', election_id, threading.Barrier(1)) == 201

    client = second.test_client()
    token = client.post('/api/auth/login', json={
        'username': 'demo001', 'password': DEMO_PASSWORD}).get_json()['access_token']
    auth = {'Authorization': f'Bearer {token}'}
    paper = client.get(f'/api/voting/ballot-paper?election_id={election_id}', headers=auth)
    assert paper.status_code == 403 and paper.get_json()['reason_code'] == 'already_voted'
    cast = client.post('/api/voting/cast', json={'election_id': election_id}, headers=auth)
    assert cast.status_code in (403, 409)
    with second.app_context():
        assert Ballot.query.filter_by(election_id=election_id).count() == 1


def test_login_on_one_replica_works_on_the_other(replicas):
    first, second, election_id = replicas
    token = first.test_client().post('/api/auth/login', json={
        'username': 'demo002', 'password': DEMO_PASSWORD}).get_json()['access_token']
    r = second.test_client().get(f'/api/voting/ballot-paper?election_id={election_id}',
                                 headers={'Authorization': f'Bearer {token}'})
    assert r.status_code == 200


# --- Shared signing keys -----------------------------------------------------------

def _pem(key):
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())


def test_signing_key_is_created_once(tmp_path):
    """If two replicas create the same key at once, the first one written wins."""
    path = tmp_path / 'receipt_key.pem'
    first, second = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    _create_once(path, first)
    _create_once(path, second)
    assert path.read_bytes() == _pem(first)
    assert [p.name for p in tmp_path.iterdir()] == ['receipt_key.pem']   # no temp files left


# --- Demo data ------------------------------------------------------------------------

def test_seed_demo_voters(app):
    seed_demo()
    assert len(seed_demo_voters(5)) == 5
    assert seed_demo_voters(5) == []          # running it again adds nobody
    assert Voter.query.count() == 6 + 5
