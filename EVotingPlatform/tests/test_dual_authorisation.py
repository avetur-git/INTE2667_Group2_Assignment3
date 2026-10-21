import pytest

from app import db
from app.models import Candidate, ApprovalRequest, AuditLog
from app.security.webauthn_mfa import issue_enrolment_code
from app.models import User
from app.seed import seed_demo, DEMO_PASSWORD
from tests.virtual_authenticator import VirtualAuthenticator
from app.utils import constants as C
from tests.test_api_flow import login


def open_and_close_election(client, election_id):
    admin = login(client, 'admin')
    assert client.put(f'/api/elections/{election_id}/status', json={'status': 'open'}, headers=admin).status_code == 200
    assert client.put(f'/api/elections/{election_id}/status', json={'status': 'closed'}, headers=admin).status_code == 200


def first_candidate_id():
    return Candidate.query.first().id


def test_exclusion_creates_pending_request_not_immediate_exclusion(client, app):
    eid = seed_demo(election_status=C.ELECTION_DRAFT)['election_id']
    candidate_id = first_candidate_id()
    open_and_close_election(client, eid)

    response = client.post(f'/api/candidates/{candidate_id}/exclude', json={'reason': 'Court order 123'}, headers=login(client, 'delegate'))

    assert response.status_code == 200
    req = ApprovalRequest.query.order_by(ApprovalRequest.id.desc()).first()
    assert req is not None
    assert req.status == 'pending'
    db.session.expire_all()
    assert db.session.get(Candidate, candidate_id).is_excluded is False


def test_candidate_unchanged_while_request_pending(client, app):
    eid = seed_demo(election_status=C.ELECTION_DRAFT)['election_id']
    candidate_id = first_candidate_id()
    open_and_close_election(client, eid)

    response = client.post(f'/api/candidates/{candidate_id}/exclude', json={'reason': 'Court order 456'}, headers=login(client, 'delegate'))
    assert response.status_code == 200
    req = ApprovalRequest.query.order_by(ApprovalRequest.id.desc()).first()
    assert req.status == 'pending'
    candidate = db.session.get(Candidate, candidate_id)
    assert candidate.is_excluded is False
    assert candidate.excluded_reason is None


def test_second_delegate_approval_executes_exclusion(client, app):
    eid = seed_demo(election_status=C.ELECTION_DRAFT)['election_id']
    candidate_id = first_candidate_id()
    open_and_close_election(client, eid)

    create = client.post(f'/api/candidates/{candidate_id}/exclude', json={'reason': 'Court order 789'}, headers=login(client, 'delegate'))
    request_id = create.get_json()['request_id']

    approval = client.post(f'/api/approval-requests/{request_id}/approve', headers=login(client, 'delegate2'))
    assert approval.status_code == 200
    req = db.session.get(ApprovalRequest, request_id)
    assert req.status == 'approved'
    db.session.expire_all()
    candidate = db.session.get(Candidate, candidate_id)
    assert candidate.is_excluded is True
    assert candidate.excluded_reason == 'Court order 789'


def test_requester_cannot_approve_own_request(client, app):
    eid = seed_demo(election_status=C.ELECTION_DRAFT)['election_id']
    candidate_id = first_candidate_id()
    open_and_close_election(client, eid)

    created = client.post(f'/api/candidates/{candidate_id}/exclude', json={'reason': 'Court order 101'}, headers=login(client, 'delegate'))
    request_id = created.get_json()['request_id']

    response = client.post(f'/api/approval-requests/{request_id}/approve', json={}, headers=login(client, 'delegate'))
    assert response.status_code == 403
    assert db.session.get(ApprovalRequest, request_id).status == 'pending'


def test_approval_requires_mfa_check(client, app):
    eid = seed_demo(election_status=C.ELECTION_DRAFT)['election_id']
    candidate_id = first_candidate_id()
    open_and_close_election(client, eid)

    created = client.post(f'/api/candidates/{candidate_id}/exclude', json={'reason': 'Court order 102'}, headers=login(client, 'delegate'))
    request_id = created.get_json()['request_id']

    # REQ-10: delegate2 logged in without a security key (MFA was off), so once
    # MFA is enforced their session cannot approve
    delegate2 = login(client, 'delegate2')
    app.config['MFA_REQUIRED'] = True
    response = client.post(f'/api/approval-requests/{request_id}/approve', json={}, headers=delegate2)
    assert response.status_code == 401
    assert db.session.get(ApprovalRequest, request_id).status == 'pending'


