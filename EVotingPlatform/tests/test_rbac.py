"""REQ-11 - Role-Based Access Control tests.

These prove four things:
  1. The role-permission matrix is enforced exactly, for every protected route.
  2. Anything not in the table is denied (unknown role, disabled account).
  3. A role change takes effect at once, even for a token issued earlier.
  4. Every route is either deliberately public or protected - none slip through.
"""
import pytest

from app import db
from app.models import AuditLog, User
from app.security.rbac import P, ROLE_PERMISSIONS, permission_required
from app.seed import seed_demo
from app.utils import constants as C
from tests.test_api_flow import login

ROLES = {'voter1': C.ROLE_VOTER, 'officer': C.ROLE_AEC_OFFICER,
         'delegate': C.ROLE_DELEGATE, 'admin': C.ROLE_ADMIN}

# (method, url, permission it needs). IDs that do not exist (9999) mean an
# allowed role gets 404/400/409 rather than changing demo data - all we test
# here is whether the request got PAST the access check.
PROTECTED = [
    ('GET',  '/api/voting/ballot-paper?election_id={eid}', P.BALLOT_CAST),
    ('POST', '/api/voting/cast',                           P.BALLOT_CAST),
    ('GET',  '/api/voters/me',                             P.OWN_ENROLMENT_READ),
    ('GET',  '/api/voters/eligibility?election_id={eid}',  P.OWN_ENROLMENT_READ),
    ('POST', '/api/voters/enrol',                          P.OWN_ENROLMENT_WRITE),
    ('POST', '/api/voters/',                               P.VOTER_REGISTER),
    ('GET',  '/api/voters/',                               P.VOTER_READ),
    ('PUT',  '/api/voters/9999/approve',                   P.VOTER_STATUS_CHANGE),
    ('PUT',  '/api/voters/9999/remove',                    P.VOTER_STATUS_CHANGE),
    ('GET',  '/api/elections/{eid}/turnout',               P.TURNOUT_READ),
    ('POST', '/api/elections/',                            P.ELECTION_MANAGE),
    ('PUT',  '/api/elections/{eid}/status',                P.ELECTION_MANAGE),
    ('POST', '/api/elections/{eid}/tally',                 P.ELECTION_TALLY),
    ('GET',  '/api/results/{eid}/detail',                  P.RESULTS_DETAIL_READ),
    ('GET',  '/api/results/{eid}/export',                  P.RESULTS_EXPORT),
    ('POST', '/api/candidates/',                           P.CANDIDATE_MANAGE),
    ('PUT',  '/api/candidates/9999',                       P.CANDIDATE_MANAGE),
    ('POST', '/api/candidates/9999/exclude',               P.CANDIDATE_EXCLUDE),
    ('GET',  '/api/terminals/',                            P.TERMINAL_MANAGE),
    ('POST', '/api/terminals/',                            P.TERMINAL_MANAGE),
    ('PUT',  '/api/terminals/NOPE/deactivate',             P.TERMINAL_MANAGE),
    ('POST', '/api/admin/users',                           P.STAFF_MANAGE),
    ('GET',  '/api/admin/audit-logs',                      P.AUDIT_READ),
]

# Routes that are public on purpose (login, public results, lists needed by
# the enrolment form, terminal heartbeat) or need login but no permission.
PUBLIC_ENDPOINTS = {
    'static', 'main.index', 'main.login_page', 'main.register_page', 'main.dashboard',
    'main.vote_page', 'main.staff_page', 'main.results_page', 'main.health_check',
    'auth.register', 'auth.login',
    'elections.list_divisions', 'elections.list_elections', 'elections.get_election',
    'candidates.list_candidates', 'results.public_results', 'terminals.heartbeat',
}
LOGIN_ONLY_ENDPOINTS = {'auth.get_profile', 'auth.logout'}


def call(client, method, url, headers=None):
    return client.open(url, method=method, json={}, headers=headers or {})


def was_denied(response):
    """Refused by RBAC - not some other 403 such as 'not eligible to vote'."""
    return (response.status_code == C.HTTP_FORBIDDEN
            and response.get_json().get('error') == C.ERROR_ACCESS_DENIED)


