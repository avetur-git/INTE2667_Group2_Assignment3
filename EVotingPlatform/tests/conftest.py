import pytest
from app import create_app, db


@pytest.fixture()
def app():
    app = create_app({'TESTING': True, 'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
                      'SQLALCHEMY_BINDS': {'ballots': 'sqlite:///:memory:'},
                      'BCRYPT_ROUNDS': 4,
                      # REQ-03: off so older tests can vote at seeded terminals;
                      # tests/test_terminal_verification.py turns it on
                      'TERMINAL_SIGNATURE_REQUIRED': False,
                      # REQ-10: off so other tests can log staff in with a password;
                      # tests/test_webauthn_mfa.py turns it on
                      'MFA_REQUIRED': False})
    with app.app_context():
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()
