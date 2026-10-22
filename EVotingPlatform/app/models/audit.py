from app import db
from app.utils.helpers import utcnow


class AuditLog(db.Model):
    """Base audit trail. Fields match REQ-15: user, action, affected object,
    timestamp, source session/device, success/failure. Tamper-evidence (REQ-04)
    and protection (REQ-05) are added later by replacing the 'write_audit_record' hook."""
    __tablename__ = 'audit_logs'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=True)
    username = db.Column(db.String(80), nullable=True)
    action = db.Column(db.String(60), nullable=False)
    object_type = db.Column(db.String(40), nullable=True)
    object_id = db.Column(db.String(60), nullable=True)
    details = db.Column(db.JSON, nullable=True)
    ip_address = db.Column(db.String(64), nullable=True)
    user_agent = db.Column(db.String(255), nullable=True)
    session_id = db.Column(db.String(64), nullable=True)
    success = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=utcnow)
    # REQ-04: hash chain, written by app/security/audit_chain.py
    seq = db.Column(db.Integer, unique=True, nullable=True)
    prev_hash = db.Column(db.String(64), nullable=True)
    entry_hash = db.Column(db.String(64), nullable=True)

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'username': self.username,
            'action': self.action,
            'object_type': self.object_type,
            'object_id': self.object_id,
            'details': self.details,
            'ip_address': self.ip_address,
            'user_agent': self.user_agent,
            'session_id': self.session_id,
            'success': self.success,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'seq': self.seq,
            'prev_hash': self.prev_hash,
            'entry_hash': self.entry_hash,
        }


class AuditArchiveBatch(db.Model):
    """REQ-05: one batch of audit entries copied to write-once (WORM) storage.
    Records which entries are archived, where, and the SHA-256 of the object, so
    the archive can be checked against the database later."""
    __tablename__ = 'audit_archive_batches'

    id = db.Column(db.Integer, primary_key=True)
    first_id = db.Column(db.Integer, nullable=False, unique=True)   # audit_logs.id range
    last_id = db.Column(db.Integer, nullable=False)
    entry_count = db.Column(db.Integer, nullable=False)
    object_key = db.Column(db.String(200), nullable=False)
    version_id = db.Column(db.String(100), nullable=True)
    sha256 = db.Column(db.String(64), nullable=False)
    retain_until = db.Column(db.DateTime, nullable=False)
    archived_at = db.Column(db.DateTime, default=utcnow)