@pytest.fixture()
def seeded(client, app):
    eid = seed_demo()['election_id']
    tokens = {name: login(client, name) for name in ROLES}
    return eid, tokens


def test_role_permission_matrix(client, seeded):
    """Every role x every protected route: allowed exactly when the table says so."""
    eid, tokens = seeded
    for method, url, permission in PROTECTED:
        for username, role in ROLES.items():
            r = call(client, method, url.format(eid=eid), tokens[username])
            expected_allowed = permission in ROLE_PERMISSIONS[role]
            assert was_denied(r) is not expected_allowed, \
                f'{role} {method} {url}: got {r.status_code} {r.get_json()}'


def test_not_logged_in_is_rejected(client, seeded):
    eid, _ = seeded
    for method, url, _ in PROTECTED:
        assert call(client, method, url.format(eid=eid)).status_code == C.HTTP_UNAUTHORIZED, url


def test_unknown_role_is_denied_everything(client, seeded):
    """Deny by default: a role missing from the table gets no permissions."""
    eid, tokens = seeded
    db.session.query(User).filter_by(username='admin').update({'role': 'superuser'})
    db.session.commit()
    for method, url, _ in PROTECTED:
        assert was_denied(call(client, method, url.format(eid=eid), tokens['admin'])), url


def test_role_change_applies_to_existing_token(client, seeded):
    """The role is read from the database on every request, not from the token."""
    eid, tokens = seeded
    officer = tokens['officer']
    assert client.get('/api/voters/', headers=officer).status_code == C.HTTP_OK
    db.session.query(User).filter_by(username='officer').update({'role': C.ROLE_VOTER})
    db.session.commit()
    assert was_denied(client.get('/api/voters/', headers=officer))


def test_disabled_account_is_rejected(client, seeded):
    _, tokens = seeded
    db.session.query(User).filter_by(username='admin').update({'is_active': False})
    db.session.commit()
    r = client.get('/api/admin/audit-logs', headers=tokens['admin'])
    assert r.status_code == C.HTTP_UNAUTHORIZED


def test_denied_access_is_logged(client, seeded):
    _, tokens = seeded
    client.get('/api/admin/audit-logs', headers=tokens['voter1'])
    entry = AuditLog.query.filter_by(action='ACCESS_DENIED').one()
    assert entry.username == 'voter1' and entry.success is False
    assert entry.details == {'role': C.ROLE_VOTER, 'permission': P.AUDIT_READ}


def test_every_route_is_public_by_design_or_protected(app):
    """A new route added without a permission makes this test fail."""
    for rule in app.url_map.iter_rules():
        if rule.endpoint in PUBLIC_ENDPOINTS | LOGIN_ONLY_ENDPOINTS:
            continue
        view = app.view_functions[rule.endpoint]
        assert getattr(view, 'required_permission', None), \
            f'{rule.rule} ({rule.endpoint}) has no permission and is not listed as public'


def test_misspelt_permission_fails_at_startup():
    with pytest.raises(ValueError):
        permission_required('candidate:mange')


def test_least_privilege_policy():
    """Pins the policy itself, so loosening the table is a visible, failing change.

    - Only voters can cast a ballot; no staff role can.
    - Voters hold nothing beyond their own record and ballot.
    - Candidate management belongs to delegates only (REQ-17).
    - Delegates cannot touch the electoral roll; officers cannot touch candidates.
    - Only election admins can read the audit log or manage staff accounts."""
    def holders(permission):
        return {role for role, perms in ROLE_PERMISSIONS.items() if permission in perms}

    assert holders(P.BALLOT_CAST) == {C.ROLE_VOTER}
    assert ROLE_PERMISSIONS[C.ROLE_VOTER] == {P.OWN_ENROLMENT_READ, P.OWN_ENROLMENT_WRITE, P.BALLOT_CAST}
    assert holders(P.CANDIDATE_MANAGE) == holders(P.CANDIDATE_EXCLUDE) == {C.ROLE_DELEGATE}
    assert not ROLE_PERMISSIONS[C.ROLE_DELEGATE] & {P.VOTER_REGISTER, P.VOTER_READ, P.VOTER_STATUS_CHANGE}
    assert holders(P.AUDIT_READ) == holders(P.STAFF_MANAGE) == {C.ROLE_ADMIN}
