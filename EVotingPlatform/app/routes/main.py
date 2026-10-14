from flask import Blueprint, jsonify, render_template

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


@main_bp.route('/health')
def health_check():
    """Health check endpoint (used by Docker; REQ-08 failover checks reuse it)."""
    return jsonify({'status': 'healthy', 'message': 'E-Voting API is running'}), 200
