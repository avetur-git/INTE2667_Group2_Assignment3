"""REQ-19: restrict overseas voting to verified diplomatic missions and block voting sessions
from unauthorised locations.

How it plugs in (no route changes). Two existing hooks are wrapped, and the previous check
on each hook still runs first, so this does not replace another requirement's check
(for example the REQ-03 terminal signature check on `verify_terminal`):

  verify_terminal(terminal, request_meta)   diplomatic mission terminals
      1. the terminal belongs to an ACTIVE mission
      2. the caller's IP is inside the mission's approved network range
      3. the device certificate fingerprint is present, approved and not revoked
      4. the geo-IP country matches the mission's country (unknown = refuse)

  pre_cast(voter, election, channel, request_meta)   online and polling station sessions
      the geo-IP country must be Australia (unknown = refuse)

The voter always gets the same general message. The exact reason, IP address, country,
channel and terminal go to the audit log.

Switch: `ENFORCE_MISSION_ACCESS` (config or environment variable, default off so the base
build keeps working on a laptop). `SIMULATE_CLIENT_IP` (default off, development only) lets a
request carry the header `X-Simulated-Client-IP` so the checks can be demonstrated with curl.
"""
import ipaddress
import os
import re

from flask import current_app, request

from app.security import geoip
from app.security.hooks import hooks
from app.utils import constants as C

GENERIC_REFUSAL = 'Voting is not available from this location or device'
HOME_COUNTRY = 'AU'
FINGERPRINT_HEADER = 'X-Device-Cert-Fingerprint'
SIMULATED_IP_HEADER = 'X-Simulated-Client-IP'
FINGERPRINT_RE = re.compile(r'^[0-9a-f]{64}$')

DEMO_FINGERPRINT = 'ab' * 32      # demo device certificate fingerprint (not a real certificate)


def normalise_fingerprint(value):
    """Lower-case hex without colons or spaces, or None if empty."""
    cleaned = (value or '').strip().lower().replace(':', '').replace(' ', '')
    return cleaned or None


def client_ip(request_meta):
    """The caller's IP address. Behind a proxy or VPN gateway this must be the address the
    gateway verified (see docs/MISSION_ACCESS.md); X-Forwarded-For is never trusted here."""
    if current_app.config.get('SIMULATE_CLIENT_IP'):
        simulated = request.headers.get(SIMULATED_IP_HEADER)
        if simulated:
            return simulated.strip()
    return (request_meta or {}).get('ip')


def _in_ranges(ip, cidrs):
    try:
        address = ipaddress.ip_address(ip)
    except (TypeError, ValueError):
        return False
    return any(address in ipaddress.ip_network(c, strict=False) for c in cidrs)


def _find_mission(terminal_id):
    """(active_mission, any_mission) for a terminal id."""
    from app.models.mission import Mission
    found = [m for m in Mission.query.all() if terminal_id in (m.terminal_ids or [])]
    active = next((m for m in found if m.is_active), None)
    return active, (found[0] if found else None)


def _refuse(reason, *, channel, ip, terminal_id=None, country=None, mission=None):
    """Write the exact reason to the audit log, return the general message to the caller."""
    try:
        from flask_jwt_extended import get_jwt_identity
        from app.models.user import User
        from app.services.audit import log_action
        user = None
        try:
            user = User.query.get(int(get_jwt_identity()))
        except Exception:
            pass
        log_action('VOTE_ACCESS_REFUSED', user=user, object_type='terminal' if terminal_id else 'session',
                   object_id=terminal_id or channel, success=False,
                   details={'reason': reason, 'channel': channel, 'ip': ip, 'country': country,
                            'mission': mission.name if mission else None})
    except Exception:
        # A failure to log must never turn a refusal into an acceptance.
        pass
    return False, GENERIC_REFUSAL


