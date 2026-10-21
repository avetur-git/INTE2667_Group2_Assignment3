"""
REQ-05 - Protect audit logs from modification or deletion
=========================================================

Two layers:

1. Database triggers on audit_logs reject every UPDATE and DELETE, so the
   application (or anyone using its connection) can only add entries. In
   production the app's Postgres role also has only INSERT and SELECT on the
   table (REQ-06, infra/db_users.tf).

2. Write-once (WORM) archive. Entries are copied in batches to an object store
   bucket with object locking in COMPLIANCE mode: until the retention date no
   one - not even the storage administrator - can delete or replace the stored
   version. MinIO stands in for cloud WORM storage (for example S3 Object Lock).
   The app uses a separate MinIO user that may only upload.

Archiving wraps whatever function is registered on the write_audit_record hook
(the plain row today, REQ-04's hash-chained entry later) instead of replacing
it, so REQ-04 and REQ-05 can land in either order. It is off unless
AUDIT_ARCHIVE_ENDPOINT is set, and an archive failure never blocks the action
being audited: unarchived entries are picked up by the next batch.
"""
import hashlib
import io
import json
import os
from datetime import datetime, timedelta, timezone

from flask import current_app
from sqlalchemy import func, text

from app import db
from app.models.audit import AuditArchiveBatch, AuditLog
from app.security.hooks import hooks

# --------------------------------------------------------------------- triggers

_SQLITE_TRIGGERS = [
    """CREATE TRIGGER IF NOT EXISTS audit_logs_no_update BEFORE UPDATE ON audit_logs
       BEGIN SELECT RAISE(ABORT, 'audit_logs is append-only (REQ-05)'); END""",
    """CREATE TRIGGER IF NOT EXISTS audit_logs_no_delete BEFORE DELETE ON audit_logs
       BEGIN SELECT RAISE(ABORT, 'audit_logs is append-only (REQ-05)'); END""",
]

# Run by the schema owner as part of a migration (the app role cannot create
# triggers in production). Kept here so both databases enforce the same rule.
POSTGRES_TRIGGERS = """
CREATE OR REPLACE FUNCTION audit_logs_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_logs is append-only (REQ-05)';
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS audit_logs_no_change ON audit_logs;
CREATE TRIGGER audit_logs_no_change BEFORE UPDATE OR DELETE ON audit_logs
    FOR EACH ROW EXECUTE FUNCTION audit_logs_append_only();
"""


def ensure_audit_triggers():
    """Create the triggers if missing. Safe to run on every start, including on
    databases created before REQ-05."""
    engine = db.engine
    if engine.dialect.name == 'sqlite':
        with engine.begin() as conn:
            for ddl in _SQLITE_TRIGGERS:
                conn.execute(text(ddl))
    elif engine.dialect.name == 'postgresql':
        try:
            with engine.begin() as conn:
                conn.execute(text(POSTGRES_TRIGGERS))
        except Exception as exc:   # expected when running as the restricted app role
            current_app.logger.info('Audit triggers not created by the app (%s); '
                                    'they are applied by the schema migration', exc)


# ---------------------------------------------------------------- configuration

def _env(name, default=None):
    return os.environ.get(name, default)


def init_app(app):
    """Called from create_app: settings, triggers and the archiving hook wrapper."""
    app.config.setdefault('AUDIT_ARCHIVE_ENDPOINT', _env('AUDIT_ARCHIVE_ENDPOINT', ''))
    app.config.setdefault('AUDIT_ARCHIVE_SECURE', _env('AUDIT_ARCHIVE_SECURE', 'false').lower() == 'true')
    app.config.setdefault('AUDIT_ARCHIVE_BUCKET', _env('AUDIT_ARCHIVE_BUCKET', 'audit-archive'))
    app.config.setdefault('AUDIT_ARCHIVE_REGION', _env('AUDIT_ARCHIVE_REGION', 'us-east-1'))
    app.config.setdefault('AUDIT_ARCHIVE_ACCESS_KEY', _env('AUDIT_ARCHIVE_ACCESS_KEY', ''))
    app.config.setdefault('AUDIT_ARCHIVE_SECRET_KEY', _env('AUDIT_ARCHIVE_SECRET_KEY', ''))
    # Read-only user for verify-audit-archive (the writer cannot read)
    app.config.setdefault('AUDIT_ARCHIVE_READER_ACCESS_KEY', _env('AUDIT_ARCHIVE_READER_ACCESS_KEY', ''))
    app.config.setdefault('AUDIT_ARCHIVE_READER_SECRET_KEY', _env('AUDIT_ARCHIVE_READER_SECRET_KEY', ''))
    app.config.setdefault('AUDIT_ARCHIVE_BATCH_SIZE', int(_env('AUDIT_ARCHIVE_BATCH_SIZE', '20')))
    app.config.setdefault('AUDIT_ARCHIVE_RETENTION_SECONDS',
                          int(_env('AUDIT_ARCHIVE_RETENTION_SECONDS', str(24 * 3600))))

    with app.app_context():
        ensure_audit_triggers()

    current = hooks.get('write_audit_record')
    if not getattr(current, '_req05', False):   # wrap once, even across many create_app calls
        hooks.register('write_audit_record', _with_archiving(current))


