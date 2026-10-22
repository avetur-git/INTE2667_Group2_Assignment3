"""REQ-09: the API is stateless, so replicas can be added.

The last per-replica state was the REQ-07 rate-limit counters. With
RATE_LIMIT_STORAGE_URL set they live in Redis, shared by every replica:
  - a client cannot get N times the limit by spreading requests over N replicas
  - a throttled client is audited once, not once per replica
  - if Redis is unreachable each replica falls back to its own counters
Here Redis is fakeredis (same commands and Lua scripting, in memory).
"""
import fakeredis
import pytest
import redis

from app import db
from app.models import AuditLog, User
from app.security.rate_limit import RATE_LIMITED_ACTION, RedisRateLimiter
from app.seed import seed_demo, seed_demo_voters
from tests.test_rate_limit import ATTACKER_IP, NORMAL_IP, bad_login, make_app


@pytest.fixture()
def redis_server(monkeypatch):
    """One fake Redis server; every app created in the test connects to it."""
    server = fakeredis.FakeServer()
    monkeypatch.setattr(redis.Redis, 'from_url',
                        lambda url, **kwargs: fakeredis.FakeRedis(server=server))
    return server


@pytest.fixture()
def replicas(tmp_path, redis_server):
    """Three app instances with shared databases and shared rate-limit storage,
    like web and two scaled-out web2 containers."""
    config = {'SQLALCHEMY_DATABASE_URI': f"sqlite:///{tmp_path / 'evoting.db'}",
              'SQLALCHEMY_BINDS': {'ballots': f"sqlite:///{tmp_path / 'ballots.db'}"},
              'RATE_LIMIT_STORAGE_URL': 'redis://rate-limits:6379/0'}
    apps = [make_app(**config) for _ in range(3)]
    yield apps
    for app in apps:
        with app.app_context():
            db.session.remove()
            for engine in db.engines.values():
                engine.dispose()


def throttle_entries(app):
    with app.app_context():
        return AuditLog.query.filter_by(action=RATE_LIMITED_ACTION).all()


def test_replicas_use_the_shared_store(replicas):
    assert all(isinstance(app.extensions['rate_limiter'], RedisRateLimiter) for app in replicas)


def test_limit_is_shared_across_replicas(replicas):
    """Login limit is 3 per 60 s. Spreading attempts over replicas does not raise it."""
    clients = [app.test_client() for app in replicas]
    assert [bad_login(c).status_code for c in clients] == [401, 401, 401]

    for client in clients:          # every replica now refuses this client
        r = bad_login(client)
        assert r.status_code == 429
        assert 1 <= int(r.headers['Retry-After']) <= 60


def test_throttled_client_is_audited_once_not_once_per_replica(replicas):
    clients = [app.test_client() for app in replicas]
    for client in clients:
        bad_login(client)
    for client in clients * 3:
        assert bad_login(client).status_code == 429

    entries = throttle_entries(replicas[0])
    assert len(entries) == 1 and entries[0].ip_address == ATTACKER_IP


def test_other_clients_keep_their_own_limit(replicas):
    clients = [app.test_client() for app in replicas]
    for client in clients:
        bad_login(client)
    assert bad_login(clients[0]).status_code == 429
    assert bad_login(clients[1], ip=NORMAL_IP).status_code == 401


def test_store_unreachable_falls_back_to_local_limits(replicas, redis_server):
    redis_server.connected = False        # Redis down
    client = replicas[0].test_client()
    statuses = [bad_login(client).status_code for _ in range(4)]
    assert statuses == [401, 401, 401, 429]   # still limited, by this replica alone


def test_store_back_after_outage(replicas, redis_server):
    redis_server.connected = False
    bad_login(replicas[0].test_client())
    redis_server.connected = True
    clients = [app.test_client() for app in replicas]
    assert [bad_login(c).status_code for c in clients] == [401, 401, 401]
    assert bad_login(clients[0]).status_code == 429


# --- Audit chain under concurrent load ---------------------------------------------

def test_audit_chain_survives_concurrent_writes(replicas):
    """40 audit entries written at once through 2 replicas all land in one valid
    REQ-04 chain. (The collisions the Docker load test found need slow shared
    storage and busy 1-CPU replicas to show up, so this checks correctness under
    concurrency; the load test in loadtest/ is what showed the fix works.)"""
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from app.security.audit_chain import find_problems
    from app.services.audit import log_action

    start = threading.Barrier(40)

    def write(i):
        app = replicas[i % 2]
        with app.app_context():
            start.wait()
            log_action('LOAD_TEST_ENTRY', object_type='test', object_id=i)

    with ThreadPoolExecutor(max_workers=40) as pool:
        list(pool.map(write, range(40)))      # re-raises any failure

    with replicas[0].app_context():
        assert AuditLog.query.filter_by(action='LOAD_TEST_ENTRY').count() == 40
        assert find_problems() == []


# --- Load-test voters ------------------------------------------------------------

def test_bulk_voters_share_one_full_cost_hash(app):
    seed_demo()
    seed_demo_voters(50, prefix='load')
    users = User.query.filter(User.username.like('load%')).all()
    assert len(users) == 50
    assert len({u.password_hash for u in users}) == 1      # hashed once...
    assert all(u.check_password('ChangeMe123!') for u in users[:3])   # ...and logins still work
    assert not users[0].check_password('wrong')
