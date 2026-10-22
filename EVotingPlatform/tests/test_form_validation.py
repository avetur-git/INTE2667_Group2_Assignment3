"""Server-side validation for the admin forms (and voter self-registration).

The browser's required/pattern checks can be skipped by calling the API
directly, so these tests send the requests the forms send - with bad values."""
from datetime import date, timedelta

import pytest

from app.models import Division, Election, Terminal, User, Voter
from app.seed import seed_demo
from app.utils import constants as C
from tests.test_api_flow import login

GOOD_STAFF = {'username': 'new.officer', 'email': 'new.officer@example.test',
              'password': 'Officer2026pass', 'role': C.ROLE_AEC_OFFICER}


@pytest.fixture()
def admin(client, app):
    seed_demo()
    return login(client, 'admin')


@pytest.fixture()
def citizen(app, admin):
    """A valid 'Register a citizen' body for a VIC division."""
    division = Division.query.filter_by(state='VIC').first()
    return {'first_name': 'Ann', 'last_name': 'Lee', 'date_of_birth': '1990-01-01',
            'address_line': '1 Test St', 'suburb': 'Carlton', 'state': 'VIC',
            'postcode': '3053', 'division_id': division.id}


def error_of(response, status=400):
    assert response.status_code == status, response.get_json()
    return response.get_json()['error']


# ------------------------------------------------------- Create staff account

def test_valid_staff_account_is_created(client, admin):
    assert client.post('/api/admin/users', json=GOOD_STAFF, headers=admin).status_code == 201


@pytest.mark.parametrize('password, message', [
    ('a', 'at least 12 characters'),
    ('Short1pass', 'at least 12 characters'),          # 10 characters: fine for voters, not staff
    ('allletterslongenough', 'letter and one number'),
    ('123456789012345', 'letter and one number'),
])
def test_weak_staff_password_rejected(client, admin, password, message):
    error = error_of(client.post('/api/admin/users', json={**GOOD_STAFF, 'password': password}, headers=admin))
    assert message in error
    assert User.query.filter_by(username=GOOD_STAFF['username']).count() == 0


def test_staff_password_cannot_be_the_username(client, admin):
    body = {**GOOD_STAFF, 'username': 'officer2026xyz', 'password': 'OFFICER2026XYZ'}
    assert 'same as the username' in error_of(client.post('/api/admin/users', json=body, headers=admin))


@pytest.mark.parametrize('email', ['not-an-email', 'a@b', 'two@@example.test', 'x' * 120 + '@example.test'])
def test_invalid_staff_email_rejected(client, admin, email):
    assert 'valid email' in error_of(client.post('/api/admin/users', json={**GOOD_STAFF, 'email': email}, headers=admin))


@pytest.mark.parametrize('username', [' ', 'ab', 'x' * 81, 'has space', 'semi;colon', '<script>'])
def test_invalid_staff_username_rejected(client, admin, username):
    error = error_of(client.post('/api/admin/users', json={**GOOD_STAFF, 'username': username}, headers=admin))
    assert 'Username must be' in error


def test_staff_email_is_normalised(client, admin):
    body = {**GOOD_STAFF, 'email': '  New.Officer@Example.TEST '}
    assert client.post('/api/admin/users', json=body, headers=admin).status_code == 201
    assert User.query.filter_by(username='new.officer').one().email == 'new.officer@example.test'


# ---------------------------------------------------------- Register a citizen

def test_valid_citizen_is_registered(client, admin, citizen):
    assert client.post('/api/voters/', json=citizen, headers=admin).status_code == 201


@pytest.mark.parametrize('postcode', ['ABCD', '123', '12345', '30 53', ''])
def test_invalid_postcode_rejected(client, admin, citizen, postcode):
    r = client.post('/api/voters/', json={**citizen, 'postcode': postcode}, headers=admin)
    assert r.status_code == 400


def test_future_date_of_birth_rejected(client, admin, citizen):
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    for dob in (tomorrow, date.today().isoformat(), '1899-12-31'):
        r = client.post('/api/voters/', json={**citizen, 'date_of_birth': dob}, headers=admin)
        assert r.status_code == 400, dob


@pytest.mark.parametrize('field', ['first_name', 'last_name', 'address_line', 'suburb'])
def test_whitespace_only_text_rejected(client, admin, citizen, field):
    assert 'required' in error_of(client.post('/api/voters/', json={**citizen, field: '   '}, headers=admin))


def test_overlong_and_control_characters_rejected(client, admin, citizen):
    assert 'at most 80' in error_of(client.post('/api/voters/', json={**citizen, 'first_name': 'A' * 81}, headers=admin))
    assert 'invalid characters' in error_of(
        client.post('/api/voters/', json={**citizen, 'last_name': 'Lee\x00\n'}, headers=admin))