def test_failed_mfa_leaves_request_pending(client, app):
    eid = seed_demo(election_status=C.ELECTION_DRAFT)['election_id']
    candidate_id = first_candidate_id()
    open_and_close_election(client, eid)

    created = client.post(f'/api/candidates/{candidate_id}/exclude', json={'reason': 'Court order 103'}, headers=login(client, 'delegate'))
    request_id = created.get_json()['request_id']
    delegate2 = login(client, 'delegate2')
    app.config['MFA_REQUIRED'] = True
    resp = client.post(f'/api/approval-requests/{request_id}/approve', json={}, headers=delegate2)
    assert resp.status_code == 401
    assert db.session.get(ApprovalRequest, request_id).status == 'pending'
    assert db.session.get(Candidate, candidate_id).is_excluded is False


def _login_with_security_key(client, username, key):
    """REQ-10 login: password, then the key signs the server's challenge."""
    creds = {'username': username, 'password': DEMO_PASSWORD}
    begin = client.post('/api/auth/webauthn/login/begin', json=creds).get_json()
    r = client.post('/api/auth/login', json={**creds, 'webauthn': key.get(begin['options'])})
    assert r.status_code == 200, r.get_json()
    return {'Authorization': f"Bearer {r.get_json()['access_token']}"}


def test_approval_succeeds_after_security_key_login(client, app):
    eid = seed_demo(election_status=C.ELECTION_DRAFT)['election_id']
    candidate_id = first_candidate_id()
    open_and_close_election(client, eid)
    created = client.post(f'/api/candidates/{candidate_id}/exclude', json={'reason': 'Court order 104'}, headers=login(client, 'delegate'))
    request_id = created.get_json()['request_id']

    # delegate2 registers a key, then MFA is switched on
    key = VirtualAuthenticator()
    delegate2_user = User.query.filter_by(username='delegate2').one()
    code = issue_enrolment_code(delegate2_user)
    creds = {'username': 'delegate2', 'password': DEMO_PASSWORD, 'enrolment_code': code}
    options = client.post('/api/auth/webauthn/register/begin', json=creds).get_json()['options']
    assert client.post('/api/auth/webauthn/register/finish', json={**creds, 'credential': key.create(options)}).status_code == 201
    app.config['MFA_REQUIRED'] = True

    response = client.post(f'/api/approval-requests/{request_id}/approve', json={},
                           headers=_login_with_security_key(client, 'delegate2', key))
    assert response.status_code == 200
    db.session.expire_all()
    assert db.session.get(ApprovalRequest, request_id).status == 'approved'
    assert db.session.get(Candidate, candidate_id).is_excluded is True
    login_entry = AuditLog.query.filter_by(action='PRIVILEGED_LOGIN', username='delegate2').order_by(AuditLog.id.desc()).first()
    assert login_entry.details == {'mfa': True}


def test_mfa_failure_on_approval_is_audited(client, app):
    eid = seed_demo(election_status=C.ELECTION_DRAFT)['election_id']
    candidate_id = first_candidate_id()
    open_and_close_election(client, eid)
    created = client.post(f'/api/candidates/{candidate_id}/exclude', json={'reason': 'Court order 105'}, headers=login(client, 'delegate'))
    request_id = created.get_json()['request_id']
    delegate2 = login(client, 'delegate2')
    app.config['MFA_REQUIRED'] = True
    client.post(f'/api/approval-requests/{request_id}/approve', json={}, headers=delegate2)
    entry = AuditLog.query.filter_by(action='APPROVAL_REQUEST_MFA_FAILED').one()
    assert entry.success is False and entry.details['reason'] == 'session_not_mfa_verified'


