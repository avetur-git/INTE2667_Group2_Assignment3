"""REQ-10: phishing-resistant MFA (WebAuthn) for privileged users."""
from datetime import timedelta

import pytest

from app import db
from app.models import AuditLog, EnrolmentCode, User, WebAuthnCredential
from app.security.webauthn_mfa import issue_enrolment_code
from app.utils import constants as C
from app.utils.helpers import utcnow
from tests.virtual_authenticator import VirtualAuthenticator

PASSWORD = 'Str0ngPassw0rd!'


@pytest.fixture()
def admin(app):
    user = User('admin', 'admin@example.test', PASSWORD, C.ROLE_ADMIN)
    db.session.add(user)
    db.session.commit()
    return user


@pytest.fixture()
def mfa_on(app):
    app.config['MFA_REQUIRED'] = True


@pytest.fixture()
def key():
    return VirtualAuthenticator()


def creds(username='admin', **extra):
    return {'username': username, 'password': PASSWORD, **extra}


def enrol(client, user, key, origin=None):
    code = issue_enrolment_code(user)
    begin = client.post('/api/auth/webauthn/register/begin', json=creds(user.username, enrolment_code=code))
    assert begin.status_code == 200, begin.get_json()
    return client.post('/api/auth/webauthn/register/finish',
                       json=creds(user.username, enrolment_code=code,
                                  credential=key.create(begin.get_json()['options'], origin=origin)))


def login_begin(client, username='admin'):
    r = client.post('/api/auth/webauthn/login/begin', json=creds(username))
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def login(client, assertion, username='admin'):
    return client.post('/api/auth/login', json=creds(username, webauthn=assertion))


# ------------------------------------------------- acceptance tests (issue #15)

def test_admin_login_requires_webauthn(client, admin, key, mfa_on):
    assert enrol(client, admin, key).status_code == 201

    no_key = client.post('/api/auth/login', json=creds())
    assert no_key.status_code == 401
    assert 'access_token' not in no_key.get_json()

    begin = login_begin(client)
    assert begin['mfa_required'] is True
    ok = login(client, key.get(begin['options']))
    assert ok.status_code == 200
    assert ok.get_json()['access_token']


def test_wrong_origin_rejected(client, admin, key, mfa_on):
    enrol(client, admin, key)
    assertion = key.get(login_begin(client)['options'], origin='https://evil.example')
    assert login(client, assertion).status_code == 401


def test_replayed_assertion_rejected(client, admin, key, mfa_on):
    enrol(client, admin, key)
    assertion = key.get(login_begin(client)['options'])
    assert login(client, assertion).status_code == 200
    assert login(client, assertion).status_code == 401


# ------------------------------------------------------------- more login cases

def test_wrong_rp_id_rejected(client, admin, key, mfa_on):
    enrol(client, admin, key)
    assertion = key.get(login_begin(client)['options'], rp_id='evil.example')
    assert login(client, assertion).status_code == 401


def test_unregistered_key_rejected(client, admin, key, mfa_on):
    enrol(client, admin, key)
    options = login_begin(client)['options']
    other = VirtualAuthenticator()
    options['allowCredentials'] = []  # a real browser would refuse; an attacker would not
    assert login(client, other.get(options)).status_code == 401


def test_cloned_key_sign_count_rejected(client, admin, key, mfa_on):
    enrol(client, admin, key)
    assert login(client, key.get(login_begin(client)['options'])).status_code == 200
    key.sign_count = 0  # a clone would replay an old counter value
    assert login(client, key.get(login_begin(client)['options'])).status_code == 401


def test_challenge_expires(client, admin, key, mfa_on):
    from app.models import WebAuthnChallenge
    enrol(client, admin, key)
    options = login_begin(client)['options']
    WebAuthnChallenge.query.update({'expires_at': utcnow() - timedelta(seconds=1)})
    db.session.commit()
    assert login(client, key.get(options)).status_code == 401


def test_staff_without_key_told_to_enrol(client, admin, mfa_on):
    r = client.post('/api/auth/webauthn/login/begin', json=creds())
    assert r.status_code == 403
    assert r.get_json()['enrolment_required'] is True


def test_login_begin_checks_password(client, admin, mfa_on):
    r = client.post('/api/auth/webauthn/login/begin',
                    json={'username': 'admin', 'password': 'wrong-password'})
    assert r.status_code == 401
    assert 'options' not in r.get_json()


