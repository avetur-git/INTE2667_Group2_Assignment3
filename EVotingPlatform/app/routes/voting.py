from flask import Blueprint, request, jsonify
from app.models.election import Election
from app.services.eligibility import check_eligibility
from app.services.errors import ServiceError
from app.services.voting import build_ballot_paper, cast_vote
from app.utils import constants as C
from app.utils.helpers import get_request_meta
from app.security.rbac import P, permission_required

voting_bp = Blueprint('voting', __name__)


@voting_bp.route('/ballot-paper', methods=['GET'])
@permission_required(P.BALLOT_CAST)
def ballot_paper(current_user):
    """The candidates this voter will see (their division for the House, their state for the Senate)."""
    election = Election.query.get(request.args.get('election_id', type=int))
    if not election:
        return jsonify({'error': C.ERROR_ELECTION_NOT_FOUND}), C.HTTP_NOT_FOUND
    if election.status != C.ELECTION_OPEN:
        return jsonify({'error': C.ERROR_ELECTION_NOT_OPEN}), C.HTTP_CONFLICT
    eligible, code, message = check_eligibility(current_user.voter, election)
    if not eligible:
        return jsonify({'error': message, 'reason_code': code}), C.HTTP_FORBIDDEN
    return jsonify(build_ballot_paper(current_user.voter, election)), C.HTTP_OK


@voting_bp.route('/cast', methods=['POST'])
@permission_required(P.BALLOT_CAST)
def cast(current_user):
    """Cast a ballot.
    Body: {election_id, channel, terminal_id (polling_station / diplomatic_mission only),
           house_preferences: [candidate ids, most preferred first],
           senate_atl: {group_code: number}, senate_btl: {candidate_id: number}}"""
    data = request.get_json(silent=True) or {}
    election = Election.query.get(data.get('election_id'))
    if not election:
        return jsonify({'error': C.ERROR_ELECTION_NOT_FOUND}), C.HTTP_NOT_FOUND
    voter = current_user.voter
    if not voter:
        return jsonify({'error': C.ERROR_NOT_ENROLLED}), C.HTTP_FORBIDDEN
    try:
        receipt = cast_vote(voter, election, data, data.get('channel', C.CHANNEL_ONLINE),
                            data.get('terminal_id'), current_user, get_request_meta())
    except ServiceError as e:
        return jsonify({'error': e.message, 'reason_code': e.code}), e.status
    return jsonify({'message': 'Your ballot has been recorded', 'receipt': receipt}), C.HTTP_CREATED
