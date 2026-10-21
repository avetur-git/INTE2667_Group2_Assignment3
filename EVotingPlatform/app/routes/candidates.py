from flask import Blueprint, request, jsonify
from app.models.candidateHistory import CandidateHistory
from app.models.user import User
from app import db
from app.models.candidate import Candidate
from app.models.election import Election, Division
from app.security.hooks import hooks
from app.services.audit import log_action
from app.utils import constants as C
from app.utils.helpers import validate_required_fields
from app.services.candidate_history import record_change, diff_fields
from app.security.rbac import P, permission_required


candidates_bp = Blueprint('candidates', __name__)


@candidates_bp.route('/', methods=['GET'])
def list_candidates():
    """Public list of candidates. Filter by election_id, contest_type, division_id, state."""
    q = Candidate.query
    for field in ('election_id', 'division_id'):
        if request.args.get(field, type=int):
            q = q.filter(getattr(Candidate, field) == request.args.get(field, type=int))
    if request.args.get('contest_type'):
        q = q.filter_by(contest_type=request.args['contest_type'])
    if request.args.get('state'):
        q = q.filter_by(state=request.args['state'].upper())
    cands = q.order_by(Candidate.contest_type, Candidate.division_id, Candidate.state,
                       Candidate.ballot_position, Candidate.group_code, Candidate.group_order).all()
    return jsonify({'candidates': [c.to_dict() for c in cands]}), C.HTTP_OK


@candidates_bp.route('/', methods=['POST'])
@permission_required(P.CANDIDATE_MANAGE)
def add_candidate(current_user):
    """A Commissioner's delegate enters candidate details, including order within party group (REQ-17)."""
    data = request.get_json(silent=True)
    ok, error = validate_required_fields(data, ['election_id', 'contest_type', 'name'])
    if not ok:
        return jsonify({'error': error}), 400
    election = Election.query.get(data['election_id'])
    if not election:
        return jsonify({'error': C.ERROR_ELECTION_NOT_FOUND}), C.HTTP_NOT_FOUND
    if election.status != C.ELECTION_DRAFT:
        return jsonify({'error': 'Candidates can only be added while the election is in draft'}), C.HTTP_CONFLICT

    contest = data['contest_type']
    if contest == C.CONTEST_HOUSE:
        if not Division.query.get(data.get('division_id')):
            return jsonify({'error': 'House candidates need a valid division_id'}), 400
        cand = Candidate(election_id=election.id, contest_type=contest, name=data['name'].strip(),
                         party=data.get('party'), division_id=data['division_id'],
                         ballot_position=data.get('ballot_position'), created_by=current_user.id)
    elif contest == C.CONTEST_SENATE:
        state = str(data.get('state', '')).upper()
        if state not in C.STATES:
            return jsonify({'error': 'Senate candidates need a valid state'}), 400
        cand = Candidate(election_id=election.id, contest_type=contest, name=data['name'].strip(),
                         party=data.get('party'), state=state,
                         group_code=(data.get('group_code') or C.UNGROUPED).upper(),
                         group_order=data.get('group_order'), created_by=current_user.id)
    else:
        return jsonify({'error': 'contest_type must be house or senate'}), 400

    db.session.add(cand)
    db.session.flush()   # gives the candidate an id
    record_change(cand, 'created', {'name': {'old': None, 'new': cand.name}}, current_user)
    db.session.commit()
    log_action('CANDIDATE_ADDED', user=current_user, object_type='candidate', object_id=cand.id)
    return jsonify({'candidate': cand.to_dict()}), C.HTTP_CREATED


@candidates_bp.route('/<int:candidate_id>', methods=['PUT'])
@permission_required(P.CANDIDATE_MANAGE)
def update_candidate(current_user, candidate_id):
    """Edit name, party, group order or ballot position (draft elections only)."""
    cand = Candidate.query.get(candidate_id)
    if not cand:
        return jsonify({'error': C.ERROR_CANDIDATE_NOT_FOUND}), C.HTTP_NOT_FOUND
    election = Election.query.get(cand.election_id)
    if election.status != C.ELECTION_DRAFT:
        return jsonify({'error': 'Candidates can only be edited while the election is in draft'}), C.HTTP_CONFLICT
    data = request.get_json(silent=True) or {}
    changes = diff_fields(cand, data)
    for field, change in changes.items():
        setattr(cand, field, change['new'])
    if changes:
        record_change(cand, 'updated', changes, current_user)
    db.session.commit()
    log_action('CANDIDATE_UPDATED', user=current_user, object_type='candidate', object_id=cand.id,
               details={'fields': [f for f in data if f in ('name', 'party', 'group_code', 'group_order', 'ballot_position')]})
    return jsonify({'candidate': cand.to_dict()}), C.HTTP_OK


@candidates_bp.route('/<int:candidate_id>/exclude', methods=['POST'])
@permission_required(P.CANDIDATE_EXCLUDE)
def exclude_candidate(current_user, candidate_id):
    """Exclude a candidate when the High Court (Court of Disputed Returns) orders a
    recount. After excluding, re-run the tally. REQ-18 adds dual authorisation."""
    cand = Candidate.query.get(candidate_id)
    if not cand:
        return jsonify({'error': C.ERROR_CANDIDATE_NOT_FOUND}), C.HTTP_NOT_FOUND
    election = Election.query.get(cand.election_id)
    if election.status not in (C.ELECTION_CLOSED, C.ELECTION_COUNTED):
        return jsonify({'error': 'Exclusion is only possible after the election has closed'}), C.HTTP_CONFLICT
    data = request.get_json(silent=True) or {}
    ok, error = validate_required_fields(data, ['reason'])
    if not ok:
        return jsonify({'error': 'A reason (for example the court order reference) is required'}), 400

    allowed, message = hooks.run('authorise_action', 'EXCLUDE_CANDIDATE', current_user, cand, data)
    if not allowed:
        log_action('CANDIDATE_EXCLUSION_REFUSED', user=current_user, object_type='candidate',
                   object_id=cand.id, success=False, details={'message': message})
        return jsonify({'error': message or 'Authorisation required'}), C.HTTP_FORBIDDEN

    changes = {'is_excluded': {'old': False, 'new': True},
           'excluded_reason': {'old': None, 'new': data['reason'][:255]}}
    cand.is_excluded = True
    cand.excluded_reason = data['reason'][:255]
    record_change(cand, 'excluded', changes, current_user)
    db.session.commit()
    log_action('CANDIDATE_EXCLUDED', user=current_user, object_type='candidate', object_id=cand.id,
               details={'reason': cand.excluded_reason})
    return jsonify({'message': 'Candidate excluded. Re-run the tally to recount.',
                    'candidate': cand.to_dict()}), C.HTTP_OK

@candidates_bp.route('/<int:candidate_id>/history', methods=['GET'])
@permission_required(P.CANDIDATE_MANAGE)
def get_candidate_history(current_user, candidate_id):
    """Every change ever made to one candidate, oldest first (REQ-17)."""
    cand = Candidate.query.get(candidate_id)
    if not cand:
        return jsonify({'error': C.ERROR_CANDIDATE_NOT_FOUND}), C.HTTP_NOT_FOUND
    entries = (CandidateHistory.query.filter_by(candidate_id=candidate_id)
               .order_by(CandidateHistory.version).all())
    names = {u.id: u.username for u in User.query.filter(
        User.id.in_({e.changed_by for e in entries})).all()} if entries else {}
    history = [{**e.to_dict(), 'changed_by_username': names.get(e.changed_by)} for e in entries]
    return jsonify({'candidate_id': candidate_id, 'history': history, 'total': len(history)}), C.HTTP_OK
