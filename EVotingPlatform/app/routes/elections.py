from datetime import date
from flask import Blueprint, request, jsonify
from app import db
from app.models.election import Election, Division
from app.models.voter import VoteRecord
from app.services import ballot_store
from app.services.audit import log_action
from app.services.errors import ServiceError
from app.services.tallying import tally_election
from app.utils import constants as C
from app.utils.helpers import validate_required_fields, roles_required

elections_bp = Blueprint('elections', __name__)

# Allowed status changes: draft -> open -> closed -> (counted by tallying)
TRANSITIONS = {C.ELECTION_DRAFT: [C.ELECTION_OPEN],
               C.ELECTION_OPEN: [C.ELECTION_CLOSED]}


@elections_bp.route('/divisions', methods=['GET'])
def list_divisions():
    """Public: divisions are needed on the enrolment form."""
    q = Division.query
    if request.args.get('state'):
        q = q.filter_by(state=request.args['state'].upper())
    return jsonify({'divisions': [d.to_dict() for d in q.order_by(Division.name).all()]}), C.HTTP_OK


@elections_bp.route('/', methods=['GET'])
def list_elections():
    q = Election.query
    if request.args.get('status'):
        q = q.filter_by(status=request.args['status'])
    return jsonify({'elections': [e.to_dict() for e in q.order_by(Election.election_date.desc()).all()]}), C.HTTP_OK


@elections_bp.route('/', methods=['POST'])
@roles_required(C.ROLE_ADMIN)
def create_election(current_user):
    data = request.get_json(silent=True)
    ok, error = validate_required_fields(data, ['name', 'election_date'])
    if not ok:
        return jsonify({'error': error}), C.HTTP_BAD_REQUEST
    try:
        election_date = date.fromisoformat(data['election_date'])
    except ValueError:
        return jsonify({'error': 'election_date must be YYYY-MM-DD'}), C.HTTP_BAD_REQUEST
    election = Election(name=data['name'].strip(), election_date=election_date,
                        senate_seats_override=data.get('senate_seats_override'))
    db.session.add(election)
    db.session.commit()
    log_action('ELECTION_CREATED', user=current_user, object_type='election', object_id=election.id)
    return jsonify({'election': election.to_dict()}), C.HTTP_CREATED


@elections_bp.route('/<int:election_id>', methods=['GET'])
def get_election(election_id):
    election = Election.query.get(election_id)
    if not election:
        return jsonify({'error': C.ERROR_ELECTION_NOT_FOUND}), C.HTTP_NOT_FOUND
    return jsonify({'election': election.to_dict()}), C.HTTP_OK


@elections_bp.route('/<int:election_id>/status', methods=['PUT'])
@roles_required(C.ROLE_ADMIN)
def set_status(current_user, election_id):
    """Open or close an election - an election configuration change (REQ-04, REQ-06)."""
    election = Election.query.get(election_id)
    if not election:
        return jsonify({'error': C.ERROR_ELECTION_NOT_FOUND}), C.HTTP_NOT_FOUND
    new_status = (request.get_json(silent=True) or {}).get('status')
    if new_status not in TRANSITIONS.get(election.status, []):
        return jsonify({'error': f'Cannot change from {election.status} to {new_status}'}), C.HTTP_CONFLICT
    old = election.status
    election.status = new_status
    db.session.commit()
    log_action('ELECTION_STATUS_CHANGED', user=current_user, object_type='election',
               object_id=election.id, details={'from': old, 'to': new_status})
    return jsonify({'election': election.to_dict()}), C.HTTP_OK


@elections_bp.route('/<int:election_id>/turnout', methods=['GET'])
@roles_required(C.ROLE_ADMIN, C.ROLE_AEC_OFFICER)
def turnout(current_user, election_id):
    """How many people have voted, by channel. Never shows how anyone voted."""
    election = Election.query.get(election_id)
    if not election:
        return jsonify({'error': C.ERROR_ELECTION_NOT_FOUND}), C.HTTP_NOT_FOUND
    by_channel = {ch: VoteRecord.query.filter_by(election_id=election_id, channel=ch).count()
                  for ch in C.ALL_CHANNELS}
    return jsonify({'election_id': election_id,
                    'voters_who_voted': sum(by_channel.values()),
                    'ballots_stored': ballot_store.count_ballots(election_id),
                    'by_channel': by_channel}), C.HTTP_OK


@elections_bp.route('/<int:election_id>/tally', methods=['POST'])
@roles_required(C.ROLE_ADMIN)
def tally(current_user, election_id):
    """Compute results (can be re-run, for example after a court-ordered exclusion)."""
    election = Election.query.get(election_id)
    if not election:
        return jsonify({'error': C.ERROR_ELECTION_NOT_FOUND}), C.HTTP_NOT_FOUND
    try:
        summary = tally_election(election, current_user)
    except ServiceError as e:
        return jsonify({'error': e.message}), e.status
    log_action('ELECTION_TALLIED', user=current_user, object_type='election', object_id=election.id,
               details={'flagged_ballots': len(summary['flagged_ballots'])})
    return jsonify({'message': 'Tally complete', 'election': election.to_dict(),
                    'flagged_ballots': summary['flagged_ballots'],
                    'house_contests': len(summary['house']),
                    'senate_contests': len(summary['senate'])}), C.HTTP_OK