def archive_enabled():
    return bool(current_app.config.get('AUDIT_ARCHIVE_ENDPOINT'))


def _client(reader=False):
    from minio import Minio
    cfg = current_app.config
    prefix = 'AUDIT_ARCHIVE_READER_' if reader else 'AUDIT_ARCHIVE_'
    # The region is given so the client never asks for the bucket location,
    # which the upload-only writer is (correctly) not allowed to do.
    return Minio(cfg['AUDIT_ARCHIVE_ENDPOINT'], access_key=cfg[prefix + 'ACCESS_KEY'],
                 secret_key=cfg[prefix + 'SECRET_KEY'], secure=cfg['AUDIT_ARCHIVE_SECURE'],
                 region=cfg['AUDIT_ARCHIVE_REGION'])


# --------------------------------------------------------------------- archive

def _with_archiving(previous):
    def write_audit_record(entry):
        previous(entry)                 # the existing record (plain row, or REQ-04's chain)
        if archive_enabled():
            try:
                archive_pending()
            except Exception as exc:    # never block the audited action; retried next batch
                db.session.rollback()
                current_app.logger.error('Audit archive failed: %s', exc)
    write_audit_record._req05 = True
    return write_audit_record


def serialise(entries):
    """One JSON object per line, keys sorted, so the same rows give the same bytes."""
    return ''.join(json.dumps(e.to_dict(), sort_keys=True) + '\n' for e in entries).encode()


def archive_pending(force=False):
    """Archive entries not yet in a batch. Waits for a full batch unless `force`.
    Returns the new AuditArchiveBatch, or None if there was nothing to do."""
    cfg = current_app.config
    last_archived = db.session.query(func.max(AuditArchiveBatch.last_id)).scalar() or 0
    pending = (AuditLog.query.filter(AuditLog.id > last_archived)
               .order_by(AuditLog.id).limit(1000).all())
    if not pending or (len(pending) < cfg['AUDIT_ARCHIVE_BATCH_SIZE'] and not force):
        return None

    from minio.commonconfig import COMPLIANCE
    from minio.retention import Retention
    body = serialise(pending)
    digest = hashlib.sha256(body).hexdigest()
    first_id, last_id = pending[0].id, pending[-1].id
    key = f'audit-logs/{first_id:010d}-{last_id:010d}-{digest[:12]}.jsonl'
    retain_until = (datetime.now(timezone.utc)
                    + timedelta(seconds=cfg['AUDIT_ARCHIVE_RETENTION_SECONDS']))
    result = _client().put_object(
        cfg['AUDIT_ARCHIVE_BUCKET'], key, io.BytesIO(body), len(body),
        content_type='application/x-ndjson', metadata={'sha256': digest},
        retention=Retention(COMPLIANCE, retain_until))
    batch = AuditArchiveBatch(first_id=first_id, last_id=last_id, entry_count=len(pending),
                              object_key=key, version_id=result.version_id, sha256=digest,
                              retain_until=retain_until.replace(tzinfo=None))
    db.session.add(batch)
    db.session.commit()
    return batch


def verify_archive():
    """Compare every archived batch with the database. Returns a list of problems
    (empty = the database still matches what was locked in the archive)."""
    cfg = current_app.config
    client = _client(reader=True)
    problems = []
    for batch in AuditArchiveBatch.query.order_by(AuditArchiveBatch.first_id):
        label = f'batch {batch.first_id}-{batch.last_id}'
        try:
            response = client.get_object(cfg['AUDIT_ARCHIVE_BUCKET'], batch.object_key,
                                         version_id=batch.version_id)
            archived = response.read()
            response.close()
            response.release_conn()
        except Exception as exc:
            problems.append(f'{label}: cannot read archive ({exc})')
            continue
        if hashlib.sha256(archived).hexdigest() != batch.sha256:
            problems.append(f'{label}: archived object does not match its recorded hash')
            continue
        rows = (AuditLog.query.filter(AuditLog.id.between(batch.first_id, batch.last_id))
                .order_by(AuditLog.id).all())
        if serialise(rows) != archived:
            archived_ids = {json.loads(line)['id'] for line in archived.decode().splitlines()}
            current = {r.id: r for r in rows}
            missing = sorted(archived_ids - set(current))
            changed = sorted(i for i in archived_ids & set(current)
                             if serialise([current[i]]).decode() not in archived.decode())
            problems.append(f'{label}: database differs from archive '
                            f'(missing ids {missing}, changed ids {changed})')
    return problems
