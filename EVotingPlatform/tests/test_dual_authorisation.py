import pytest

from app import db
from app.models import Candidate, ApprovalRequest, AuditLog
from app.security.hooks import hooks
from app.seed import seed_demo
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

    delegate2 = login(client, 'delegate2')
    original = hooks.get('verify_mfa')
    hooks.register('verify_mfa', lambda user, request_data: False)
    try:
        response = client.post(f'/api/approval-requests/{request_id}/approve', json={}, headers=delegate2)
        assert response.status_code == 401
        assert db.session.get(ApprovalRequest, request_id).status == 'pending'
    finally:
        hooks.register('verify_mfa', original)


def test_failed_mfa_leaves_request_pending(client, app):
    eid = seed_demo(election_status=C.ELECTION_DRAFT)['election_id']
    candidate_id = first_candidate_id()
    open_and_close_election(client, eid)

    created = client.post(f'/api/candidates/{candidate_id}/exclude', json={'reason': 'Court order 103'}, headers=login(client, 'delegate'))
    request_id = created.get_json()['request_id']
    delegate2 = login(client, 'delegate2')
    original = hooks.get('verify_mfa')
    hooks.register('verify_mfa', lambda user, request_data: False)
    try:
        resp = client.post(f'/api/approval-requests/{request_id}/approve', json={}, headers=delegate2)
        assert resp.status_code == 401
    finally:
        hooks.register('verify_mfa', original)
    assert db.session.get(ApprovalRequest, request_id).status == 'pending'
    assert db.session.get(Candidate, candidate_id).is_excluded is False


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