def test_failed_mfa_is_audited(client, admin, key, mfa_on):
    enrol(client, admin, key)
    client.post('/api/auth/login', json=creds())
    assert AuditLog.query.filter_by(action='MFA_FAILED', user_id=admin.id, success=False).count() == 1


def test_voter_login_unaffected(client, app, mfa_on):
    db.session.add(User('voter', 'voter@example.test', PASSWORD, C.ROLE_VOTER))
    db.session.commit()
    assert login_begin(client, 'voter')['mfa_required'] is False
    assert client.post('/api/auth/login', json=creds('voter')).status_code == 200


def test_mfa_off_by_default_allows_password_login(client, admin):
    assert login_begin(client)['mfa_required'] is False
    assert client.post('/api/auth/login', json=creds()).status_code == 200


# ---------------------------------------------------------------- enrolment

def test_enrolment_registers_key(client, admin, key):
    assert enrol(client, admin, key).status_code == 201
    stored = WebAuthnCredential.query.filter_by(user_id=admin.id).one()
    assert stored.credential_id == key.credential_id
    assert AuditLog.query.filter_by(action='WEBAUTHN_KEY_REGISTERED').count() == 1


def test_enrolment_code_is_single_use(client, admin, key):
    code = issue_enrolment_code(admin)
    begin = client.post('/api/auth/webauthn/register/begin', json=creds(enrolment_code=code))
    client.post('/api/auth/webauthn/register/finish',
                json=creds(enrolment_code=code, credential=key.create(begin.get_json()['options'])))
    again = client.post('/api/auth/webauthn/register/begin', json=creds(enrolment_code=code))
    assert again.status_code == 401


def test_new_code_cancels_old_one(client, admin):
    old = issue_enrolment_code(admin)
    issue_enrolment_code(admin)
    r = client.post('/api/auth/webauthn/register/begin', json=creds(enrolment_code=old))
    assert r.status_code == 401


def test_expired_enrolment_code_rejected(client, admin):
    code = issue_enrolment_code(admin)
    EnrolmentCode.query.update({'expires_at': utcnow() - timedelta(seconds=1)})
    db.session.commit()
    r = client.post('/api/auth/webauthn/register/begin', json=creds(enrolment_code=code))
    assert r.status_code == 401


def test_wrong_enrolment_code_rejected(client, admin):
    issue_enrolment_code(admin)
    r = client.post('/api/auth/webauthn/register/begin', json=creds(enrolment_code='AAAA-BBBB-CCCC'))
    assert r.status_code == 401


def test_enrolment_needs_password(client, admin):
    code = issue_enrolment_code(admin)
    r = client.post('/api/auth/webauthn/register/begin',
                    json={'username': 'admin', 'password': 'wrong-password', 'enrolment_code': code})
    assert r.status_code == 401


def test_voter_cannot_enrol(client, app):
    voter = User('voter', 'voter@example.test', PASSWORD, C.ROLE_VOTER)
    db.session.add(voter)
    db.session.commit()
    code = issue_enrolment_code(voter)
    r = client.post('/api/auth/webauthn/register/begin', json=creds('voter', enrolment_code=code))
    assert r.status_code == 403


def test_registration_from_wrong_origin_rejected(client, admin, key):
    r = enrol(client, admin, key, origin='https://evil.example')
    assert r.status_code == 400
    assert WebAuthnCredential.query.count() == 0


def test_enrolment_code_stored_hashed(admin):
    code = issue_enrolment_code(admin)
    stored = EnrolmentCode.query.one().code_hash
    assert code.replace('-', '') not in stored
    assert len(stored) == 64


def test_cli_issues_enrolment_code(app, admin):
    result = app.test_cli_runner().invoke(args=['issue-enrolment-code', 'admin'])
    assert result.exit_code == 0, result.output
    assert 'Enrolment code for admin:' in result.output
    assert EnrolmentCode.query.filter_by(user_id=admin.id, used_at=None).count() == 1


def test_cli_refuses_voter(app):
    db.session.add(User('voter', 'voter@example.test', PASSWORD, C.ROLE_VOTER))
    db.session.commit()
    result = app.test_cli_runner().invoke(args=['issue-enrolment-code', 'voter'])
    assert result.exit_code != 0
