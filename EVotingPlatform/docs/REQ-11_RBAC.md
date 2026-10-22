# REQ-11: Role-based access control

Solution implemented: Option B (one central place that defines every role and
what it may do, shared by every part of the system).

## What was built

- **One permission table.** `app/security/rbac.py` holds the whole access policy:
  the list of permissions (class `P`, for example `P.CANDIDATE_MANAGE`) and which
  roles hold each one (`ROLE_PERMISSIONS`). Changing what a role can do means
  editing this one table, which goes through a reviewed pull request.
- **One decorator on every protected route.** Routes never name roles. They ask for
  a permission:

  ```python
  @candidates_bp.route('/', methods=['POST'])
  @permission_required(P.CANDIDATE_MANAGE)
  def add_candidate(current_user): ...
  ```

- **Roles and their permissions:**
  - `voter`: read and update their own enrolment, cast a ballot.
  - `aec_officer`: register voters, read the roll, approve or remove enrolments, see turnout.
  - `commissioner_delegate`: manage candidates, exclude candidates for a recount, read candidate history.
  - `election_admin`: run elections and the tally, results, terminals, missions, staff accounts, the audit log, plus the roll permissions.
- Other requirements have already extended the table without changing the
  mechanism, for example `CANDIDATE_HISTORY_READ` (REQ-17) and `MISSION_MANAGE` (REQ-19).

## Security properties

- **Deny by default.** A role that is not in the table, or a permission a role
  was not given, is refused (403). Forgetting to grant something fails safe.
- **Least privilege.** Each role holds only what its job needs. Voters hold three
  permissions; no staff role can cast a ballot.
- **Checked on the server, every request.** The role is read from the database
  each time, not from the login token or the browser. A role change or a disabled
  account takes effect on the very next request, even with a token issued earlier.
- **Authentication vs authorisation.** 401 means "who are you?" (not logged in,
  or the account is disabled). 403 means "you are logged in but not allowed".
- **Every refusal is logged** as `ACCESS_DENIED`, with the role and the
  permission that was missing (REQ-15 fields).
- **Typos fail at start-up.** A misspelt permission name stops the app from
  starting, instead of silently locking a route.
- **No route slips through.** A test fails if any route is neither protected nor
  listed as deliberately public.

## Tests

`tests/test_rbac.py` (9 tests):

- `test_role_permission_matrix`: every role against every protected route
  (27 routes × 4 roles); each request is allowed exactly when the table says so.
- `test_not_logged_in_is_rejected`: every protected route returns 401 without a token.
- `test_unknown_role_is_denied_everything`: deny by default.
- `test_role_change_applies_to_existing_token`: a demoted officer's old token stops working at once.
- `test_disabled_account_is_rejected`
- `test_denied_access_is_logged`
- `test_every_route_is_public_by_design_or_protected`: guard for new routes.
- `test_misspelt_permission_fails_at_startup`
- `test_least_privilege_policy`: pins the policy itself (only voters cast, only
  delegates manage candidates, only admins read the audit log or manage staff),
  so loosening the table is a visible, failing change.

The tests were checked by breaking the code on purpose: wiring a route to the
wrong permission, removing a decorator, making the check always allow, letting
an unknown role fall back to voter, and giving voters `audit:read`. A test
failed in every case.

## Simulated vs real

The prototype keeps the role table in code. Part Two's full solution is a central
identity and access management (IAM) platform with single sign-on, where roles are
managed outside the application and shared by every system. Moving to it would
replace the table lookup in `permissions_for()`; routes would not change.

## Assumptions and limitations

1. One role per user. Someone who is both an officer and a voter needs two accounts.
2. Changing a role's permissions needs a code change and a pull request. This is
   slower than an admin screen, but every change is reviewed and recorded in Git (REQ-06).
3. The staff page hides sections by the role stored in the browser. That is for
   usability only; the server-side check above is the real control.
4. The election admin holds both roll permissions (`voter:status_change`) and
   tally permissions (`election:tally`), as in the base build. One stolen admin
   login could therefore change the roll and run the count. Splitting these is a
   possible separation-of-duties improvement; REQ-18 adds dual approval for the
   most sensitive actions.
5. The admin row lists `TERMINAL_MANAGE` and `STAFF_MANAGE` twice. This has no
   effect (it is a set), but should be tidied.
