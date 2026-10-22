"""REQ-04: tamper-evident audit log.

Every audit entry is stored with three extra values:
  seq         1, 2, 3 ... with no gaps
  prev_hash   the entry_hash of the entry before it (64 zeros for the first one)
  entry_hash  HMAC-SHA256 over prev_hash and the entry's own fields

Changing an entry breaks its own hash. Deleting or inserting an entry breaks the
sequence or the link after it. Because the hash is keyed (AUDIT_CHAIN_KEY, kept
outside the database), someone who can edit the database cannot simply recompute
the hashes. REQ-05 (append-only triggers and the write-once archive) stops the edits
in the first place; this requirement makes any edit that still happens visible.

This replaces the plain 'write_audit_record' hook. REQ-05 wraps whatever is registered
there, so init_app must run before REQ-05's init_app in create_app."""
import hashlib
import hmac
import json
import os

import click
from flask import current_app
from sqlalchemy.exc import IntegrityError

from app import db
from app.models.audit import AuditLog
from app.security.chain import GENESIS
from app.security.hooks import hooks
from app.utils.helpers import utcnow

_RETRIES = 5


def _key():
    return str(current_app.config['AUDIT_CHAIN_KEY']).encode()


def row_payload(row):
    """The entry fields covered by the hash (the same when writing and when verifying)."""
    created = row.created_at.replace(tzinfo=None).isoformat() if row.created_at else None
    return {
        'user_id': row.user_id,
        'username': row.username,
        'action': row.action,
        'object_type': row.object_type,
        'object_id': row.object_id,
        'details': json.loads(json.dumps(row.details)),   # same shape as the JSON column returns
        'ip_address': row.ip_address,
        'user_agent': row.user_agent,
        'session_id': row.session_id,
        'success': bool(row.success),
        'created_at': created,
    }


def compute_hash(prev_hash, payload):
    body = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    return hmac.new(_key(), (prev_hash + body).encode(), hashlib.sha256).hexdigest()


def write_chained_audit_record(entry):
    """Hook write_audit_record: append the entry to the chain."""
    for _attempt in range(_RETRIES):
        last = (AuditLog.query.filter(AuditLog.seq.isnot(None))
                .order_by(AuditLog.seq.desc()).first())
        row = AuditLog(**entry, created_at=utcnow())
        row.seq = last.seq + 1 if last else 1
        row.prev_hash = last.entry_hash if last else GENESIS
        row.entry_hash = compute_hash(row.prev_hash, row_payload(row))
        db.session.add(row)
        try:
            db.session.commit()
            return
        except IntegrityError:      # another request took this sequence number: try again
            db.session.rollback()
    raise RuntimeError('Could not append to the audit chain')


def find_problems():
    """Check the whole chain. Returns a list of (audit_logs.id, reason); empty means intact."""
    problems = []
    prev, expected = GENESIS, 1
    for row in AuditLog.query.order_by(AuditLog.id):
        if row.seq is None or not row.entry_hash:
            problems.append((row.id, 'entry has no hash (it was not written through the audit chain)'))
            continue
        if row.seq != expected or row.prev_hash != prev:
            problems.append((row.id, f'chain broken before entry #{row.seq} '
                                     f'(an entry was removed, added or reordered)'))
        if not hmac.compare_digest(compute_hash(row.prev_hash or '', row_payload(row)), row.entry_hash):
            problems.append((row.id, f'entry #{row.seq} was altered'))
        prev, expected = row.entry_hash, row.seq + 1
    return problems


def init_app(app):
    """Called from create_app, before REQ-05's init_app."""
    app.config.setdefault('AUDIT_CHAIN_KEY', os.environ.get('AUDIT_CHAIN_KEY') or app.config['SECRET_KEY'])
    hooks.reset('write_audit_record')       # start from the base hook, so repeated create_app calls never stack
    hooks.register('write_audit_record', write_chained_audit_record)

    @app.cli.command('verify-audit-chain')
    def verify_audit_chain_command():
        """REQ-04: check every audit entry against the hash chain."""
        problems = find_problems()
        total = AuditLog.query.count()
        if not problems:
            click.echo(f'Audit chain OK: {total} entries verified.')
            return
        click.echo(f'AUDIT CHAIN BROKEN: {len(problems)} problem(s) in {total} entries')
        for entry_id, reason in problems:
            click.echo(f'  audit entry id {entry_id}: {reason}')
        raise SystemExit(1)