def _make_verify_terminal(previous):
    def verify_terminal(terminal, request_meta):
        ok, message = previous(terminal, request_meta)
        if not ok:
            return ok, message
        if not current_app.config.get('ENFORCE_MISSION_ACCESS'):
            return True, ''
        if terminal.channel != C.CHANNEL_DIPLOMATIC_MISSION:
            return True, ''

        ip = client_ip(request_meta)
        ctx = dict(channel=terminal.channel, ip=ip, terminal_id=terminal.terminal_id)

        mission, any_mission = _find_mission(terminal.terminal_id)
        if mission is None:
            return _refuse('mission_inactive' if any_mission else 'terminal_not_in_a_mission',
                           mission=any_mission, **ctx)
        ctx['mission'] = mission

        if not _in_ranges(ip, mission.allowed_cidrs or []):
            return _refuse('ip_outside_mission_range', **ctx)

        fingerprint = normalise_fingerprint(request.headers.get(FINGERPRINT_HEADER))
        if fingerprint is None:
            return _refuse('device_certificate_missing', **ctx)
        if fingerprint in (mission.revoked_fingerprints or []):
            return _refuse('device_certificate_revoked', **ctx)
        if fingerprint not in (mission.cert_fingerprints or []):
            return _refuse('device_certificate_not_approved', **ctx)

        country = geoip.lookup_country(ip)
        ctx['country'] = country
        if country is None:
            return _refuse('geoip_lookup_failed', **ctx)
        if country != mission.country.upper():
            return _refuse('geoip_country_mismatch', **ctx)
        return True, ''
    verify_terminal._req19 = True
    verify_terminal._previous = previous
    return verify_terminal


def _make_pre_cast(previous):
    def pre_cast(voter, election, channel, request_meta):
        ok, message = previous(voter, election, channel, request_meta)
        if not ok:
            return ok, message
        if not current_app.config.get('ENFORCE_MISSION_ACCESS'):
            return True, ''
        if channel not in (C.CHANNEL_ONLINE, C.CHANNEL_POLLING_STATION):
            return True, ''
        ip = client_ip(request_meta)
        country = geoip.lookup_country(ip)
        if country is None:
            return _refuse('geoip_lookup_failed', channel=channel, ip=ip)
        if country != HOME_COUNTRY:
            return _refuse('outside_australia', channel=channel, ip=ip, country=country)
        return True, ''
    pre_cast._req19 = True
    pre_cast._previous = previous
    return pre_cast


def install():
    """Wrap the two hooks. Safe to call many times (never wraps itself twice)."""
    for name, factory in (('verify_terminal', _make_verify_terminal), ('pre_cast', _make_pre_cast)):
        current = hooks.get(name)
        if not getattr(current, '_req19', False):
            hooks.register(name, factory(current))


def _env_flag(name, default=False):
    return os.environ.get(name, str(default)).strip().lower() in ('1', 'true', 'yes', 'on')


def init_app(app):
    """Called from create_app: settings, admin routes, hook wrapping, demo seed command."""
    app.config.setdefault('ENFORCE_MISSION_ACCESS', _env_flag('ENFORCE_MISSION_ACCESS'))
    app.config.setdefault('SIMULATE_CLIENT_IP', _env_flag('SIMULATE_CLIENT_IP'))

    from app.routes.missions import missions_bp
    app.register_blueprint(missions_bp, url_prefix='/api/missions')
    install()

    @app.cli.command('seed-mission-demo')
    def seed_mission_demo():
        """Create a demo mission for terminal DM-0001 (run seed-demo first)."""
        import click
        from app import db
        from app.models.mission import Mission
        if Mission.query.filter_by(name='Demo Australian Embassy').first():
            click.echo('Demo mission already present')
            return
        db.session.add(Mission(name='Demo Australian Embassy', country='JP',
                               allowed_cidrs=['198.51.100.0/24'],
                               cert_fingerprints=[DEMO_FINGERPRINT], revoked_fingerprints=[],
                               terminal_ids=['DM-0001']))
        db.session.commit()
        click.echo(f'Demo mission created. Range 198.51.100.0/24, country JP, fingerprint {DEMO_FINGERPRINT}')