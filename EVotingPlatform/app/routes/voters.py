from datetime import date
from flask import Blueprint, request, jsonify
from app import db
from app.models.election import Division, Election
from app.models.user import User
from app.models.voter import Voter
from app.services.audit import log_action
from app.services.eligibility import check_eligibility
from app.utils import constants as C
from app.utils.helpers import validate_required_fields, user_required, roles_required

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
    state = str(data['state']).strip().upper()
    if state not in C.STATES:
        return None, f'state must be one of {", ".join(C.STATES)}'
    division = Division.query.get(data['division_id'])
    if not division:
        return None, 'Unknown division'
    if division.state != state:
        return None, 'Division is not in the given state'
    return {
        'first_name': data['first_name'].strip(), 'last_name': data['last_name'].strip(),
        'date_of_birth': dob, 'address_line': data['address_line'].strip(),
        'suburb': data['suburb'].strip(), 'state': state,
        'postcode': str(data['postcode']).strip(), 'division_id': division.id,
        'is_citizen': bool(data.get('is_citizen', True)),
        'is_overseas': bool(data.get('is_overseas', False)),
    }, None


# ----------------------------- the voter themselves --------------------------

@voters_bp.route('/me', methods=['GET'])
@user_required
def my_enrolment(current_user):
    """Check my own enrolment status."""
    voter = current_user.voter
    if not voter:
        return jsonify({'enrolled': False, 'status': 'not_enrolled'}), C.HTTP_OK
    return jsonify({'enrolled': voter.enrolment_status == C.ENROLMENT_ENROLLED,
                    'status': voter.enrolment_status, 'voter': voter.to_dict()}), C.HTTP_OK


@voters_bp.route('/enrol', methods=['POST'])
@roles_required(C.ROLE_VOTER)
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
@roles_required(C.ROLE_VOTER)
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
@user_required
def my_eligibility(current_user):
    """Before voting: am I eligible to vote in this election?"""
    election = Election.query.get(request.args.get('election_id', type=int))
    if not election:
        return jsonify({'error': C.ERROR_ELECTION_NOT_FOUND}), C.HTTP_NOT_FOUND
    eligible, code, message = check_eligibility(current_user.voter, election)
    return jsonify({'eligible': eligible, 'reason_code': code, 'message': message}), C.HTTP_OK


# ----------------------------- AEC officers ---------------------------------

@voters_bp.route('/', methods=['POST'])
@roles_required(C.ROLE_AEC_OFFICER, C.ROLE_ADMIN)
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
        if User.query.filter((User.username == data['username'].strip()) |
                             (User.email == data['email'].strip().lower())).first():
            return jsonify({'error': 'Username or email already exists'}), C.HTTP_CONFLICT
        user = User(data['username'].strip(), data['email'].strip().lower(), data['password'], C.ROLE_VOTER)
        db.session.add(user)
        db.session.flush()

    voter = Voter(user_id=user.id if user else None, enrolment_status=C.ENROLMENT_ENROLLED,
                  registered_via='aec_officer', registered_by=current_user.id, **clean)
    db.session.add(voter)
    db.session.commit()
    log_action('VOTER_REGISTERED', user=current_user, object_type='voter', object_id=voter.id)
    return jsonify({'message': 'Citizen registered', 'voter': voter.to_dict()}), C.HTTP_CREATED


@voters_bp.route('/', methods=['GET'])
@roles_required(C.ROLE_AEC_OFFICER, C.ROLE_ADMIN)
def list_voters(current_user):
    q = Voter.query
    status = request.args.get('status')
    if status:
        q = q.filter_by(enrolment_status=status)
    name = request.args.get('q')
    if name:
        like = f'%{name}%'
        q = q.filter(db.or_(Voter.first_name.ilike(like), Voter.last_name.ilike(like)))
    voters = q.order_by(Voter.id.desc()).limit(100).all()
    return jsonify({'voters': [v.to_dict() for v in voters], 'total': len(voters)}), C.HTTP_OK


def _set_status(voter_id, status, action, current_user):
    voter = Voter.query.get(voter_id)
    if not voter:
        return jsonify({'error': C.ERROR_VOTER_NOT_FOUND}), C.HTTP_NOT_FOUND
    voter.enrolment_status = status
    db.session.commit()
    log_action(action, user=current_user, object_type='voter', object_id=voter.id)
    return jsonify({'message': f'Voter {status}', 'voter': voter.to_dict()}), C.HTTP_OK


@voters_bp.route('/<int:voter_id>/approve', methods=['PUT'])
@roles_required(C.ROLE_AEC_OFFICER, C.ROLE_ADMIN)
def approve_voter(current_user, voter_id):
    return _set_status(voter_id, C.ENROLMENT_ENROLLED, 'VOTER_APPROVED', current_user)


@voters_bp.route('/<int:voter_id>/remove', methods=['PUT'])
@roles_required(C.ROLE_AEC_OFFICER, C.ROLE_ADMIN)
def remove_voter(current_user, voter_id):
    return _set_status(voter_id, C.ENROLMENT_REMOVED, 'VOTER_REMOVED', current_user)