def test_non_delegate_cannot_request_or_approve(client, app):
    eid = seed_demo(election_status=C.ELECTION_DRAFT)['election_id']
    candidate_id = first_candidate_id()
    open_and_close_election(client, eid)

    request_response = client.post(f'/api/candidates/{candidate_id}/exclude', json={'reason': 'Court order 104'}, headers=login(client, 'admin'))
    assert request_response.status_code == 403

    create = client.post(f'/api/candidates/{candidate_id}/exclude', json={'reason': 'Court order 105'}, headers=login(client, 'delegate'))
    request_id = create.get_json()['request_id']
    approve_response = client.post(f'/api/approval-requests/{request_id}/approve', json={}, headers=login(client, 'admin'))
    assert approve_response.status_code == 403


def test_rejected_request_does_not_run_action(client, app):
    eid = seed_demo(election_status=C.ELECTION_DRAFT)['election_id']
    candidate_id = first_candidate_id()
    open_and_close_election(client, eid)

    created = client.post(f'/api/candidates/{candidate_id}/exclude', json={'reason': 'Court order 106'}, headers=login(client, 'delegate'))
    request_id = created.get_json()['request_id']
    rejected = client.post(f'/api/approval-requests/{request_id}/reject', json={'reason': 'No legal basis'}, headers=login(client, 'delegate2'))
    assert rejected.status_code == 200
    assert db.session.get(ApprovalRequest, request_id).status == 'rejected'
    db.session.expire_all()
    assert db.session.get(Candidate, candidate_id).is_excluded is False


def test_expired_request_cannot_be_approved(client, app):
    eid = seed_demo(election_status=C.ELECTION_DRAFT)['election_id']
    candidate_id = first_candidate_id()
    open_and_close_election(client, eid)

    created = client.post(f'/api/candidates/{candidate_id}/exclude', json={'reason': 'Court order 107'}, headers=login(client, 'delegate'))
    request_id = created.get_json()['request_id']
    req = db.session.get(ApprovalRequest, request_id)
    req.expires_at = req.requested_at
    db.session.commit()

    response = client.post(f'/api/approval-requests/{request_id}/approve', json={}, headers=login(client, 'delegate2'))
    assert response.status_code == 409
    assert db.session.get(ApprovalRequest, request_id).status == 'expired'


def test_request_can_only_be_decided_once(client, app):
    eid = seed_demo(election_status=C.ELECTION_DRAFT)['election_id']
    candidate_id = first_candidate_id()
    open_and_close_election(client, eid)

    created = client.post(f'/api/candidates/{candidate_id}/exclude', json={'reason': 'Court order 108'}, headers=login(client, 'delegate'))
    request_id = created.get_json()['request_id']
    first = client.post(f'/api/approval-requests/{request_id}/approve', json={}, headers=login(client, 'delegate2'))
    assert first.status_code == 200
    second = client.post(f'/api/approval-requests/{request_id}/approve', json={}, headers=login(client, 'delegate2'))
    assert second.status_code == 409


def test_reason_is_required_and_stored(client, app):
    eid = seed_demo(election_status=C.ELECTION_DRAFT)['election_id']
    candidate_id = first_candidate_id()
    open_and_close_election(client, eid)

    response = client.post(f'/api/candidates/{candidate_id}/exclude', json={}, headers=login(client, 'delegate'))
    assert response.status_code == 400

    created = client.post(f'/api/candidates/{candidate_id}/exclude', json={'reason': 'Court order 109'}, headers=login(client, 'delegate'))
    request_id = created.get_json()['request_id']
    req = db.session.get(ApprovalRequest, request_id)
    assert req.reason == 'Court order 109'


def test_each_step_written_to_audit_log(client, app):
    eid = seed_demo(election_status=C.ELECTION_DRAFT)['election_id']
    candidate_id = first_candidate_id()
    open_and_close_election(client, eid)

    delegate = login(client, 'delegate')
    created = client.post(f'/api/candidates/{candidate_id}/exclude', json={'reason': 'Court order 110'}, headers=delegate)
    request_id = created.get_json()['request_id']
    client.post(f'/api/approval-requests/{request_id}/reject', json={'reason': 'No basis'}, headers=login(client, 'delegate2'))

    actions = [log.action for log in AuditLog.query.filter_by(object_type='approval_request', object_id=str(request_id)).order_by(AuditLog.id).all()]
    assert 'APPROVAL_REQUEST_CREATED' in actions
    assert 'APPROVAL_REQUEST_REJECTED' in actions
