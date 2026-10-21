"""REQ-05: audit logs cannot be modified or deleted.

The trigger and hook tests always run. The WORM archive tests need a MinIO
server (docker/minio) and run only when REQ05_MINIO_ENDPOINT is set, e.g.

    docker run -d -p 9100:9000 -e MINIO_ROOT_USER=rootadmin \\
        -e MINIO_ROOT_PASSWORD=rootpassword123 evoting-minio:<release>
    REQ05_MINIO_ENDPOINT=localhost:9100 REQ05_MINIO_ROOT_USER=rootadmin \\
        REQ05_MINIO_ROOT_PASSWORD=rootpassword123 python -m pytest tests/test_audit_protection.py
"""
import hashlib
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import exc, text

from app import create_app, db
from app.models import AuditArchiveBatch, AuditLog
from app.security import audit_protection
from app.security.hooks import hooks
from app.services.audit import log_action

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
from setup_audit_archive import setup as setup_archive  # noqa: E402


def add_entries(n, action='TEST_ACTION'):
    for i in range(n):
        log_action(action, object_type='test', object_id=i)
    return AuditLog.query.filter_by(action=action).order_by(AuditLog.id).all()


# ------------------------------------------- database triggers (issue #10 tests)

def test_audit_table_rejects_update(app):
    [entry] = add_entries(1)
    entry.action = 'TAMPERED'
    with pytest.raises(exc.DatabaseError, match='append-only'):
        db.session.commit()
    db.session.rollback()
    with pytest.raises(exc.DatabaseError, match='append-only'):
        db.session.execute(text("UPDATE audit_logs SET success = 0"))
    db.session.rollback()
    assert db.session.get(AuditLog, entry.id).action == 'TEST_ACTION'


def test_audit_table_rejects_delete(app):
    [entry] = add_entries(1)
    db.session.delete(entry)
    with pytest.raises(exc.DatabaseError, match='append-only'):
        db.session.commit()
    db.session.rollback()
    for attempt in (lambda: AuditLog.query.delete(),
                    lambda: db.session.execute(text('DELETE FROM audit_logs'))):
        with pytest.raises(exc.DatabaseError, match='append-only'):
            attempt()
        db.session.rollback()
    assert AuditLog.query.count() == 1


def test_audit_table_still_accepts_new_entries(app):
    assert len(add_entries(3)) == 3


def test_triggers_added_to_a_database_created_before_req05(tmp_path):
    """Existing databases get the triggers on the next start (no migration needed)."""
    url = f"sqlite:///{(tmp_path / 'old.db').as_posix()}"
    config = {'TESTING': True, 'SQLALCHEMY_DATABASE_URI': url,
              'SQLALCHEMY_BINDS': {'ballots': 'sqlite:///:memory:'}, 'MFA_REQUIRED': False}
    app = create_app(config)
    with app.app_context():
        for name in ('audit_logs_no_update', 'audit_logs_no_delete'):   # simulate a pre-REQ-05 database
            db.session.execute(text(f'DROP TRIGGER {name}'))
        db.session.commit()
        db.engine.dispose()
    app = create_app(config)                                          # next start
    with app.app_context():
        names = {row[0] for row in db.session.execute(
            text("SELECT name FROM sqlite_master WHERE type = 'trigger'"))}
        assert {'audit_logs_no_update', 'audit_logs_no_delete'} <= names
        db.session.remove()
        db.engine.dispose()


# ------------------------------------------------------- hook wrapping (no MinIO)

def test_archiving_wraps_the_existing_hook_once(app, monkeypatch):
    create_app({'TESTING': True, 'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
                'SQLALCHEMY_BINDS': {'ballots': 'sqlite:///:memory:'}})   # a second start
    calls = []
    monkeypatch.setattr(audit_protection, 'archive_pending', lambda force=False: calls.append(1))
    app.config['AUDIT_ARCHIVE_ENDPOINT'] = 'archive.invalid:9000'
    add_entries(1)
    assert hooks.get('write_audit_record')._req05 is True
    assert len(calls) == 1                    # not archived twice by a stacked wrapper
    assert AuditLog.query.count() == 1        # the wrapped hook still wrote the row


