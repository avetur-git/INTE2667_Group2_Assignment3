from flask import Blueprint, request, jsonify
from app import db
from app.models.audit import AuditLog
from app.models.user import User
from app.services.audit import log_action
from app.utils import constants as C
from app.utils.helpers import validate_required_fields, roles_required

admin_bp = Blueprint('admin', __name__)


@admin_bp.route('/users', methods=['POST'])
@roles_required(C.ROLE_ADMIN)
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
    log_action('STAFF_USER_CREATED', user=current_user, object_type='user', object_id=user.id,
               details={'role': user.role})
    return jsonify({'user': user.to_dict()}), C.HTTP_CREATED


@admin_bp.route('/audit-logs', methods=['GET'])
@roles_required(C.ROLE_ADMIN)
def audit_logs(current_user):
    q = AuditLog.query
    if request.args.get('action'):
        q = q.filter_by(action=request.args['action'])
    if request.args.get('username'):
        q = q.filter_by(username=request.args['username'])
    logs = q.order_by(AuditLog.id.desc()).limit(request.args.get('limit', 100, type=int)).all()
    return jsonify({'logs': [l.to_dict() for l in logs], 'total': len(logs)}), C.HTTP_OK
