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