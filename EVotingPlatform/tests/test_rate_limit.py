"""REQ-07: rate limiting on the login and voting endpoints.

  - a client over the limit gets 429 with Retry-After
  - clients under the limit, and other clients, are not affected
  - the first refusal per client and window is audited as RATE_LIMITED
"""
import pytest

from app import create_app, db
from app.models import AuditLog
from app.seed import seed_demo, DEMO_PASSWORD
from app.security.rate_limit import RATE_LIMITED_ACTION, parse_limit, rate_limited

LOGIN = '/api/auth/login'
ATTACKER_IP = '203.0.113.66'
NORMAL_IP = '198.51.100.7'


def make_app(**config):
    settings = {'TESTING': True, 'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
                'SQLALCHEMY_BINDS': {'ballots': 'sqlite:///:memory:'},
                'BCRYPT_ROUNDS': 4, 'MFA_REQUIRED': False,
                'RATE_LIMIT_ENABLED': True,
                'RATE_LIMIT_LOGIN': '3/60', 'RATE_LIMIT_VOTE': '2/60'}
    settings.update(config)
    return create_app(settings)


@pytest.fixture()
def limited_app():
    app = make_app()
    with app.app_context():
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(limited_app):
    return limited_app.test_client()


@pytest.fixture()
def clock(limited_app):
    """Controllable time for the limiter."""
    now = [1000.0]
    limiter = limited_app.extensions['rate_limiter']
    limiter.clock = lambda: now[0]
    return now


def bad_login(client, ip=ATTACKER_IP):
    return client.post(LOGIN, json={'username': 'nobody', 'password': 'guess'},
                       environ_base={'REMOTE_ADDR': ip})


def good_login(client, username='voter1', ip=NORMAL_IP):
    return client.post(LOGIN, json={'username': username, 'password': DEMO_PASSWORD},
                       environ_base={'REMOTE_ADDR': ip})


def throttle_entries():
    return AuditLog.query.filter_by(action=RATE_LIMITED_ACTION).all()


# --- Required tests (REQ-07 card) ---------------------------------------------

def test_requests_over_limit_return_429(client, clock):
    for _ in range(3):
        assert bad_login(client).status_code == 401

    r = bad_login(client)
    assert r.status_code == 429
    assert int(r.headers['Retry-After']) == 60
    assert 'Too many requests' in r.get_json()['error']

    # Refused requests are not passed to the login code at all
    assert AuditLog.query.filter_by(action='LOGIN_FAILED').count() == 3


def test_requests_under_limit_succeed(client, clock):
    seed_demo()
    # A normal client stays under the limit and logs in
    assert good_login(client).status_code == 200

    # While another address is being throttled...
    for _ in range(5):
        bad_login(client)
    assert bad_login(client).status_code == 429

    # ...normal clients on other addresses are not affected
    assert good_login(client, 'voter2').status_code == 200
    assert good_login(client, 'voter3', ip='198.51.100.8').status_code == 200


def test_throttle_event_logged(client, clock):
    for _ in range(3):
        bad_login(client)
    assert bad_login(client).status_code == 429

    entries = throttle_entries()
    assert len(entries) == 1
    entry = entries[0]
    assert entry.ip_address == ATTACKER_IP
    assert entry.object_type == 'endpoint'
    assert entry.object_id == f'POST {LOGIN}'
    assert entry.success is False
    assert entry.details == {'rule': 'login', 'limit': 3, 'window_seconds': 60,
                             'counted_by': 'ip'}


# --- Further behaviour ---------------------------------------------------------

def test_flood_is_logged_once_per_window(client, clock):
    for _ in range(3):
        bad_login(client)
    for _ in range(50):
        assert bad_login(client).status_code == 429
    assert len(throttle_entries()) == 1


