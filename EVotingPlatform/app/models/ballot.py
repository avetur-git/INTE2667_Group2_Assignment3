from app import db
from app.utils.helpers import utcnow


class Ballot(db.Model):
    """One cast ballot (House + Senate papers together).

    REQ-01: stored in its own database (ballots.db). Contains NO voter id,
    user id, session id, channel or terminal id, and only the date, never the time."""
    __tablename__ = 'ballots'
    __bind_key__ = 'ballots'
    __table_args__ = (db.UniqueConstraint('election_id', 'seq', name='uq_ballot_election_seq'),)

    id = db.Column(db.Integer, primary_key=True)
    ballot_uid = db.Column(db.String(36), unique=True, nullable=False)
    election_id = db.Column(db.Integer, nullable=False, index=True)  # plain number: no cross-database foreign key
    division_id = db.Column(db.Integer, nullable=False)
    state = db.Column(db.String(3), nullable=False)

    house_order = db.Column(db.JSON, nullable=False)     # candidate ids, most preferred first
    senate_atl = db.Column(db.JSON, nullable=True)       # {group_code: number} as entered
    senate_btl = db.Column(db.JSON, nullable=True)       # {candidate_id: number} as entered
    senate_order = db.Column(db.JSON, nullable=False)    # resolved candidate ids, most preferred first
    senate_source = db.Column(db.String(3), nullable=False)  # 'atl' or 'btl'

    receipt_code = db.Column(db.String(32), unique=True, nullable=False)
    ballot_hash = db.Column(db.String(128), nullable=True)   # filled by REQ-02 hook
    seq = db.Column(db.Integer, nullable=True)            # REQ-02: position in this election's chain
    prev_hash = db.Column(db.String(64), nullable=True)   # REQ-02: hash of the previous ballot
    cast_date = db.Column(db.Date, default=lambda: utcnow().date())




class SpentToken(db.Model):
    """REQ-01: one row per ballot token already used. The primary key makes a second use fail."""
    __tablename__ = 'spent_tokens'
    __bind_key__ = 'ballots'

    nonce = db.Column(db.String(32), primary_key=True)



class BallotCheckpoint(db.Model):
    """REQ-02: a signed record of the chain head, saved every few ballots."""
    __tablename__ = 'ballot_checkpoints'
    __bind_key__ = 'ballots'

    id = db.Column(db.Integer, primary_key=True)
    election_id = db.Column(db.Integer, nullable=False, index=True)
    seq = db.Column(db.Integer, nullable=False)
    head_hash = db.Column(db.String(64), nullable=False)
    signature = db.Column(db.String(128), nullable=False)
