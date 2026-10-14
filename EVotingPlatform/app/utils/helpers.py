import uuid
import secrets
from datetime import datetime, timezone
from functools import wraps
from flask import jsonify, request
from flask_jwt_extended import jwt_required, get_jwt_identity, get_jwt
from app.utils import constants as C


def utcnow():
    """Naive UTC timestamp (keeps SQLite happy, avoids deprecated utcnow())."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def generate_uuid():
    return str(uuid.uuid4())


def generate_receipt_code():
    """Random code with no link to the voter or the vote (REQ-20 replaces this)."""
    return secrets.token_hex(8).upper()


def validate_required_fields(data, required_fields):
    """Check that all required fields are present in the JSON body."""
    if not isinstance(data, dict):
        return False, 'Request body must be JSON'
    missing = [f for f in required_fields if f not in data or data[f] in (None, '')]
    if missing:
        return False, f"Missing required fields: {', '.join(missing)}"
    return True, None


def get_request_meta():
    """Who/what/where of the current request. Used by audit logging (REQ-15)
    and by the terminal and location hooks (REQ-03, REQ-19)."""
    session_id = None
    try:
        session_id = get_jwt().get('jti')
    except Exception:
        pass
    try:
        return {
            'ip': request.remote_addr,
            'user_agent': (request.headers.get('User-Agent') or '')[:255],
            'session_id': session_id,
        }
    except RuntimeError:  # outside a request
        return {'ip': None, 'user_agent': None, 'session_id': None}


def user_required(f):
    """Require a logged-in, active user. Injects current_user."""
    @wraps(f)
    @jwt_required()
    def decorated(*args, **kwargs):
        from app.models.user import User   # imported here to avoid a circular import
        user = User.query.get(int(get_jwt_identity()))
        if not user or not user.is_active:
            return jsonify({'error': C.ERROR_USER_INACTIVE}), C.HTTP_UNAUTHORIZED
        return f(current_user=user, *args, **kwargs)
    return decorated


def roles_required(*roles):
    """Require one of the given roles. Injects current_user.

    BASE RBAC (REQ-11): a plain role-name check against users.role. The
    central IAM/RBAC solution replaces the body of this decorator, so route
    code does not need to change."""
    def wrapper(f):
        @wraps(f)
        @jwt_required()
        def decorated(*args, **kwargs):
            from app.models.user import User
            user = User.query.get(int(get_jwt_identity()))
            if not user or not user.is_active:
                return jsonify({'error': C.ERROR_USER_INACTIVE}), C.HTTP_UNAUTHORIZED
            if user.role not in roles:
                return jsonify({'error': C.ERROR_ACCESS_DENIED}), C.HTTP_FORBIDDEN
            return f(current_user=user, *args, **kwargs)
        return decorated
    return wrapper
