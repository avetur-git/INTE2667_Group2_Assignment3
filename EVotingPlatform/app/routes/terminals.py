from flask import Blueprint, request, jsonify
from app import db
from app.models.terminal import Terminal
from app.utils import constants as C
from app.utils.helpers import validate_required_fields, utcnow
from app.security.rbac import P, permission_required
from app.security.audit_trail import audit, audited

terminals_bp = Blueprint('terminals', __name__)


@terminals_bp.route('/', methods=['GET'])
@permission_required(P.TERMINAL_MANAGE)
@audited('TERMINALS_VIEWED', object_type='terminal')
def list_terminals(current_user):
    terminals = Terminal.query.order_by(Terminal.terminal_id).all()
    return jsonify({'terminals': [t.to_dict() for t in terminals], 'total': len(terminals)}), C.HTTP_OK


@terminals_bp.route('/', methods=['POST'])
@permission_required(P.TERMINAL_MANAGE)
@audited('TERMINAL_REGISTERED', object_type='terminal')
def register_terminal(current_user):
    data = request.get_json(silent=True)
    ok, error = validate_required_fields(data, ['terminal_id', 'channel', 'location_name'])
    if not ok:
        return jsonify({'error': error}), 400
    if data['channel'] not in C.TERMINAL_CHANNELS:
        return jsonify({'error': f'channel must be one of {", ".join(C.TERMINAL_CHANNELS)}'}), 400
    if Terminal.query.filter_by(terminal_id=data['terminal_id']).first():
        return jsonify({'error': 'Terminal already registered'}), C.HTTP_CONFLICT
    terminal = Terminal(terminal_id=data['terminal_id'], channel=data['channel'],
                        location_name=data['location_name'], state=data.get('state'),
                        country=data.get('country', 'Australia'),
                        software_version=data.get('software_version'),
                        software_signature=data.get('software_signature'))
    db.session.add(terminal)
    db.session.commit()
    audit.target(terminal.terminal_id)
    return jsonify({'terminal': terminal.to_dict()}), C.HTTP_CREATED


@terminals_bp.route('/<terminal_id>/deactivate', methods=['PUT'])
@permission_required(P.TERMINAL_MANAGE)
@audited('TERMINAL_DEACTIVATED', object_type='terminal')
def deactivate_terminal(current_user, terminal_id):
    terminal = Terminal.query.filter_by(terminal_id=terminal_id).first()
    if not terminal:
        return jsonify({'error': 'Terminal not found'}), C.HTTP_NOT_FOUND
    terminal.is_active = False
    db.session.commit()
    return jsonify({'terminal': terminal.to_dict()}), C.HTTP_OK


@terminals_bp.route('/<terminal_id>/heartbeat', methods=['POST'])
def heartbeat(terminal_id):
    """Terminals call this regularly so operators can see which are alive
    (monitoring for REQ-08 / REQ-09). No authentication in the base build."""
    terminal = Terminal.query.filter_by(terminal_id=terminal_id, is_active=True).first()
    if not terminal:
        return jsonify({'error': C.ERROR_TERMINAL_INVALID}), C.HTTP_NOT_FOUND
    terminal.last_seen = utcnow()
    db.session.commit()
    return jsonify({'status': 'ok', 'server_time': terminal.last_seen.isoformat()}), C.HTTP_OK
