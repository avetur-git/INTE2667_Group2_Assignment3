"""
REQ-11 - Role-Based Access Control (RBAC)
=========================================

This module is the ONE place where the platform decides who may do what.

    user  --has-->  role  --grants-->  permissions  --protect-->  routes

Routes never name roles. They ask for a permission
(``@permission_required(P.CANDIDATE_MANAGE)``) and this module answers from
ROLE_PERMISSIONS. Changing what a role can do means editing one table here,
not hunting through every route file. This is the prototype version of the
central IAM platform chosen in Part Two (Option B).

Security properties:
  * Deny by default - a role that is not in the table, or a permission that a
    role was not given, is refused.
  * Least privilege - each role gets only the permissions its job needs.
  * Checked on the server for every request - the role is read from the
    database each time, never trusted from the browser or the login token.
"""
from app.utils import constants as C


class P:
    """Every permission in the system. Using these names (not loose strings)
    means a typo fails loudly instead of silently locking a route."""
    # A voter acting on their own record and ballot
    OWN_ENROLMENT_READ = 'own_enrolment:read'
    OWN_ENROLMENT_WRITE = 'own_enrolment:write'
    BALLOT_CAST = 'ballot:cast'

    # The electoral roll (AEC officers)
    VOTER_REGISTER = 'voter:register'
    VOTER_READ = 'voter:read'
    VOTER_STATUS_CHANGE = 'voter:status_change'
    TURNOUT_READ = 'turnout:read'

    # Candidates (Commissioner's delegates)
    CANDIDATE_MANAGE = 'candidate:manage'
    CANDIDATE_EXCLUDE = 'candidate:exclude'

    # Running the election (election admins)
    ELECTION_MANAGE = 'election:manage'
    ELECTION_TALLY = 'election:tally'
    RESULTS_DETAIL_READ = 'results:detail_read'
    RESULTS_EXPORT = 'results:export'
    TERMINAL_MANAGE = 'terminal:manage'
    STAFF_MANAGE = 'staff:manage'
    AUDIT_READ = 'audit:read'


ALL_PERMISSIONS = frozenset(v for k, v in vars(P).items() if not k.startswith('_'))


ROLE_PERMISSIONS = {
    C.ROLE_VOTER: frozenset({
        P.OWN_ENROLMENT_READ,
        P.OWN_ENROLMENT_WRITE,
        P.BALLOT_CAST,
    }),
    C.ROLE_AEC_OFFICER: frozenset({
        P.VOTER_REGISTER,
        P.VOTER_READ,
        P.VOTER_STATUS_CHANGE,
        P.TURNOUT_READ,
    }),
    C.ROLE_DELEGATE: frozenset({
        P.CANDIDATE_MANAGE,
        P.CANDIDATE_EXCLUDE,
    }),
    C.ROLE_ADMIN: frozenset({
        P.VOTER_REGISTER,
        P.VOTER_READ,
        P.VOTER_STATUS_CHANGE,
        P.TURNOUT_READ,
        P.ELECTION_MANAGE,
        P.ELECTION_TALLY,
        P.RESULTS_DETAIL_READ,
        P.RESULTS_EXPORT,
        P.TERMINAL_MANAGE,
        P.STAFF_MANAGE,
        P.AUDIT_READ,
    }),
}

# Fail at start-up if the table names a permission that does not exist.
for _role, _perms in ROLE_PERMISSIONS.items():
    _unknown = _perms - ALL_PERMISSIONS
    if _unknown:
        raise ValueError(f'Role {_role!r} has unknown permissions: {sorted(_unknown)}')


def permissions_for(role):
    """All permissions a role holds. Unknown role -> empty set (deny by default)."""
    return ROLE_PERMISSIONS.get(role, frozenset())


def has_permission(user, permission):
    """True only for an active user whose role grants this permission."""
    if user is None or not user.is_active:
        return False
    return permission in permissions_for(user.role)
