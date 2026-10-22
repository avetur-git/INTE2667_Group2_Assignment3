from datetime import date
from flask import Blueprint, request, jsonify
from app import db
from app.models.election import Division, Election
from app.models.user import User
from app.models.voter import Voter
from app.services.audit import log_action
from app.services.eligibility import check_eligibility
from app.utils import constants as C
from app.utils.helpers import validate_required_fields
from app.utils.validation import (check_date_of_birth, check_password, clean_email, clean_postcode,
                                  clean_text, clean_username)
from app.security.rbac import P, permission_required
from app.security.audit_trail import audit, audited

voters_bp = Blueprint('voters', __name__)

VOTER_FIELDS = ['first_name', 'last_name', 'date_of_birth', 'address_line',
                'suburb', 'state', 'postcode', 'division_id']


def _parse_voter_fields(data):
    """Validate and normalise enrolment details. Returns (clean_dict, error)."""
    ok, error = validate_required_fields(data, VOTER_FIELDS)
    if not ok:
        return None, error
    try:
        dob = date.fromisoformat(str(data['date_of_birth']))
    except ValueError:
        return None, 'date_of_birth must be YYYY-MM-DD'
    dob, error = check_date_of_birth(dob)
    if error:
        return None, error
    text = {}
    for field, label, max_len in (('first_name', 'First name', 80), ('last_name', 'Last name', 80),
                                  ('address_line', 'Address', 200), ('suburb', 'Suburb', 100)):
        text[field], error = clean_text(data[field], label, max_len)
        if error:
            return None, error
    postcode, error = clean_postcode(data['postcode'])
    if error:
        return None, error
    state = str(data['state']).strip().upper()
    if state not in C.STATES:
        return None, f'state must be one of {", ".join(C.STATES)}'
    division = Division.query.get(data['division_id'])
    if not division:
        return None, 'Unknown division'
    if division.state != state:
        return None, 'Division is not in the given state'
    return {
        **text, 'date_of_birth': dob, 'state': state,
        'postcode': postcode, 'division_id': division.id,
        'is_citizen': bool(data.get('is_citizen', True)),
        'is_overseas': bool(data.get('is_overseas', False)),
    }, None


# ----------------------------- the voter themselves --------------------------

@voters_bp.route('/me', methods=['GET'])
@permission_required(P.OWN_ENROLMENT_READ)
def my_enrolment(current_user):
    """Check my own enrolment status."""
    voter = current_user.voter
    if not voter:
        return jsonify({'enrolled': False, 'status': 'not_enrolled'}), C.HTTP_OK
    return jsonify({'enrolled': voter.enrolment_status == C.ENROLMENT_ENROLLED,
                    'status': voter.enrolment_status, 'voter': voter.to_dict()}), C.HTTP_OK


@voters_bp.route('/enrol', methods=['POST'])
@permission_required(P.OWN_ENROLMENT_WRITE)
def self_enrol(current_user):
    """A citizen enrols themselves. The record is 'pending' until an AEC officer approves it."""
    if current_user.voter:
        return jsonify({'error': 'You already have an enrolment record'}), C.HTTP_CONFLICT
    clean, error = _parse_voter_fields(request.get_json(silent=True))
    if error:
        return jsonify({'error': error}), C.HTTP_BAD_REQUEST
    voter = Voter(user_id=current_user.id, enrolment_status=C.ENROLMENT_PENDING,
                  registered_via='self', **clean)
    db.session.add(voter)
    db.session.commit()
    return jsonify({'message': 'Enrolment submitted for approval', 'voter': voter.to_dict()}), C.HTTP_CREATED


@voters_bp.route('/me/address', methods=['PUT'])
@permission_required(P.OWN_ENROLMENT_WRITE)
def update_address(current_user):
    """An enrolled voter updates their address (and division)."""
    voter = current_user.voter
    if not voter or voter.enrolment_status != C.ENROLMENT_ENROLLED:
        return jsonify({'error': C.ERROR_NOT_ENROLLED}), C.HTTP_FORBIDDEN
    data = request.get_json(silent=True) or {}
    merged = {f: data.get(f, getattr(voter, f)) for f in VOTER_FIELDS}
    merged['date_of_birth'] = voter.date_of_birth.isoformat()
    clean, error = _parse_voter_fields(merged)
    if error:
        return jsonify({'error': error}), C.HTTP_BAD_REQUEST
    for f in ('address_line', 'suburb', 'state', 'postcode', 'division_id'):
        setattr(voter, f, clean[f])
    db.session.commit()
    log_action('VOTER_ADDRESS_UPDATED', user=current_user, object_type='voter', object_id=voter.id)
    return jsonify({'message': 'Address updated', 'voter': voter.to_dict()}), C.HTTP_OK


