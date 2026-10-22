import os
import socket

from flask import Blueprint, jsonify, render_template
from sqlalchemy import text

from app import db

main_bp = Blueprint('main', __name__)


@main_bp.route('/')
def index():
    return render_template('index.html')


@main_bp.route('/login')
def login_page():
    return render_template('login.html')


@main_bp.route('/register')
def register_page():
    return render_template('register.html')


@main_bp.route('/mfa/enrol')
def mfa_enrol_page():
    """REQ-10: staff register a security key with their enrolment code."""
    return render_template('mfa_enrol.html')


@main_bp.route('/dashboard')
def dashboard():
    """Voter dashboard: enrolment status, address update, link to vote."""
    return render_template('dashboard.html')


@main_bp.route('/vote')
def vote_page():
    return render_template('vote.html')


@main_bp.route('/staff')
def staff_page():
    """Portal for AEC officers, Commissioner's delegates and election admins."""
    return render_template('staff.html')


@main_bp.route('/results')
def results_page():
    return render_template('results.html')


# REQ-08: the databases this replica cannot serve votes without. None = the
# main database, 'ballots' = the REQ-01 ballot database.
HEALTH_CHECKS = {'database': None, 'ballot_database': 'ballots'}


def _probe(bind_key):
    """'ok' if the database answers a trivial query, otherwise a short error name."""
    try:
        with db.engines[bind_key].connect() as conn:
            conn.execute(text('SELECT 1'))
        return 'ok'
    except Exception as e:   # any failure means this replica should be taken out of rotation
        return f'error: {e.__class__.__name__}'


@main_bp.route('/health')
def health_check():
    """REQ-08: health of this replica. The load balancer polls it every 2 seconds
    and stops sending traffic to a replica that answers 503 (or not at all).
    Public, and reveals no data: only which replica answered and check results."""
    checks = {name: _probe(bind) for name, bind in HEALTH_CHECKS.items()}
    healthy = all(result == 'ok' for result in checks.values())
    body = {'status': 'healthy' if healthy else 'unhealthy',
            'instance': os.environ.get('INSTANCE_NAME') or socket.gethostname(),
            'checks': checks}
    return jsonify(body), 200 if healthy else 503
