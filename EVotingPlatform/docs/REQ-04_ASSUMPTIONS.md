# REQ-04 assumptions and limits: tamper-evident audit log

## What is implemented
- Every audit entry is stored with `seq` (1, 2, 3 ... no gaps), `prev_hash` (the hash of the entry before it,
  64 zeros for the first) and `entry_hash` (HMAC-SHA256 over `prev_hash` and all fields of the entry,
  including its timestamp).
- Editing an entry breaks its own hash. Deleting, inserting or reordering entries breaks the sequence or the link.
- `flask --app run verify-audit-chain` checks the whole log, lists each problem by audit entry id and exits with
  code 1 if anything is wrong, so it can be run on a schedule and trigger an alert.
- It is the `write_audit_record` hook (`app/security/audit_chain.py`), so every privileged action that is logged
  through `log_action` (voter-record changes, candidate changes, election configuration changes) is chained.

## Assumptions
1. All privileged actions are recorded through `log_action`, which calls the `write_audit_record` hook (REQ-15).
   A row added any other way has no hash and is reported as "entry has no hash".
2. The chain key (`AUDIT_CHAIN_KEY`) is kept outside the database. In development it falls back to `SECRET_KEY`.
   In production it would be a separate secret held in an HSM or secrets manager. Anyone who can edit the database
   but does not have the key cannot recompute valid hashes.
3. REQ-05 (append-only triggers and the write-once archive) prevents edits in the first place. REQ-04 makes an edit
   visible even if the triggers are dropped by someone with database access. The tests do exactly that.
4. Sequence numbers are unique in the database. If two requests try to take the same number, the loser retries
   (up to 5 times), so entries are never silently skipped.
5. REQ-04 is registered before REQ-05 in `create_app`, because REQ-05 wraps whatever `write_audit_record` hook is
   registered. The hook is reset before it is registered, so creating the app many times never stacks hooks.
6. Databases created before REQ-04 have no chain columns. The prototype recreates the database. Old rows without a
   hash would be reported as unhashed.

## Known limits
- Deleting the newest entries is not visible to the chain alone, because nothing after them refers to them. The
  REQ-05 triggers block it, and the REQ-05 archive comparison (`verify-audit-archive`) reports missing rows.
  Signed checkpoints, as used for ballots in REQ-02, would close this gap.
- Someone who has both the database and the chain key can rewrite the whole chain, so the key must be protected.
- The selected design (Part Two, option B) is a SIEM with hash-chained, signed entries. The prototype uses a keyed
  hash (HMAC) instead of a digital signature, so verification needs the secret key. The REQ-05 write-once archive
  stands in for the SIEM, and it now also stores the hashes.
- Verification reads the whole table. That is fine for the prototype. A production system would verify in ranges
  and on a schedule.
- Under very heavy load the retry on a sequence clash adds a little delay. Production would use a single writer or a
  database sequence.