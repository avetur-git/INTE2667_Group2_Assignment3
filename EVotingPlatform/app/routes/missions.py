import ipaddress

from flask import Blueprint, request, jsonify
from app import db
from app.models.mission import Mission
from app.models.terminal import Terminal
from app.security.mission_access import FINGERPRINT_RE, normalise_fingerprint
from app.utils import constants as C
from app.security.rbac import P, permission_required
from app.security.audit_trail import audit, audited
from app.utils.helpers import utcnow, validate_required_fields

missions_bp = Blueprint('missions', __name__)


def _clean_list(value):
    return value if isinstance(value, list) and value else None


def _parse(data):
    """Validate a new mission. Returns (clean_dict, error)."""
    ok, error = validate_required_fields(data, ['name', 'country', 'allowed_cidrs', 'cert_fingerprints', 'terminal_ids'])
    if not ok:
        return None, error
    country = str(data['country']).strip().upper()
    if len(country) != 2 or not country.isalpha():
        return None, 'country must be a two-letter code, for example JP'
    cidrs = _clean_list(data['allowed_cidrs'])
    if not cidrs:
        return None, 'allowed_cidrs must be a non-empty list'
    try:
        cidrs = [str(ipaddress.ip_network(c, strict=False)) for c in cidrs]
    except ValueError as err:
        return None, f'Invalid network range: {err}'
    fingerprints = _clean_list(data['cert_fingerprints'])
    if not fingerprints:
        return None, 'cert_fingerprints must be a non-empty list'
    fingerprints = [normalise_fingerprint(f) for f in fingerprints]
    if not all(f and FINGERPRINT_RE.match(f) for f in fingerprints):
        return None, 'Each certificate fingerprint must be 64 hex characters (SHA-256)'
    terminal_ids = _clean_list(data['terminal_ids'])
    if not terminal_ids:
        return None, 'terminal_ids must be a non-empty list'
    for tid in terminal_ids:
        terminal = Terminal.query.filter_by(terminal_id=tid).first()
        if not terminal or terminal.channel != C.CHANNEL_DIPLOMATIC_MISSION:
            return None, f'{tid} is not a registered diplomatic mission terminal'
    return {'name': data['name'].strip(), 'country': country, 'allowed_cidrs': cidrs,
            'cert_fingerprints': fingerprints, 'terminal_ids': list(terminal_ids)}, None


@missions_bp.route('/', methods=['GET'])
@permission_required(P.MISSION_MANAGE)
@audited('MISSIONS_VIEWED', object_type='mission')
def list_missions(current_user):
    missions = Mission.query.order_by(Mission.name).all()
    return jsonify({'missions': [m.to_dict() for m in missions], 'total': len(missions)}), C.HTTP_OK


@missions_bp.route('/', methods=['POST'])
@permission_required(P.MISSION_MANAGE)
@audited('MISSION_CREATED', object_type='mission')
def add_mission(current_user):
    clean, error = _parse(request.get_json(silent=True))
    if error:
        return jsonify({'error': error}), C.HTTP_BAD_REQUEST
    if Mission.query.filter_by(name=clean['name']).first():
        return jsonify({'error': 'A mission with this name already exists'}), C.HTTP_CONFLICT
    mission = Mission(created_by=current_user.id, revoked_fingerprints=[], **clean)
    db.session.add(mission)
    db.session.commit()
    audit.target(mission.id)
    audit.details.update({'name': mission.name, 'country': mission.country,
                          'terminal_ids': mission.terminal_ids, 'allowed_cidrs': mission.allowed_cidrs})
    return jsonify({'mission': mission.to_dict()}), C.HTTP_CREATED


@missions_bp.route('/<int:mission_id>/deactivate', methods=['PUT'])
@permission_required(P.MISSION_MANAGE)
@audited('MISSION_DEACTIVATED', object_type='mission')
def deactivate_mission(current_user, mission_id):
    """Stops voting from this mission straight away: the next session is refused."""
    mission = Mission.query.get(mission_id)
    if not mission:
        return jsonify({'error': 'Mission not found'}), C.HTTP_NOT_FOUND
    mission.is_active = False
    mission.deactivated_at = utcnow()
    db.session.commit()
    audit.details['name'] = mission.name
    return jsonify({'mission': mission.to_dict()}), C.HTTP_OK


@missions_bp.route('/<int:mission_id>/revoke-cert', methods=['POST'])
@permission_required(P.MISSION_MANAGE)
@audited('MISSION_CERT_REVOKED', object_type='mission')
def revoke_certificate(current_user, mission_id):
    """Revoke one device certificate (for example a lost or stolen terminal)."""
    mission = Mission.query.get(mission_id)
    if not mission:
        return jsonify({'error': 'Mission not found'}), C.HTTP_NOT_FOUND
    fingerprint = normalise_fingerprint((request.get_json(silent=True) or {}).get('fingerprint'))
    if not fingerprint or not FINGERPRINT_RE.match(fingerprint):
        return jsonify({'error': 'fingerprint must be 64 hex characters (SHA-256)'}), C.HTTP_BAD_REQUEST
    if fingerprint not in (mission.cert_fingerprints or []):
        return jsonify({'error': 'That certificate is not approved for this mission'}), C.HTTP_NOT_FOUND
    # Assign new lists (not in-place edits) so SQLAlchemy sees the change.
    mission.cert_fingerprints = [f for f in mission.cert_fingerprints if f != fingerprint]
    mission.revoked_fingerprints = list(mission.revoked_fingerprints or []) + [fingerprint]
    db.session.commit()
    audit.details.update({'name': mission.name, 'fingerprint': fingerprint})
    return jsonify({'mission': mission.to_dict()}), C.HTTP_OK