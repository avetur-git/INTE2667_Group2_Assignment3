"""REQ-15: every privileged action is recorded with all six required fields."""
import pytest
from flask import Blueprint, jsonify

from app import db
from app.models import AuditLog, Election
from app.security.audit_trail import (audit, audited, check_privileged_routes_audited,
                                      unaudited_privileged_routes)
from app.security.rbac import P, permission_required
from app.seed import seed_demo, DEMO_PASSWORD


def login(client, username):
    r = client.post('/api/auth/login', json={'username': username, 'password': DEMO_PASSWORD})
    assert r.status_code == 200, r.get_json()
    return {'Authorization': f"Bearer {r.get_json()['access_token']}", 'User-Agent': 'pytest-agent'}


@pytest.fixture()
def admin(client, app):
    seed_demo()
    return login(client, 'admin')


def entries(action):
    db.session.expire_all()
    return AuditLog.query.filter_by(action=action).order_by(AuditLog.id).all()


# ------------------------------------------------- acceptance tests (issue #20)

def test_audit_entry_has_all_fields(client, admin):
    r = client.post('/api/elections/', json={'name': 'By-election', 'election_date': '2030-01-01'},
                    headers=admin)
    assert r.status_code == 201
    [entry] = entries('ELECTION_CREATED')
    assert entry.user_id is not None and entry.username == 'admin'            # who
    assert entry.action == 'ELECTION_CREATED'                                 # what
    assert entry.object_type == 'election'                                    # on what
    assert entry.object_id == str(r.get_json()['election']['id'])
    assert entry.created_at is not None                                       # when
    assert entry.ip_address and entry.user_agent == 'pytest-agent'            # where
    assert entry.session_id                                                   #   (JWT id)
    assert entry.success is True                                              # outcome


def test_failed_action_logged(client, admin):
    r = client.put('/api/elections/9999/status', json={'status': 'open'}, headers=admin)
    assert r.status_code == 404
    [entry] = entries('ELECTION_STATUS_CHANGED')
    assert entry.success is False
    assert entry.object_id == '9999'
    assert entry.details['status'] == 404
    assert entry.details['error'] == 'Election not found'


def test_unaudited_admin_endpoint_rejected(app):
    bp = Blueprint('rogue', __name__)

    @bp.route('/rogue', methods=['POST'])
    @permission_required(P.ELECTION_MANAGE)
    def rogue(current_user):
        return jsonify({}), 200

    app.register_blueprint(bp, url_prefix='/api/rogue')
    assert '/api/rogue/rogue (rogue.rogue)' in unaudited_privileged_routes(app)
    with pytest.raises(RuntimeError, match='rogue'):
        check_privileged_routes_audited(app)


# ------------------------------------------------------------ more behaviour

def test_route_details_recorded(client, admin):
    eid = Election.query.first().id
    client.put(f'/api/elections/{eid}/status', json={'status': 'closed'}, headers=admin)
    [entry] = entries('ELECTION_STATUS_CHANGED')
    assert entry.success is True
    assert entry.details == {'from': 'open', 'to': 'closed'}


def test_privileged_reads_are_audited(client, app):
    seed_demo()
    officer = login(client, 'officer')
    client.get('/api/voters/?q=Smith&status=enrolled', headers=officer)
    [entry] = entries('VOTER_ROLL_VIEWED')
    assert entry.username == 'officer' and entry.success is True
    assert entry.details == {'status': 'enrolled', 'name_search': True}
    assert 'Smith' not in str(entry.details)   # searched names are personal data


def test_refused_request_logged_once_as_access_denied(client, app):
    seed_demo()
    voter = login(client, 'voter1')
    assert client.post('/api/elections/', json={}, headers=voter).status_code == 403
    assert len(entries('ACCESS_DENIED')) == 1
    assert entries('ELECTION_CREATED') == []


def _closed_election_candidate(client, admin):
    from app.models import Candidate
    election = Election.query.first()
    client.put(f'/api/elections/{election.id}/status', json={'status': 'closed'}, headers=admin)
    return Candidate.query.filter_by(election_id=election.id).first()


def test_exclusion_request_recorded_on_the_approval_request(client, admin):
    cand = _closed_election_candidate(client, admin)
    r = client.post(f'/api/candidates/{cand.id}/exclude', json={'reason': 'Court order 1'},
                    headers=login(client, 'delegate'))
    assert r.status_code == 200
    [entry] = entries('APPROVAL_REQUEST_CREATED')
    assert (entry.object_type, entry.object_id) == ('approval_request', str(r.get_json()['request_id']))
    assert entry.details['target'] == f'Candidate {cand.name}' and entry.success is True
    assert entries('CANDIDATE_EXCLUSION_REQUESTED') == []


