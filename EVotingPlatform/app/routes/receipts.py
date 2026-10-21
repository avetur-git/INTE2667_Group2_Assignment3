"""REQ-20: public receipt lookup, verification key and bulletin board.
No login: anyone can check that a ballot was recorded, but nothing here says
who cast it, how they voted or when."""
from flask import Blueprint, jsonify

from app import db
from app.models.election import Election
from app.security import receipts
from app.utils import constants as C

receipts_bp = Blueprint('receipts', __name__)


@receipts_bp.route('/receipts/public-key', methods=['GET'])
def public_key():
    """The key that checks every receipt signature (Ed25519)."""
    return jsonify({'algorithm': 'Ed25519', 'key_id': receipts.key_id(),
                    'public_key_hex': receipts.public_key_bytes().hex(),
                    'public_key_pem': receipts.public_key_pem()}), C.HTTP_OK


@receipts_bp.route('/receipts/<code>', methods=['GET'])
def lookup_receipt(code):
    """Was the ballot with this receipt code recorded? Returns its hash and signature."""
    found = receipts.lookup(code)
    if not found:
        return jsonify({'found': False, 'error': 'No ballot with this receipt code'}), C.HTTP_NOT_FOUND
    return jsonify({'found': True, **found}), C.HTTP_OK


@receipts_bp.route('/elections/<int:election_id>/bulletin-board', methods=['GET'])
def bulletin_board(election_id):
    """Every receipt code and ballot hash for an election, once voting has closed."""
    election = db.session.get(Election, election_id)
    if not election:
        return jsonify({'error': C.ERROR_ELECTION_NOT_FOUND}), C.HTTP_NOT_FOUND
    if election.status not in (C.ELECTION_CLOSED, C.ELECTION_COUNTED):
        return jsonify({'error': 'The bulletin board is published after voting closes'}), C.HTTP_FORBIDDEN
    entries = receipts.bulletin_board(election_id)
    return jsonify({'election_id': election_id, 'key_id': receipts.key_id(),
                    'entries': entries, 'total': len(entries)}), C.HTTP_OK