def test_limit_resets_after_window(client, clock):
    for _ in range(3):
        bad_login(client)
    assert bad_login(client).status_code == 429

    clock[0] += 61
    assert bad_login(client).status_code == 401      # allowed through again

    # A new breach in the new window is logged again
    for _ in range(2):
        bad_login(client)
    assert bad_login(client).status_code == 429
    assert len(throttle_entries()) == 2


def test_retry_after_counts_down(client, clock):
    for _ in range(3):
        bad_login(client)
    clock[0] += 45
    assert bad_login(client).headers['Retry-After'] == '15'


def test_voting_is_counted_per_voter_not_per_ip(client, clock):
    """Voters at one polling station share an IP; one voter's retries must not block the next."""
    eid = seed_demo()['election_id']
    paper = f'/api/voting/ballot-paper?election_id={eid}'
    station = {'REMOTE_ADDR': '192.0.2.10'}

    def headers(username):
        token = client.post(LOGIN, json={'username': username, 'password': DEMO_PASSWORD},
                            environ_base=station).get_json()['access_token']
        return {'Authorization': f'Bearer {token}'}

    voter1, voter2 = headers('voter1'), headers('voter2')
    for _ in range(2):
        assert client.get(paper, headers=voter1, environ_base=station).status_code == 200
    assert client.get(paper, headers=voter1, environ_base=station).status_code == 429

    assert client.get(paper, headers=voter2, environ_base=station).status_code == 200

    entry = throttle_entries()[0]
    assert entry.username == 'voter1'
    assert entry.details['counted_by'] == 'user'


def test_cast_is_rate_limited(client, clock):
    eid = seed_demo()['election_id']
    token = good_login(client).get_json()['access_token']
    auth = {'Authorization': f'Bearer {token}'}
    statuses = [client.post('/api/voting/cast', json={'election_id': eid}, headers=auth,
                            environ_base={'REMOTE_ADDR': NORMAL_IP}).status_code
                for _ in range(3)]
    assert 429 not in statuses[:2] and statuses[2] == 429


def test_forwarded_header_ignored_without_trusted_proxy(client, clock):
    """A client cannot dodge the limit by sending a new X-Forwarded-For each time."""
    statuses = [client.post(LOGIN, json={'username': 'x', 'password': 'y'},
                            headers={'X-Forwarded-For': f'10.0.0.{i}'},
                            environ_base={'REMOTE_ADDR': ATTACKER_IP}).status_code
                for i in range(4)]
    assert statuses == [401, 401, 401, 429]


def test_trusted_proxy_client_address_is_used():
    """Behind nginx (TRUSTED_PROXY_COUNT=1) each real client gets its own limit."""
    app = make_app(TRUSTED_PROXY_COUNT=1)
    with app.app_context():
        client = app.test_client()
        proxy = {'REMOTE_ADDR': '172.18.0.5'}

        def via_proxy(client_ip):
            return client.post(LOGIN, json={'username': 'x', 'password': 'y'},
                               headers={'X-Forwarded-For': client_ip}, environ_base=proxy)

        for _ in range(3):
            via_proxy(ATTACKER_IP)
        assert via_proxy(ATTACKER_IP).status_code == 429
        assert via_proxy(NORMAL_IP).status_code == 401      # different client, not throttled
        assert throttle_entries()[0].ip_address == ATTACKER_IP
        db.session.remove()
        db.drop_all()


def test_disabled_rate_limit_lets_everything_through():
    app = make_app(RATE_LIMIT_ENABLED=False)
    with app.app_context():
        client = app.test_client()
        assert all(bad_login(client).status_code == 401 for _ in range(10))
        db.session.remove()
        db.drop_all()


def test_bad_limit_fails_at_startup():
    with pytest.raises(ValueError):
        make_app(RATE_LIMIT_LOGIN='lots')
    with pytest.raises(ValueError):
        parse_limit('0/60')


def test_unknown_rule_fails_at_import():
    with pytest.raises(ValueError):
        rate_limited('logn')
