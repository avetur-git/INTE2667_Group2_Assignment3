from flask import Blueprint, current_app, request, jsonify
from flask_jwt_extended import create_access_token
from app import db
from app.models.user import User
from app.security.hooks import hooks
from app.security.rate_limit import rate_limited
from app.services.audit import log_action
from app.utils import constants as C
from app.utils.helpers import validate_required_fields, user_required
from app.utils.validation import check_password, clean_email, clean_username

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/register', methods=['POST'])
def register():
    """Create a voter login account. (Staff accounts are created by an election admin.)"""
    try:
        data = request.get_json(silent=True)
        ok, error = validate_required_fields(data, ['username', 'email', 'password'])
        if not ok:
            return jsonify({'error': error}), C.HTTP_BAD_REQUEST

        # Same rules as a voter login created by an AEC officer (app/utils/validation.py)
        username, error = clean_username(data['username'])
        email, email_error = clean_email(data['email'])
        password, password_error = check_password(data['password'], username)
        error = error or email_error or password_error
        if error:
            return jsonify({'error': error}), C.HTTP_BAD_REQUEST
        if User.query.filter_by(username=username).first():
            return jsonify({'error': 'Username already exists'}), C.HTTP_CONFLICT
        if User.query.filter_by(email=email).first():
            return jsonify({'error': 'Email already registered'}), C.HTTP_CONFLICT

        user = User(username=username, email=email, password=password, role=C.ROLE_VOTER)
        db.session.add(user)
        db.session.commit()

        token = create_access_token(identity=str(user.id))
        return jsonify({'message': 'Registration successful', 'user': user.to_dict(),
                        'access_token': token}), C.HTTP_CREATED
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Registration failed: {str(e)}'}), 500


@auth_bp.route('/login', methods=['POST'])
@rate_limited('login')
def login():
    try:
        data = request.get_json(silent=True)
        ok, error = validate_required_fields(data, ['username', 'password'])
        if not ok:
            return jsonify({'error': error}), C.HTTP_BAD_REQUEST

        user = User.query.filter_by(username=data['username'].strip()).first()
        if not user or not user.check_password(data['password']):
            log_action('LOGIN_FAILED', details={'username': data['username'][:80]}, success=False)
            return jsonify({'error': C.ERROR_INVALID_CREDENTIALS}), C.HTTP_UNAUTHORIZED
        if not user.is_active:
            return jsonify({'error': 'Account is disabled'}), C.HTTP_UNAUTHORIZED

        # REQ-10: second factor for privileged users plugs in here.
        if user.is_privileged and not hooks.run('verify_mfa', user, data):
            log_action('MFA_FAILED', user=user, success=False)
            return jsonify({'error': 'Second authentication factor failed'}), C.HTTP_UNAUTHORIZED

        # The token records that the security key was checked, so later
        # sensitive steps (REQ-18 approvals) can rely on this login.
        mfa_verified = user.is_privileged and bool(current_app.config.get('MFA_REQUIRED'))
        token = create_access_token(identity=str(user.id),
                                    additional_claims={'mfa': True} if mfa_verified else None)
        if user.is_privileged:
            log_action('PRIVILEGED_LOGIN', user=user, object_type='user', object_id=user.id,
                       details={'mfa': mfa_verified})
        return jsonify({'message': 'Login successful', 'user': user.to_dict(),
                        'access_token': token}), C.HTTP_OK
    except Exception as e:
        return jsonify({'error': f'Login failed: {str(e)}'}), 500


@auth_bp.route('/profile', methods=['GET'])
@user_required
def get_profile(current_user):
    voter = current_user.voter
    return jsonify({'user': current_user.to_dict(),
                    'voter': voter.to_dict() if voter else None}), C.HTTP_OK


@auth_bp.route('/logout', methods=['POST'])
@user_required
def logout(current_user):
    """Client-side token removal in the base build."""
    return jsonify({'message': 'Logout successful'}), C.HTTP_OK
