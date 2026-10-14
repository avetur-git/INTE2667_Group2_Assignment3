from app import db
from app.utils.helpers import utcnow
from app.utils import constants as C


class Voter(db.Model):
    """An entry on the electoral roll. Linked to a login (User) once the person
    has an account. Holds identity details and NEVER any vote choices."""
    __tablename__ = 'voters'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), unique=True, nullable=True)
    first_name = db.Column(db.String(80), nullable=False)
    last_name = db.Column(db.String(80), nullable=False)
    date_of_birth = db.Column(db.Date, nullable=False)
    is_citizen = db.Column(db.Boolean, default=True)
    is_overseas = db.Column(db.Boolean, default=False)   # overseas voters use diplomatic missions
    address_line = db.Column(db.String(200), nullable=False)
    suburb = db.Column(db.String(100), nullable=False)
    state = db.Column(db.String(3), nullable=False)
    postcode = db.Column(db.String(10), nullable=False)
    division_id = db.Column(db.Integer, db.ForeignKey('divisions.id'), nullable=False)
    enrolment_status = db.Column(db.String(20), nullable=False, default=C.ENROLMENT_PENDING)
    registered_via = db.Column(db.String(20), default='self')   # self | aec_officer
    registered_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    division = db.relationship('Division')

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'first_name': self.first_name,
            'last_name': self.last_name,
            'date_of_birth': self.date_of_birth.isoformat(),
            'is_citizen': self.is_citizen,
            'is_overseas': self.is_overseas,
            'address_line': self.address_line,
            'suburb': self.suburb,
            'state': self.state,
            'postcode': self.postcode,
            'division_id': self.division_id,
            'division_name': self.division.name if self.division else None,
            'enrolment_status': self.enrolment_status,
            'registered_via': self.registered_via,
        }


class VoteRecord(db.Model):
    """Records THAT a voter has voted in an election - never WHAT they voted.
    There is deliberately no link from this table to the ballots table.
    (REQ-01 hardens this separation; REQ-13 hardens the one-vote rule.)"""
    __tablename__ = 'vote_records'

    id = db.Column(db.Integer, primary_key=True)
    voter_id = db.Column(db.Integer, db.ForeignKey('voters.id'), nullable=False)
    election_id = db.Column(db.Integer, db.ForeignKey('elections.id'), nullable=False)
    channel = db.Column(db.String(20), nullable=False)
    terminal_id = db.Column(db.String(50), nullable=True)
    voted_at = db.Column(db.DateTime, default=utcnow)
    # NOTE: no unique constraint on (voter_id, election_id) in the base build -
    # duplicate prevention is a plain check in the hook 'has_already_voted' (REQ-13).
