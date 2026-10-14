from flask import Blueprint, request, jsonify
from app.models.election import Election
from app.services.audit import log_action
from app.services.tallying import stored_results, export_results
from app.utils import constants as C
from app.utils.helpers import roles_required

results_bp = Blueprint('results', __name__)


def _counted_election(election_id):
    election = Election.query.get(election_id)
    if not election:
        return None, (jsonify({'error': C.ERROR_ELECTION_NOT_FOUND}), C.HTTP_NOT_FOUND)
    if election.status != C.ELECTION_COUNTED:
        return None, (jsonify({'error': 'Results are not available yet'}), C.HTTP_CONFLICT)
    return election, None


@results_bp.route('/<int:election_id>', methods=['GET'])
def public_results(election_id):
    """Public summary: winners only, without the round-by-round detail."""
    election, err = _counted_election(election_id)
    if err:
        return err
    data = stored_results(election_id)
    return jsonify({
        'election': election.to_dict(),
        'house': [{'division': r['division'], 'winner': r['winner']} for r in data['house']],
        'senate': [{'state': r['state'], 'seats': r['seats'], 'elected': r['elected']} for r in data['senate']],
    }), C.HTTP_OK


@results_bp.route('/<int:election_id>/detail', methods=['GET'])
@roles_required(C.ROLE_ADMIN)
def detailed_results(current_user, election_id):
    """Full counts with every round, for the election admin."""
    election, err = _counted_election(election_id)
    if err:
        return err
    return jsonify({'election': election.to_dict(), **stored_results(election_id)}), C.HTTP_OK


@results_bp.route('/<int:election_id>/export', methods=['GET'])
@roles_required(C.ROLE_ADMIN)
def export(current_user, election_id):
    """Results package for sharing with external systems."""
    election, err = _counted_election(election_id)
    if err:
        return err
    package = export_results(election)
    log_action('RESULTS_EXPORTED', user=current_user, object_type='election', object_id=election_id)
    return jsonify(package), C.HTTP_OK
