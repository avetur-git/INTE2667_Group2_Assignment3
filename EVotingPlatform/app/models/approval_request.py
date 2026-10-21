from app import db
from app.utils.helpers import utcnow


class ApprovalRequest(db.Model):
    """Maker-checker records for sensitive election-management actions.

    A request is created while the action is pending, then approved or rejected by
    a different Commissioner's delegate. It also stores the expiry time so the
    action cannot run after the waiting period expires.
    """
    __tablename__ = 'approval_requests'

    id = db.Column(db.Integer, primary_key=True)
    action = db.Column(db.String(64), nullable=False)
    target_type = db.Column(db.String(32), nullable=False, default='candidate')
    target_id = db.Column(db.String(64), nullable=False)
    target = db.Column(db.String(255), nullable=False)
    reason = db.Column(db.String(255), nullable=False)
    requested_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    requested_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    status = db.Column(db.String(20), default='pending', nullable=False)
    approved_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    rejected_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    decided_at = db.Column(db.DateTime, nullable=True)
    expires_at = db.Column(db.DateTime, nullable=False)

    requester = db.relationship('User', foreign_keys='ApprovalRequest.requested_by', backref='approval_requests_requested')
    approver = db.relationship('User', foreign_keys='ApprovalRequest.approved_by', backref='approval_requests_approved')
    rejector = db.relationship('User', foreign_keys='ApprovalRequest.rejected_by', backref='approval_requests_rejected')

    def to_dict(self):
        return {
            'id': self.id,
            'action': self.action,
            'target_type': self.target_type,
            'target_id': self.target_id,
            'target': self.target,
            'reason': self.reason,
            'requested_by': self.requested_by,
            'requested_by_username': self.requester.username if self.requester else None,
            'requested_at': self.requested_at.isoformat() if self.requested_at else None,
            'status': self.status,
            'approved_by': self.approved_by,
            'approved_by_username': self.approver.username if self.approver else None,
            'rejected_by': self.rejected_by,
            'rejected_by_username': self.rejector.username if self.rejector else None,
            'decided_at': self.decided_at.isoformat() if self.decided_at else None,
            'expires_at': self.expires_at.isoformat() if self.expires_at else None,
        }
