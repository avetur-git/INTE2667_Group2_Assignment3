from app import db
from app.utils.helpers import utcnow

class CandidateHistory(db.Model):
    __tablename__ = 'candidate_history'

    id = db.Column(db.Integer, primary_key=True)
    candidate_id = db.Column(db.Integer, db.ForeignKey('candidates.id'), nullable=False)
    version = db.Column(db.Integer, nullable=False)          # 1, 2, 3 ... per candidate
    action = db.Column(db.String(20), nullable=False)        # created | updated | excluded
    changes = db.Column(db.JSON, nullable=False)             # {"group_order": {"old": 2, "new": 1}}
    changed_by = db.Column(db.Integer, nullable=False)
    changed_at = db.Column(db.DateTime, default=utcnow)
    session_id = db.Column(db.String(64), nullable=True)
    user_agent = db.Column(db.String(255), nullable=True)

    def to_dict(self):
        return {
            'id': self.id,
            'candidate_id': self.candidate_id,
            'version': self.version,
            'action': self.action,
            'changes': self.changes,
            'changed_by': self.changed_by,
            'changed_at': self.changed_at.isoformat() if self.changed_at else None,
            'session_id': self.session_id,
            'user_agent': self.user_agent,
        }