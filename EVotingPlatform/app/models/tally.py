from app import db
from app.utils.helpers import utcnow


class TallyResult(db.Model):
    """Computed result for one contest: a House division or a Senate state."""
    __tablename__ = 'tally_results'

    id = db.Column(db.Integer, primary_key=True)
    election_id = db.Column(db.Integer, db.ForeignKey('elections.id'), nullable=False)
    contest_type = db.Column(db.String(10), nullable=False)
    division_id = db.Column(db.Integer, db.ForeignKey('divisions.id'), nullable=True)
    state = db.Column(db.String(3), nullable=True)
    result = db.Column(db.JSON, nullable=False)
    computed_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    computed_at = db.Column(db.DateTime, default=utcnow)