def test_citizen_login_needs_valid_details(client, admin, citizen):
    login_details = {'username': 'ann.lee', 'email': 'ann@example.test', 'password': 'Voter2026'}
    for bad in ({'password': 'a'}, {'password': 'abcdefgh'}, {'email': 'nope'}, {'username': ' '}):
        r = client.post('/api/voters/', json={**citizen, **login_details, **bad}, headers=admin)
        assert r.status_code == 400, bad
    assert Voter.query.filter_by(first_name='Ann').count() == 0       # nothing half-created
    assert client.post('/api/voters/', json={**citizen, **login_details}, headers=admin).status_code == 201


def test_voter_self_enrolment_uses_same_rules(client, app, citizen):
    r = client.post('/api/auth/register', json={'username': 'selfie', 'email': 's@example.test',
                                                'password': 'Selfie2026'})
    headers = {'Authorization': f"Bearer {r.get_json()['access_token']}"}
    assert client.post('/api/voters/enrol', json={**citizen, 'postcode': 'XXXX'}, headers=headers).status_code == 400


# ------------------------------------------------------- Elections and terminals

def test_election_in_the_past_rejected(client, admin):
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    assert 'past' in error_of(client.post('/api/elections/', json={'name': 'Old', 'election_date': yesterday},
                                          headers=admin))
    today = date.today().isoformat()
    assert client.post('/api/elections/', json={'name': 'Today', 'election_date': today}, headers=admin).status_code == 201


def test_blank_or_overlong_election_name_rejected(client, admin):
    for name in ('   ', 'E' * 151):
        r = client.post('/api/elections/', json={'name': name, 'election_date': '2030-01-01'}, headers=admin)
        assert r.status_code == 400, name
    assert Election.query.filter(Election.name.in_(['   ', 'E' * 151])).count() == 0


@pytest.mark.parametrize('terminal_id', ['   ', 'PS', 'PS 0099', 'PS_0099', 'X' * 51, "PS-1'; --"])
def test_invalid_terminal_id_rejected(client, admin, terminal_id):
    r = client.post('/api/terminals/', json={'terminal_id': terminal_id, 'channel': 'polling_station',
                                             'location_name': 'Town Hall'}, headers=admin)
    assert r.status_code == 400


def test_valid_terminal_added_and_location_checked(client, admin):
    body = {'terminal_id': 'PS-0099', 'channel': 'polling_station', 'location_name': '  Town Hall  '}
    assert client.post('/api/terminals/', json={**body, 'location_name': '   '}, headers=admin).status_code == 400
    assert client.post('/api/terminals/', json=body, headers=admin).status_code == 201
    assert Terminal.query.filter_by(terminal_id='PS-0099').one().location_name == 'Town Hall'


# ------------------------------------------------------------ voter self-signup

@pytest.mark.parametrize('body', [
    {'username': 'okname', 'email': 'ok@example.test', 'password': 'abc123'},      # too short
    {'username': 'okname', 'email': 'not-an-email', 'password': 'Longer2026'},
    {'username': 'x', 'email': 'ok@example.test', 'password': 'Longer2026'},
])
def test_self_registration_rules(client, app, body):
    assert client.post('/api/auth/register', json=body).status_code == 400


# ------------------------------------------------------------------- demo data

def test_demo_data_has_divisions_and_candidates_in_every_state(app):
    seed_demo()
    for state in C.STATES:
        divisions = Division.query.filter_by(state=state).all()
        assert divisions, f'no division in {state}'
        from app.models import Candidate
        assert Candidate.query.filter_by(contest_type=C.CONTEST_SENATE, state=state).count() > 0, state
        assert Candidate.query.filter(Candidate.division_id.in_([d.id for d in divisions])).count() > 0, state


# ------------------------------------------------------- stale or broken sessions

@pytest.mark.parametrize('header', ['Bearer stale.token.from-an-old-database', 'Bearer abc', 'Basic xyz'])
def test_bad_login_token_is_401_with_error_message(client, app, header):
    r = client.get('/api/voters/me', headers={'Authorization': header})
    assert r.status_code == 401
    assert 'log in' in r.get_json()['error'].lower()


def test_token_signed_with_another_secret_is_401(client, app):
    from flask_jwt_extended import create_access_token
    app.config['JWT_SECRET_KEY'], real = 'some-other-secret-0123456789abcdefghij', app.config['JWT_SECRET_KEY']
    with app.test_request_context():
        forged = create_access_token(identity='1')
    app.config['JWT_SECRET_KEY'] = real
    r = client.get('/api/voters/me', headers={'Authorization': f'Bearer {forged}'})
    assert r.status_code == 401 and 'error' in r.get_json()


def test_missing_token_is_401_with_error_message(client, app):
    r = client.get('/api/voters/me')
    assert r.status_code == 401 and r.get_json()['error'] == 'Please log in to continue.'
