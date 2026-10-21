from datetime import timedelta
import os

from dotenv import load_dotenv
from flask import Flask, jsonify
from flask_cors import CORS
from flask_jwt_extended import JWTManager
from flask_sqlalchemy import SQLAlchemy

# Load environment variables
load_dotenv()

# Initialize extensions
db = SQLAlchemy()
jwt = JWTManager()


def create_app(test_config=None):
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    app = Flask(__name__,
                template_folder=os.path.join(project_root, 'templates'),
                static_folder=os.path.join(project_root, 'static'))

    # Configuration (development defaults - see .env.example)
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-me-0123456789abcdef')
    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///evoting.db')
    # REQ-01: ballots live in their own database file, apart from the voter data
    app.config['SQLALCHEMY_BINDS'] = {
        'ballots': os.environ.get('BALLOT_DATABASE_URL', 'sqlite:///ballots.db')
    }
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['JWT_SECRET_KEY'] = os.environ.get('JWT_SECRET_KEY', 'dev-jwt-secret-key-change-me-0123456789abcdef')
    app.config['JWT_ACCESS_TOKEN_EXPIRES'] = timedelta(hours=int(os.environ.get('JWT_EXPIRES_HOURS', 8)))
    app.config['BCRYPT_ROUNDS'] = int(os.environ.get('BCRYPT_ROUNDS', 12))
    # REQ-10: WebAuthn security keys for privileged users. MFA_REQUIRED is off by
    # default for now; set it to true to enforce the second factor at login.
    app.config['MFA_REQUIRED'] = os.environ.get('MFA_REQUIRED', 'false').lower() == 'true'
    app.config['WEBAUTHN_RP_ID'] = os.environ.get('WEBAUTHN_RP_ID', 'localhost')
    app.config['WEBAUTHN_RP_NAME'] = os.environ.get('WEBAUTHN_RP_NAME', 'E-Voting Platform')
    app.config['WEBAUTHN_ORIGINS'] = [o.strip() for o in os.environ.get(
        'WEBAUTHN_ORIGINS', 'http://localhost:3000,http://localhost:9001').split(',') if o.strip()]
    if test_config:
        app.config.update(test_config)

    # Initialize extensions with app
    db.init_app(app)
    jwt.init_app(app)
    CORS(app)

    # Models and security hooks (base behaviour; see app/security/hooks.py)
    from app import models  # noqa: F401
    from app.security import register_default_hooks
    register_default_hooks()

    # REQ-10: security-key check for privileged logins
    from app.security.webauthn_mfa import register_webauthn_mfa
    register_webauthn_mfa()

    # Register blueprints
    from app.routes import (main_bp, auth_bp, voters_bp, elections_bp, candidates_bp,
                            voting_bp, terminals_bp, results_bp, admin_bp, approvals_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp, url_prefix='/api/auth')
    app.register_blueprint(voters_bp, url_prefix='/api/voters')
    app.register_blueprint(elections_bp, url_prefix='/api/elections')
    app.register_blueprint(candidates_bp, url_prefix='/api/candidates')
    app.register_blueprint(voting_bp, url_prefix='/api/voting')
    app.register_blueprint(terminals_bp, url_prefix='/api/terminals')
    app.register_blueprint(results_bp, url_prefix='/api/results')
    app.register_blueprint(admin_bp, url_prefix='/api/admin')
    app.register_blueprint(approvals_bp, url_prefix='/api')
    from app.routes.webauthn import webauthn_bp
    app.register_blueprint(webauthn_bp, url_prefix='/api/auth/webauthn')

    # REQ-19: diplomatic mission allow-list and location checks
    from app.security.mission_access import init_app as init_mission_access
    init_mission_access(app)

    @app.errorhandler(404)
    def not_found(e):
        return jsonify({'error': 'Not found'}), 404

    # Create database tables
    with app.app_context():
        db.create_all()

    from app.cli import register_cli
    register_cli(app)

    # REQ-02: hash every ballot when it is stored
    from app.security.ballot_chain import register_ballot_chain
    register_ballot_chain()

    return app
