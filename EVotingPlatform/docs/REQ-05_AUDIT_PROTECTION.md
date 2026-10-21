# REQ-05: Protect audit logs from modification or deletion

Audit entries are protected in two layers. The database refuses to change or
remove them, and a copy of every entry is locked in write-once (WORM) storage
that nobody can delete before the retention date, including the storage
administrator.

## Layer 1: append-only audit table

Triggers on `audit_logs` reject every `UPDATE` and `DELETE`:

```
audit_logs_no_update  BEFORE UPDATE ON audit_logs  -> RAISE(ABORT, 'audit_logs is append-only (REQ-05)')
audit_logs_no_delete  BEFORE DELETE ON audit_logs  -> RAISE(ABORT, ...)
```

- **Created on every start** (`CREATE TRIGGER IF NOT EXISTS`), so databases made
  before REQ-05 get them too. No migration is needed.
- **Production (Postgres):** the same rule is `POSTGRES_TRIGGERS` in
  `app/security/audit_protection.py`. The schema owner applies it as a migration,
  because the app's role cannot create triggers. That role also has only `INSERT`
  and `SELECT` on `audit_logs` (REQ-06, `infra/db_users.tf`), so the two controls
  back each other up.
- **Limit:** anyone with direct access to the database file, or the schema owner,
  can drop a trigger. Layer 2 exists for exactly that case.

## Layer 2: write-once archive

```
log_action -> write_audit_record hook
                |-- existing function (plain row today, REQ-04's chained entry later)
                '-- REQ-05: when a full batch is waiting, upload it to MinIO
                       bucket "audit-archive": object lock, COMPLIANCE mode
```

- **Batches:** every `AUDIT_ARCHIVE_BATCH_SIZE` entries (default 20) are written
  as one object, `audit-logs/<first id>-<last id>-<sha256 prefix>.jsonl`, with one
  JSON entry per line and keys sorted. `audit_archive_batches` records each batch:
  id range, object key, version id, SHA-256 and lock date.
- **Compliance-mode retention** (default 1 day, `AUDIT_ARCHIVE_RETENTION_SECONDS`):
  until that date the stored version cannot be deleted, and its lock cannot be
  shortened or removed, by any user including MinIO root.
- **Least privilege:**

  | User | Can | Cannot |
  |---|---|---|
  | `audit-writer` (the app) | upload objects, set their retention | read, list, delete |
  | `audit-reader` | read and list, for `verify-audit-archive` | upload, delete |
  | MinIO root | everything the bucket allows | used only by the one-off setup job, never given to the app |

- **Wraps, does not replace, the hook.** REQ-05 calls whatever `write_audit_record`
  function is registered, then archives. REQ-04 (#9) can therefore add its
  hash-chained entries before or after this, and its hash fields will be archived
  automatically. The wrapper is applied once (marked `_req05`) and registered last
  in `create_app`.
- **Never blocks the audited action.** If MinIO is down, the entry is still
  written, the error is logged, and the next batch picks it up.
- **Off unless configured:** archiving runs only when `AUDIT_ARCHIVE_ENDPOINT` is
  set, so it doesn't change anyone else's local setup.

### What "cannot be overwritten" means here

The bucket uses S3 versioning, which object lock requires. Uploading to an
existing key does not replace the locked object; it adds a newer version on top.
Deleting without a version id only adds a delete marker. In both cases the
original locked version stays byte-for-byte unchanged and readable by its version
id, which `audit_archive_batches.version_id` records. The tests check exactly this.

## Checking the archive against the database

```
flask --app run verify-audit-archive
```

For every batch, this reads the locked version (as `audit-reader`), checks it
against the recorded SHA-256, and compares it with the current `audit_logs` rows.
It reports any row that has been **changed** or is **missing**, for example after
someone with file access dropped the triggers. It exits non-zero if anything
differs. `flask --app run archive-audit-logs` archives a partial batch straight away.

## Running it

**Docker stack:** `docker compose up -d --build` starts:
- `minio`: built from source, S3 API on `localhost:9100`, console on `localhost:9091`
- `minio-setup`: a one-off job that creates the locked bucket and the two users
- `web`: the app, given only the writer and reader credentials

Change the `change-me-*` passwords through environment variables before any real use.

**Why MinIO is built from source:** MinIO no longer publishes official Docker
images. `docker/minio/Dockerfile` builds it from GitHub at a pinned release
(`RELEASE.2025-10-15T17-29-55Z`), checks the commit hash
(`9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a`), and fails if the tag has moved. This
avoids trusting a third-party image for the audit trail. The first build takes a
few minutes.

## Tests

`tests/test_audit_protection.py`:

| Test | Needs MinIO |
|---|---|
| `test_audit_table_rejects_update`, `test_audit_table_rejects_delete` (ORM, bulk and raw SQL) | no |
| new entries still accepted; triggers added to an older database | no |
| hook wrapped only once; archive off by default; archive failure never blocks the action | no |
| `test_entries_archived_in_locked_batches` (SHA-256, COMPLIANCE mode, lock date) | yes |
| `test_locked_object_cannot_be_deleted` (even by root) | yes |
| `test_locked_object_cannot_be_overwritten` (original version unchanged, lock cannot be shortened) | yes |
| writer cannot read, list or delete; partial batch flush; verify passes; verify detects changed and deleted rows | yes |

The MinIO tests run when `REQ05_MINIO_ENDPOINT` is set and skip otherwise. CI
does not run MinIO, so they skip there (a group decision to avoid a `.github/`
change). To run them locally:

```bash
docker build -t evoting-minio:RELEASE.2025-10-15T17-29-55Z docker/minio
docker run -d --name req05-minio -p 9100:9000 \
  -e MINIO_ROOT_USER=rootadmin -e MINIO_ROOT_PASSWORD=rootpassword123 \
  evoting-minio:RELEASE.2025-10-15T17-29-55Z
REQ05_MINIO_ENDPOINT=localhost:9100 REQ05_MINIO_ROOT_USER=rootadmin \
  REQ05_MINIO_ROOT_PASSWORD=rootpassword123 python -m pytest tests/test_audit_protection.py
```

Each run uses a new bucket and new users. The objects stay locked for 2 minutes
in tests.

## Simulated vs real

| Prototype | Real system |
|---|---|
| MinIO in Docker on the same host | Cloud WORM storage (for example S3 Object Lock) in a **separate account** that platform administrators cannot log into |
| Root credentials in environment variables | Root or account credentials held by a different team, with MFA and break-glass procedures |
| 1-day retention | The legal retention period for election records |
| Batches every 20 entries | Batches by time as well (for example every minute), so a quiet period still gets archived |

## Limitations

- **Entries between batches are only in the database.** Up to `BATCH_SIZE - 1`
  entries are protected only by the triggers until the next batch or a manual
  flush. A real system would also flush on a timer.
- **The archive proves what was written, not that everything was written.** If an
  attacker stops entries from being written at all, the archive has nothing to
  compare. REQ-04's hash chain, forwarded to a separate log service, covers that
  case.
