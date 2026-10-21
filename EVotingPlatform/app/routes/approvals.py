from datetime import timedelta

from flask import Blueprint, jsonify, request

from app import db
from app.models.approval_request import ApprovalRequest
from app.models.candidate import Candidate
from app.models.user import User
from app.security.hooks import hooks
from app.utils import constants as C
from app.utils.helpers import validate_required_fields, utcnow
from app.security.rbac import P, permission_required
from app.security.audit_trail import audit, audited

approvals_bp = Blueprint('approvals', __name__)


def _deny_not_delegate(current_user):
    return jsonify({'error': "Only Commissioner's delegates can manage approval requests"}), C.HTTP_FORBIDDEN


def _expire_request(request_obj, user=None):
    """Mark a pending request expired. Returns the audit details, or None if it
    was not pending. The caller records the expiry through `audit`."""
    if request_obj.status != 'pending':
        return None
    request_obj.status = 'expired'
    request_obj.decided_at = utcnow()
    request_obj.rejected_by = user.id if user else request_obj.rejected_by
    return {'reason': 'expired',
            'expired_at': request_obj.expires_at.isoformat() if request_obj.expires_at else None}


def _list_requests():
    """Shared by both list routes. Expires overdue pending requests on the way."""
    q = ApprovalRequest.query.order_by(ApprovalRequest.requested_at.desc())
    if request.args.get('status'):
        q = q.filter_by(status=request.args['status'])
        audit.details['status'] = request.args['status']
    for req in q.all():
        if req.status == 'pending' and req.expires_at <= utcnow():
            audit.also('APPROVAL_REQUEST_EXPIRED', 'approval_request', req.id, _expire_request(req))
    db.session.commit()
    q = ApprovalRequest.query.order_by(ApprovalRequest.requested_at.desc())
    if request.args.get('status'):
        q = q.filter_by(status=request.args['status'])
    return jsonify({'requests': [r.to_dict() for r in q.all()]}), C.HTTP_OK


@approvals_bp.route('/approval-requests', methods=['GET'])
@permission_required(P.CANDIDATE_HISTORY_READ)
@audited('APPROVAL_REQUESTS_VIEWED', object_type='approval_request')
def list_approval_requests(current_user):
    return _list_requests()


@approvals_bp.route('/approval-requests', methods=['POST'])
@permission_required(P.CANDIDATE_EXCLUDE)
@audited('APPROVAL_REQUEST_CREATED', object_type='approval_request')
def create_approval_request(current_user):
    data = request.get_json(silent=True) or {}
    action = data.get('action') or 'EXCLUDE_CANDIDATE'
    if action not in C.SENSITIVE_ACTIONS:
        return jsonify({'error': 'Unsupported sensitive action'}), C.HTTP_BAD_REQUEST
    if action == 'EXCLUDE_CANDIDATE':
        candidate_id = data.get('target_id') or data.get('candidate_id')
        if candidate_id is None:
            return jsonify({'error': 'target_id is required for candidate exclusion'}), C.HTTP_BAD_REQUEST
        cand = Candidate.query.get(candidate_id)
        if not cand:
            return jsonify({'error': C.ERROR_CANDIDATE_NOT_FOUND}), C.HTTP_NOT_FOUND
        target = f'candidate:{cand.id}'
        target_id = str(cand.id)
        title = f'Candidate {cand.name}'
    else:
        target = data.get('target') or 'unknown-target'
        target_id = str(data.get('target_id', data.get('id', 'unknown')))
        title = target

    ok, error = validate_required_fields(data, ['reason'])
    if not ok:
        return jsonify({'error': 'A reason is required'}), C.HTTP_BAD_REQUEST

    if current_user.role != C.ROLE_DELEGATE:
        return _deny_not_delegate(current_user)

    allowed, message = hooks.run('authorise_action', action, current_user, target, data)
    if not allowed:
        audit.action = 'APPROVAL_REQUEST_REFUSED'
        # No request was created, so the affected object is the target itself
        audit.target(target_id, object_type='candidate' if action == 'EXCLUDE_CANDIDATE'
                     else (data.get('target_type') or 'generic'))
        audit.details.update({'action': action, 'message': message})
        return jsonify({'error': message or 'Authorisation required'}), C.HTTP_FORBIDDEN

    req = ApprovalRequest(
        action=action,
        target_type='candidate' if action == 'EXCLUDE_CANDIDATE' else (data.get('target_type') or 'generic'),
        target_id=target_id,
        target=title,
        reason=data['reason'][:255],
        requested_by=current_user.id,
        expires_at=utcnow() + timedelta(hours=C.APPROVAL_REQUEST_TIMEOUT_HOURS),
    )
    db.session.add(req)
    db.session.commit()
    audit.target(req.id)
    audit.details.update({'action': action, 'target': target, 'reason': req.reason,
                          'expires_at': req.expires_at.isoformat() if req.expires_at else None})
    return jsonify({'message': 'Sensitive action submitted for approval', 'request_id': req.id, 'status': req.status}), C.HTTP_OK


