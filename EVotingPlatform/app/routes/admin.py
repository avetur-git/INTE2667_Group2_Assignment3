from flask import Blueprint, request, jsonify
from app import db
from app.models.audit import AuditLog
from app.models.user import User
from app.utils import constants as C
from app.utils.helpers import validate_required_fields
from app.security.rbac import P, permission_required
from app.security.audit_trail import audit, audited

admin_bp = Blueprint('admin', __name__)


@admin_bp.route('/users', methods=['POST'])
@permission_required(P.STAFF_MANAGE)
@audited('STAFF_USER_CREATED', object_type='user')
def create_staff_user(current_user):
    """Create a staff account (AEC officer, Commissioner's delegate, or admin)."""
    data = request.get_json(silent=True)
    ok, error = validate_required_fields(data, ['username', 'email', 'password', 'role'])
    if not ok:
        return jsonify({'error': error}), 400
    if data['role'] not in C.PRIVILEGED_ROLES:
        return jsonify({'error': f'role must be one of {", ".join(C.PRIVILEGED_ROLES)}'}), 400
    if User.query.filter((User.username == data['username'].strip()) |
                         (User.email == data['email'].strip().lower())).first():
        return jsonify({'error': 'Username or email already exists'}), C.HTTP_CONFLICT
    user = User(data['username'].strip(), data['email'].strip().lower(), data['password'], data['role'])
    db.session.add(user)
    db.session.commit()
    audit.target(user.id)
    audit.details['role'] = user.role
    return jsonify({'user': user.to_dict()}), C.HTTP_CREATED


@admin_bp.route('/audit-logs', methods=['GET'])
@permission_required(P.AUDIT_READ)
@audited('AUDIT_LOG_VIEWED', object_type='audit_log')
def audit_logs(current_user):
    """Newest first. Filters: action, username, success (true/false),
    hide_views (true leaves out *_VIEWED read entries), limit (1-500)."""
    filters = ('action', 'username', 'success', 'hide_views', 'limit')
    audit.details.update({k: request.args[k] for k in filters if k in request.args})
    q = AuditLog.query
    if request.args.get('action'):
        q = q.filter_by(action=request.args['action'])
    if request.args.get('username'):
        q = q.filter_by(username=request.args['username'])
    if request.args.get('success') in ('true', 'false'):
        q = q.filter_by(success=request.args['success'] == 'true')
    if request.args.get('hide_views') == 'true':
        q = q.filter(~AuditLog.action.like('%\\_VIEWED', escape='\\'))
    limit = min(max(request.args.get('limit', 100, type=int), 1), 500)
    logs = q.order_by(AuditLog.id.desc()).limit(limit).all()
    return jsonify({'logs': [l.to_dict() for l in logs], 'total': len(logs)}), C.HTTP_OK