def test_refused_exclusion_recorded_as_failure(client, admin):
    from app.security.hooks import hooks
    cand = _closed_election_candidate(client, admin)
    hooks.register('authorise_action', lambda *args: (False, 'Second approver required'))
    try:
        r = client.post(f'/api/candidates/{cand.id}/exclude', json={'reason': 'Court order 2'},
                        headers=login(client, 'delegate'))
    finally:
        hooks.reset('authorise_action')
    assert r.status_code == 403
    [entry] = entries('CANDIDATE_EXCLUSION_REFUSED')
    assert (entry.object_type, entry.object_id) == ('candidate', str(cand.id))
    assert entry.success is False
    assert entry.details['message'] == 'Second approver required'


def test_candidate_history_view_audited(client, app):
    from app.models import Candidate
    seed_demo()
    cand = Candidate.query.first()
    client.get(f'/api/candidates/{cand.id}/history', headers=login(client, 'delegate'))
    [entry] = entries('CANDIDATE_HISTORY_VIEWED')
    assert (entry.username, entry.object_id, entry.success) == ('delegate', str(cand.id), True)


def test_mission_list_view_audited(client, admin):
    client.get('/api/missions/', headers=admin)
    [entry] = entries('MISSIONS_VIEWED')
    assert entry.username == 'admin' and entry.object_type == 'mission' and entry.success is True


def test_voter_actions_are_not_privileged(app):
    # Voter routes (enrol, cast a ballot...) are not required to use @audited
    assert not any('/api/voting/' in r or '/api/voters/me' in r
                   for r in unaudited_privileged_routes(app))


# ------------------------------------------------- decorator safety and errors

@pytest.fixture()
def probe(app, client):
    """Throwaway privileged routes for testing the decorator itself. Routes must
    be added before the app handles its first request, so log in afterwards."""
    bp = Blueprint('probe', __name__)

    @bp.route('/fail', methods=['POST'])
    @permission_required(P.ELECTION_MANAGE)
    @audited('PROBE_FAILED', object_type='election')
    def fail(current_user):
        Election.query.first().name = 'changed but not committed'
        return jsonify({'error': 'Nope'}), 400

    @bp.route('/crash', methods=['POST'])
    @permission_required(P.ELECTION_MANAGE)
    @audited('PROBE_CRASHED', object_type='election')
    def crash(current_user):
        raise ZeroDivisionError

    @bp.route('/extra', methods=['POST'])
    @permission_required(P.ELECTION_MANAGE)
    @audited('PROBE_MAIN', object_type='election')
    def extra(current_user):
        audit.target(1)
        audit.also('PROBE_SECOND', 'candidate', 7, details={'why': 'test'})
        return jsonify({}), 200

    app.register_blueprint(bp, url_prefix='/api/probe')
    seed_demo()
    return login(client, 'admin')


def test_failure_rolls_back_uncommitted_changes(client, probe):
    original = Election.query.first().name
    assert client.post('/api/probe/fail', headers=probe).status_code == 400
    db.session.expire_all()
    assert Election.query.first().name == original
    [entry] = entries('PROBE_FAILED')
    assert entry.success is False and entry.details['error'] == 'Nope'


def test_crash_is_logged_then_raised(client, probe):
    with pytest.raises(ZeroDivisionError):
        client.post('/api/probe/crash', headers=probe)
    [entry] = entries('PROBE_CRASHED')
    assert entry.success is False
    assert entry.details['error'] == 'ZeroDivisionError'


def test_second_object_recorded_in_same_format(client, probe):
    assert client.post('/api/probe/extra', headers=probe).status_code == 200
    [main] = entries('PROBE_MAIN')
    [second] = entries('PROBE_SECOND')
    assert (second.object_type, second.object_id, second.details) == ('candidate', '7', {'why': 'test'})
    assert (second.username, second.session_id, second.success) == (main.username, main.session_id, True)


def test_audited_needs_action_and_object_type():
    with pytest.raises(ValueError):
        audited('', object_type='election')
    with pytest.raises(ValueError):
        audited('SOMETHING', object_type=None)


def test_audited_must_sit_under_permission_required(app):
    @audited('WRONG_ORDER', object_type='election')
    def view():
        return 'ok'

    with app.test_request_context(), pytest.raises(RuntimeError, match='permission_required'):
        view()


def test_audit_outside_audited_route_fails(app):
    with app.test_request_context(), pytest.raises(RuntimeError):
        audit.details['x'] = 1
