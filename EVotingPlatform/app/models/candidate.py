from app import db
from app.utils.helpers import utcnow
from app.utils import constants as C


class Candidate(db.Model):
    """A House candidate (belongs to a division) or a Senate candidate
    (belongs to a state, with a party group and an order inside that group)."""
    __tablename__ = 'candidates'

    id = db.Column(db.Integer, primary_key=True)
    election_id = db.Column(db.Integer, db.ForeignKey('elections.id'), nullable=False)
    contest_type = db.Column(db.String(10), nullable=False)          # house | senate
    division_id = db.Column(db.Integer, db.ForeignKey('divisions.id'), nullable=True)  # house
    state = db.Column(db.String(3), nullable=True)                   # senate
    name = db.Column(db.String(120), nullable=False)
    party = db.Column(db.String(120), nullable=True)
    group_code = db.Column(db.String(3), nullable=True)              # senate: 'A', 'B', ... or 'UG'
    group_order = db.Column(db.Integer, nullable=True)               # senate: order within the group
    ballot_position = db.Column(db.Integer, nullable=True)           # house: order on the ballot paper
    is_excluded = db.Column(db.Boolean, default=False)               # court-ordered recount exclusion
    excluded_reason = db.Column(db.String(255), nullable=True)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'election_id': self.election_id,
            'contest_type': self.contest_type,
            'division_id': self.division_id,
            'state': self.state,
            'name': self.name,
            'party': self.party,
            'group_code': self.group_code,
            'group_order': self.group_order,
            'ballot_position': self.ballot_position,
            'is_excluded': self.is_excluded,
        }