def test_archive_is_off_unless_configured(app, monkeypatch):
    monkeypatch.setattr(audit_protection, 'archive_pending',
                        lambda force=False: pytest.fail('archive should not run'))
    assert not app.config['AUDIT_ARCHIVE_ENDPOINT']
    assert len(add_entries(2)) == 2


def test_archive_failure_never_blocks_the_audited_action(app, monkeypatch):
    def broken(force=False):
        raise ConnectionError('archive unreachable')
    monkeypatch.setattr(audit_protection, 'archive_pending', broken)
    app.config['AUDIT_ARCHIVE_ENDPOINT'] = 'archive.invalid:9000'
    assert len(add_entries(2)) == 2           # entries still recorded; archived by a later batch


# --------------------------------------------------- WORM archive (needs MinIO)

MINIO = os.environ.get('REQ05_MINIO_ENDPOINT')
needs_minio = pytest.mark.skipif(not MINIO, reason='set REQ05_MINIO_ENDPOINT to run the MinIO archive tests')


@pytest.fixture(scope='session')
def minio_archive():
    """A fresh locked bucket with its own writer and reader users for this test run."""
    run = uuid.uuid4().hex[:8]
    archive = {'endpoint': MINIO, 'bucket': f'audit-test-{run}',
               'root': (os.environ['REQ05_MINIO_ROOT_USER'], os.environ['REQ05_MINIO_ROOT_PASSWORD']),
               'writer': (f'writer{run}', f'writer-secret-{run}'),
               'reader': (f'reader{run}', f'reader-secret-{run}')}
    setup_archive(MINIO, *archive['root'], archive['bucket'], archive['writer'], archive['reader'])
    return archive


@pytest.fixture()
def archived_app(app, minio_archive):
    app.config.update({
        'AUDIT_ARCHIVE_ENDPOINT': minio_archive['endpoint'], 'AUDIT_ARCHIVE_BUCKET': minio_archive['bucket'],
        'AUDIT_ARCHIVE_ACCESS_KEY': minio_archive['writer'][0], 'AUDIT_ARCHIVE_SECRET_KEY': minio_archive['writer'][1],
        'AUDIT_ARCHIVE_READER_ACCESS_KEY': minio_archive['reader'][0],
        'AUDIT_ARCHIVE_READER_SECRET_KEY': minio_archive['reader'][1],
        'AUDIT_ARCHIVE_BATCH_SIZE': 3,
        'AUDIT_ARCHIVE_RETENTION_SECONDS': 120,   # short, so test objects expire quickly
    })
    return app


def client(archive, who):
    from minio import Minio
    access_key, secret_key = archive[who]
    return Minio(archive['endpoint'], access_key=access_key, secret_key=secret_key,
                 secure=False, region='us-east-1')


def archived_batch(n=3):
    add_entries(n)
    batch = AuditArchiveBatch.query.order_by(AuditArchiveBatch.id.desc()).first()
    assert batch is not None, 'expected a full batch to be archived'
    return batch


@needs_minio
def test_entries_archived_in_locked_batches(archived_app, minio_archive):
    batch = archived_batch(3)
    reader = client(minio_archive, 'reader')
    body = reader.get_object(minio_archive['bucket'], batch.object_key, version_id=batch.version_id).read()
    assert hashlib.sha256(body).hexdigest() == batch.sha256
    assert len(body.decode().splitlines()) == batch.entry_count == 3
    retention = reader.get_object_retention(minio_archive['bucket'], batch.object_key,
                                            version_id=batch.version_id)
    assert retention.mode == 'COMPLIANCE'
    assert retention.retain_until_date > datetime.now(timezone.utc)


