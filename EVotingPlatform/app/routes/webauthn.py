"""REQ-10: WebAuthn endpoints for security-key registration and the first step
of a privileged login. The second step is the normal POST /api/auth/login with
the signed assertion in a `webauthn` field, checked by the `verify_mfa` hook."""
from flask import Blueprint, current_app, jsonify, request

from app import db
from app.models.user import User
from app.security import webauthn_mfa as mfa
from app.services.audit import log_action
from app.utils import constants as C
from app.utils.helpers import utcnow, validate_required_fields

webauthn_bp = Blueprint('webauthn', __name__)


def _check_password(data, extra_fields=()):
    """Return (user, None) if the username and password are valid, else (None, response)."""
    ok, error = validate_required_fields(data, ['username', 'password', *extra_fields])
    if not ok:
        return None, (jsonify({'error': error}), C.HTTP_BAD_REQUEST)
    user = User.query.filter_by(username=data['username'].strip()).first()
    if not user or not user.check_password(data['password']):
        log_action('LOGIN_FAILED', details={'username': data['username'][:80]}, success=False)
        return None, (jsonify({'error': C.ERROR_INVALID_CREDENTIALS}), C.HTTP_UNAUTHORIZED)
    if not user.is_active:
        return None, (jsonify({'error': 'Account is disabled'}), C.HTTP_UNAUTHORIZED)
    return user, None


def _enrolment(data):
    """Password plus a valid enrolment code, for a privileged user only."""
    user, error = _check_password(data, ['enrolment_code'])
    if error:
        return None, None, error
    if not user.is_privileged:
        return None, None, (jsonify({'error': 'Security keys are only for staff accounts'}),
                            C.HTTP_FORBIDDEN)
    try:
        code = mfa.find_enrolment_code(user, data['enrolment_code'])
    except mfa.MFAError as exc:
        log_action('WEBAUTHN_ENROL_FAILED', user=user, details={'reason': str(exc)}, success=False)
        return None, None, (jsonify({'error': str(exc)}), C.HTTP_UNAUTHORIZED)
    return user, code, None


@webauthn_bp.route('/register/begin', methods=['POST'])
def register_begin():
    user, _, error = _enrolment(request.get_json(silent=True))
    if error:
        return error
    return jsonify({'options': mfa.registration_options(user)}), C.HTTP_OK


@webauthn_bp.route('/register/finish', methods=['POST'])
def register_finish():
    data = request.get_json(silent=True)
    user, code, error = _enrolment(data)
    if error:
        return error
    try:
        cred = mfa.register_credential(user, data.get('credential'))
    except mfa.MFAError as exc:
        log_action('WEBAUTHN_ENROL_FAILED', user=user, details={'reason': str(exc)}, success=False)
        return jsonify({'error': str(exc)}), C.HTTP_BAD_REQUEST
    code.used_at = utcnow()
    db.session.commit()
    log_action('WEBAUTHN_KEY_REGISTERED', user=user, object_type='webauthn_credential',
               object_id=cred.id)
    return jsonify({'message': 'Security key registered'}), C.HTTP_CREATED


@webauthn_bp.route('/login/begin', methods=['POST'])
def login_begin():
    """Step 1 of a login: check the password and, for staff when MFA is
    enforced, return a one-time challenge for the security key to sign."""
    user, error = _check_password(request.get_json(silent=True))
    if error:
        return error
    if not (user.is_privileged and current_app.config.get('MFA_REQUIRED')):
        return jsonify({'mfa_required': False}), C.HTTP_OK
    if not mfa.has_credentials(user):
        return jsonify({'error': 'No security key registered. Ask an administrator for an '
                                 'enrolment code and register a key first.',
                        'enrolment_required': True}), C.HTTP_FORBIDDEN
    return jsonify({'mfa_required': True,
                    'options': mfa.authentication_options(user)}), C.HTTP_OK
