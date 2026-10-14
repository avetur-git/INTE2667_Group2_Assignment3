from app.security.hooks import hooks
from app.utils.helpers import get_request_meta


def log_action(action, user=None, object_type=None, object_id=None, details=None, success=True):
    """Record a privileged or security-relevant action (REQ-15 fields).
    Never put vote choices in `details`."""
    meta = get_request_meta()
    entry = {
        'user_id': user.id if user else None,
        'username': user.username if user else None,
        'action': action,
        'object_type': object_type,
        'object_id': str(object_id) if object_id is not None else None,
        'details': details,
        'ip_address': meta.get('ip'),
        'user_agent': meta.get('user_agent'),
        'session_id': meta.get('session_id'),
        'success': success,
    }
    hooks.run('write_audit_record', entry)