@voters_bp.route('/eligibility', methods=['GET'])
@permission_required(P.OWN_ENROLMENT_READ)
def my_eligibility(current_user):
    """Before voting: am I eligible to vote in this election?"""
    election = Election.query.get(request.args.get('election_id', type=int))
    if not election:
        return jsonify({'error': C.ERROR_ELECTION_NOT_FOUND}), C.HTTP_NOT_FOUND
    eligible, code, message = check_eligibility(current_user.voter, election)
    return jsonify({'eligible': eligible, 'reason_code': code, 'message': message}), C.HTTP_OK


# ----------------------------- AEC officers ---------------------------------

@voters_bp.route('/', methods=['POST'])
@permission_required(P.VOTER_REGISTER)
@audited('VOTER_REGISTERED', object_type='voter')
def register_citizen(current_user):
    """An AEC employee registers a citizen. Optionally creates their login too."""
    data = request.get_json(silent=True) or {}
    clean, error = _parse_voter_fields(data)
    if error:
        return jsonify({'error': error}), C.HTTP_BAD_REQUEST

    user = None
    if data.get('username'):
        ok, error = validate_required_fields(data, ['username', 'email', 'password'])
        if not ok:
            return jsonify({'error': error}), C.HTTP_BAD_REQUEST
        username, error = clean_username(data['username'])
        email, email_error = clean_email(data['email'])
        password, password_error = check_password(data['password'], username)
        error = error or email_error or password_error
        if error:
            return jsonify({'error': error}), C.HTTP_BAD_REQUEST
        if User.query.filter((User.username == username) | (User.email == email)).first():
            return jsonify({'error': 'Username or email already exists'}), C.HTTP_CONFLICT
        user = User(username, email, password, C.ROLE_VOTER)
        db.session.add(user)
        db.session.flush()

    voter = Voter(user_id=user.id if user else None, enrolment_status=C.ENROLMENT_ENROLLED,
                  registered_via='aec_officer', registered_by=current_user.id, **clean)
    db.session.add(voter)
    db.session.commit()
    audit.target(voter.id)
    return jsonify({'message': 'Citizen registered', 'voter': voter.to_dict()}), C.HTTP_CREATED


@voters_bp.route('/', methods=['GET'])
@permission_required(P.VOTER_READ)
@audited('VOTER_ROLL_VIEWED', object_type='voter')
def list_voters(current_user):
    q = Voter.query
    status = request.args.get('status')
    if status:
        q = q.filter_by(enrolment_status=status)
    name = request.args.get('q')
    if name:
        like = f'%{name}%'
        q = q.filter(db.or_(Voter.first_name.ilike(like), Voter.last_name.ilike(like)))
    # Record what was searched, but not the name itself (it is personal data)
    audit.details.update({'status': status, 'name_search': bool(name)})
    voters = q.order_by(Voter.id.desc()).limit(100).all()
    return jsonify({'voters': [v.to_dict() for v in voters], 'total': len(voters)}), C.HTTP_OK


def _set_status(voter_id, status):
    voter = Voter.query.get(voter_id)
    if not voter:
        return jsonify({'error': C.ERROR_VOTER_NOT_FOUND}), C.HTTP_NOT_FOUND
    voter.enrolment_status = status
    db.session.commit()
    return jsonify({'message': f'Voter {status}', 'voter': voter.to_dict()}), C.HTTP_OK


@voters_bp.route('/<int:voter_id>/approve', methods=['PUT'])
@permission_required(P.VOTER_STATUS_CHANGE)
@audited('VOTER_APPROVED', object_type='voter')
def approve_voter(current_user, voter_id):
    return _set_status(voter_id, C.ENROLMENT_ENROLLED)


@voters_bp.route('/<int:voter_id>/remove', methods=['PUT'])
@permission_required(P.VOTER_STATUS_CHANGE)
@audited('VOTER_REMOVED', object_type='voter')
def remove_voter(current_user, voter_id):
    return _set_status(voter_id, C.ENROLMENT_REMOVED)
