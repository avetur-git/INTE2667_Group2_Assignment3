# REQ-15: Full details of every privileged action

Every action by a staff account (`aec_officer`, `commissioner_delegate`,
`election_admin`) is recorded by one shared decorator, `@audited`, in one fixed
format. Routes no longer write audit entries themselves.

## The six fields

| Field | Column(s) | Where it comes from |
|---|---|---|
| Who | `user_id`, `username` | The logged-in user, from `@permission_required` |
| What | `action` | Named on the decorator, or set by the route for a specific outcome |
| On what | `object_type`, `object_id` | The decorator, the URL argument, or `audit.target(...)` |
| When | `created_at` | Set when the row is written |
| Where | `ip_address`, `user_agent`, `session_id` | The request and the JWT id |
| Outcome | `success` | `False` for any 4xx/5xx response or a crash |

`@audited` requires an action and an object type, so an entry can never be written
without them. The other fields are filled in by the decorator, not by each route.

## Using it on a new staff route

```python
from app.security.audit_trail import audit, audited

@bp.route('/<int:election_id>/status', methods=['PUT'])
@permission_required(P.ELECTION_MANAGE)
@audited('ELECTION_STATUS_CHANGED', object_type='election')
def set_status(current_user, election_id):
    ...
    audit.details.update({'from': old, 'to': new_status})
    return jsonify(...), 200
```

- **Order matters:** put `@audited` directly under `@permission_required`. A refused
  request never reaches it, because `permission_required` already logs it as
  `ACCESS_DENIED`. Putting `@audited` above it raises an error.
- **Object id:** taken from the URL argument `<object_type>_id`. Pass `id_arg=`
  when the URL uses another name (the approval routes use `request_id`).
- **What a route can add:**
  - `audit.target(id)`: the id of something it just created
  - `audit.action = '...'`: a more specific outcome, e.g. `APPROVAL_REQUEST_REFUSED`
  - `audit.details[...]`: extra detail
  - `audit.also(action, object_type, id, details)`: a second object changed by
    the same request

## What is enforced

- **No unaudited staff routes.** `create_app` calls
  `check_privileged_routes_audited`, which refuses to start the app if any route
  that needs a staff permission lacks `@audited`. `test_every_privileged_route_is_audited`
  checks the same thing in CI.
- **Failures are logged.** A 4xx/5xx response is recorded with `success = False`,
  the status code and the error message. A crash is recorded with the exception
  type and then raised as normal.
- **No half-saved work.** On a failure, uncommitted database changes are rolled
  back before the audit row is written, so the audit write cannot save them by
  accident.
- **Reads are audited too.** Viewing the electoral roll, the audit log, detailed
  results, turnout, terminals, missions, candidate history and approval requests
  all create entries. Voter roll searches record that a name search happened, but
  not the name.

## One request, several records

Approving a candidate exclusion writes three entries, in this order:

1. `APPROVAL_REQUEST_APPROVED` on the approval request
2. `APPROVAL_REQUEST_EXECUTED` on the approval request
3. `CANDIDATE_EXCLUDED` on the candidate

That keeps both audit trails complete: REQ-18 reads the approval request's trail,
and REQ-17 reads the candidate's. All three come from `@audited`, with the same
user, session and timestamp source.

## Out of scope (still use `log_action` directly)

| Event | Why it is not under `@audited` |
|---|---|
| `LOGIN_FAILED`, `PRIVILEGED_LOGIN`, `MFA_FAILED`, WebAuthn enrolment | Happen before login, so there is no staff route yet |
| `ACCESS_DENIED` | Written by `permission_required` before the route runs |
| `VOTE_ACCESS_REFUSED` (REQ-19) | Part of the voter's voting path |
| `VOTER_ADDRESS_UPDATED`, `CAST_BALLOT` | The voter's own actions, not privileged ones |
| `ENROLMENT_CODE_ISSUED` | Run from the server command line |

Voter actions are deliberately left out. Recording who called `/api/voting/cast`
next to ballot data would weaken REQ-01's separation of voter identity from ballots.

## Tests

`tests/test_privileged_audit.py` covers the acceptance tests from issue #20
(`test_audit_entry_has_all_fields`, `test_failed_action_logged`,
`test_unaudited_admin_endpoint_rejected`). It also covers:

- rollback of unsaved changes on failure, and crashes being logged
- second-object records, and a refused request logged once (`ACCESS_DENIED` only)
- searched names not being stored
- the approval flows: three-entry approval, self-approval, expiry
- every staff route having `@audited`

## Limitations

- **Integrity is not part of REQ-15.** Entries are still plain rows in
  `audit_logs`. Tamper-evidence (REQ-04) and write-once storage (REQ-05) plug into
  the `write_audit_record` hook, which every `@audited` entry goes through.
- **The audit write is a separate commit after the route's own commit.** If that
  write fails, the action has already happened without its record. Making them one
  transaction would need every route to stop committing itself.