@needs_minio
def test_partial_batch_waits_until_flushed(archived_app):
    add_entries(2)
    assert AuditArchiveBatch.query.count() == 0
    result = archived_app.test_cli_runner().invoke(args=['archive-audit-logs'])
    assert result.exit_code == 0, result.output
    assert AuditArchiveBatch.query.one().entry_count == 2


@needs_minio
def test_locked_object_cannot_be_deleted(archived_app, minio_archive):
    from minio.error import S3Error
    batch = archived_batch(3)
    root = client(minio_archive, 'root')                       # even the storage administrator
    with pytest.raises(S3Error):
        root.remove_object(minio_archive['bucket'], batch.object_key, version_id=batch.version_id)
    root.remove_object(minio_archive['bucket'], batch.object_key)   # only adds a delete marker
    body = client(minio_archive, 'reader').get_object(
        minio_archive['bucket'], batch.object_key, version_id=batch.version_id).read()
    assert hashlib.sha256(body).hexdigest() == batch.sha256


@needs_minio
def test_locked_object_cannot_be_overwritten(archived_app, minio_archive):
    import io
    from minio.commonconfig import COMPLIANCE
    from minio.error import S3Error
    from minio.retention import Retention
    batch = archived_batch(3)
    bucket, key = minio_archive['bucket'], batch.object_key
    forged = b'{"id": 1, "action": "NOTHING_HAPPENED"}\n'
    client(minio_archive, 'writer').put_object(bucket, key, io.BytesIO(forged), len(forged))
    root = client(minio_archive, 'root')
    with pytest.raises(S3Error):                               # nor can the lock be shortened
        root.set_object_retention(bucket, key, Retention(
            COMPLIANCE, datetime.now(timezone.utc) + timedelta(seconds=5)), version_id=batch.version_id)
    original = client(minio_archive, 'reader').get_object(bucket, key, version_id=batch.version_id).read()
    assert hashlib.sha256(original).hexdigest() == batch.sha256   # the locked version is untouched


@needs_minio
def test_archive_writer_cannot_read_list_or_delete(archived_app, minio_archive):
    from minio.error import S3Error
    batch = archived_batch(3)
    writer = client(minio_archive, 'writer')
    for attempt in (lambda: writer.get_object(minio_archive['bucket'], batch.object_key).read(),
                    lambda: list(writer.list_objects(minio_archive['bucket'])),
                    lambda: writer.remove_object(minio_archive['bucket'], batch.object_key,
                                                 version_id=batch.version_id)):
        with pytest.raises(S3Error) as error:
            attempt()
        assert error.value.code == 'AccessDenied'


@needs_minio
def test_verify_passes_when_database_matches_archive(archived_app):
    archived_batch(3)
    result = archived_app.test_cli_runner().invoke(args=['verify-audit-archive'])
    assert result.exit_code == 0, result.output
    assert 'OK: 1 archived batch' in result.output


@needs_minio
def test_verify_detects_changed_or_deleted_database_rows(archived_app):
    batch = archived_batch(3)
    # An attacker with direct access to the database file drops the triggers first
    db.session.execute(text('DROP TRIGGER audit_logs_no_update'))
    db.session.execute(text('DROP TRIGGER audit_logs_no_delete'))
    db.session.execute(text('UPDATE audit_logs SET success = 0 WHERE id = :i'), {'i': batch.first_id})
    db.session.execute(text('DELETE FROM audit_logs WHERE id = :i'), {'i': batch.last_id})
    db.session.commit()
    [problem] = audit_protection.verify_archive()
    assert f'changed ids [{batch.first_id}]' in problem
    assert f'missing ids [{batch.last_id}]' in problem
    result = archived_app.test_cli_runner().invoke(args=['verify-audit-archive'])
    assert result.exit_code != 0 and 'PROBLEM' in result.output
