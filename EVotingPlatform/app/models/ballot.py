from app import db
from app.utils.helpers import utcnow


class Ballot(db.Model):
    """One cast ballot (House + Senate papers together). Contains NO voter id.
    Only the date is stored (not the time) to make timing-based matching with
    vote_records harder."""
    __tablename__ = 'ballots'

    id = db.Column(db.Integer, primary_key=True)
    ballot_uid = db.Column(db.String(36), unique=True, nullable=False)
    election_id = db.Column(db.Integer, db.ForeignKey('elections.id'), nullable=False)
    division_id = db.Column(db.Integer, db.ForeignKey('divisions.id'), nullable=False)
    state = db.Column(db.String(3), nullable=False)
    channel = db.Column(db.String(20), nullable=False)
    terminal_id = db.Column(db.String(50), nullable=True)

    house_order = db.Column(db.JSON, nullable=False)     # candidate ids, most preferred first
    senate_atl = db.Column(db.JSON, nullable=True)       # {group_code: number} as entered
    senate_btl = db.Column(db.JSON, nullable=True)       # {candidate_id: number} as entered
    senate_order = db.Column(db.JSON, nullable=False)    # resolved candidate ids, most preferred first
    senate_source = db.Column(db.String(3), nullable=False)  # 'atl' or 'btl'

    receipt_code = db.Column(db.String(32), unique=True, nullable=False)
    ballot_hash = db.Column(db.String(128), nullable=True)   # filled by REQ-02 hook
    cast_date = db.Column(db.Date, default=lambda: utcnow().date())