@approvals_bp.route('/approval-requests/<int:request_id>/approve', methods=['POST'])
@permission_required(P.CANDIDATE_EXCLUDE)
@audited('APPROVAL_REQUEST_APPROVED', object_type='approval_request', id_arg='request_id')
def approve_approval_request(current_user, request_id):
    if current_user.role != C.ROLE_DELEGATE:
        return _deny_not_delegate(current_user)

    req = ApprovalRequest.query.get(request_id)
    if not req:
        return jsonify({'error': 'Approval request not found'}), C.HTTP_NOT_FOUND
    if req.requested_by == current_user.id:
        audit.action = 'APPROVAL_REQUEST_REFUSED'
        audit.details['reason'] = 'requester_cannot_approve_own_request'
        return jsonify({'error': 'A requester cannot approve their own request'}), C.HTTP_FORBIDDEN
    if req.status != 'pending':
        return jsonify({'error': 'This request has already been decided'}), C.HTTP_CONFLICT
    if req.expires_at <= utcnow():
        audit.action = 'APPROVAL_REQUEST_EXPIRED'
        audit.details.update(_expire_request(req, current_user))
        db.session.commit()
        return jsonify({'error': 'This approval request has expired'}), C.HTTP_CONFLICT

    # REQ-10: placeholder MFA gate. Replace this implementation with the real FIDO2
    # hardware-key check when the MFA workstream is implemented. This fake version
    # is intentionally permissive so the approval workflow can be tested before the
    # real second-factor mechanism lands.
    if not hooks.run('verify_mfa', current_user, {'action': 'approve_approval_request', 'request_id': request_id}):
        audit.action = 'APPROVAL_REQUEST_MFA_FAILED'
        audit.details['request_id'] = req.id
        return jsonify({'error': 'MFA verification failed; request remains pending'}), C.HTTP_UNAUTHORIZED

    if req.action == 'EXCLUDE_CANDIDATE':
        cand = Candidate.query.get(req.target_id)
        if not cand:
            audit.action = 'APPROVAL_REQUEST_EXECUTION_FAILED'
            audit.details['target_missing'] = True
            return jsonify({'error': C.ERROR_CANDIDATE_NOT_FOUND}), C.HTTP_NOT_FOUND

        original_reason = cand.excluded_reason
        original_flag = cand.is_excluded
        cand.is_excluded = True
        cand.excluded_reason = req.reason[:255]
        req.status = 'approved'
        req.approved_by = current_user.id
        req.decided_at = utcnow()
        db.session.add(req)
        db.session.flush()
        # One request, three records: the approval, its execution, and the
        # candidate it excluded (REQ-17 reads the candidate's own trail)
        audit.details.update({'target': req.target, 'reason': req.reason, 'approved_by': current_user.id})
        audit.also('APPROVAL_REQUEST_EXECUTED', 'approval_request', req.id,
                   {'target': req.target, 'status': 'approved', 'action': req.action})
        audit.also('CANDIDATE_EXCLUDED', 'candidate', cand.id, {'reason': cand.excluded_reason})
        from app.services.candidate_history import record_change
        record_change(cand, 'excluded', {'is_excluded': {'old': original_flag, 'new': True},
                                        'excluded_reason': {'old': original_reason, 'new': cand.excluded_reason}}, current_user)
    else:
        req.status = 'approved'
        req.approved_by = current_user.id
        req.decided_at = utcnow()
        db.session.add(req)
        audit.details.update({'target': req.target, 'reason': req.reason, 'approved_by': current_user.id})
        audit.also('APPROVAL_REQUEST_EXECUTED', 'approval_request', req.id,
                   {'target': req.target, 'status': 'approved', 'action': req.action})

    db.session.commit()
    return jsonify({'message': 'Approval granted and sensitive action completed', 'request_id': req.id, 'status': req.status}), C.HTTP_OK


@approvals_bp.route('/approval-requests/<int:request_id>/reject', methods=['POST'])
@permission_required(P.CANDIDATE_EXCLUDE)
@audited('APPROVAL_REQUEST_REJECTED', object_type='approval_request', id_arg='request_id')
def reject_approval_request(current_user, request_id):
    if current_user.role != C.ROLE_DELEGATE:
        return _deny_not_delegate(current_user)

    req = ApprovalRequest.query.get(request_id)
    if not req:
        return jsonify({'error': 'Approval request not found'}), C.HTTP_NOT_FOUND
    if req.requested_by == current_user.id:
        return jsonify({'error': 'A requester cannot reject their own request'}), C.HTTP_FORBIDDEN
    if req.status != 'pending':
        return jsonify({'error': 'This request has already been decided'}), C.HTTP_CONFLICT
    if req.expires_at <= utcnow():
        audit.action = 'APPROVAL_REQUEST_EXPIRED'
        audit.details.update(_expire_request(req, current_user))
        db.session.commit()
        return jsonify({'error': 'This approval request has expired'}), C.HTTP_CONFLICT

    data = request.get_json(silent=True) or {}
    if not data.get('reason'):
        data['reason'] = 'Rejected by second delegate'
    req.status = 'rejected'
    req.rejected_by = current_user.id
    req.decided_at = utcnow()
    db.session.add(req)
    db.session.commit()
    audit.details.update({'reason': data['reason'][:255], 'rejected_by': current_user.id})
    return jsonify({'message': 'Approval request rejected', 'request_id': req.id, 'status': req.status}), C.HTTP_OK


@approvals_bp.route('/approval-requests/<int:request_id>', methods=['GET'])
@permission_required(P.CANDIDATE_HISTORY_READ)
@audited('APPROVAL_REQUEST_VIEWED', object_type='approval_request', id_arg='request_id')
def get_approval_request(current_user, request_id):
    req = ApprovalRequest.query.get(request_id)
    if not req:
        return jsonify({'error': 'Approval request not found'}), C.HTTP_NOT_FOUND
    return jsonify({'request': req.to_dict()}), C.HTTP_OK


@approvals_bp.route('/approvals', methods=['GET'])
@permission_required(P.CANDIDATE_HISTORY_READ)
@audited('APPROVAL_REQUESTS_VIEWED', object_type='approval_request')
def approvals_alias(current_user):
    return _list_requests()
