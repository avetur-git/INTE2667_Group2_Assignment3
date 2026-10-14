from app import db
from app.utils.helpers import utcnow
from app.utils import constants as C


class Division(db.Model):
    """An electoral division (House of Representatives seat)."""
    __tablename__ = 'divisions'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    state = db.Column(db.String(3), nullable=False)

    def to_dict(self):
        return {'id': self.id, 'name': self.name, 'state': self.state}


class Election(db.Model):
    __tablename__ = 'elections'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    election_date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), nullable=False, default=C.ELECTION_DRAFT)
    senate_seats_override = db.Column(db.Integer, nullable=True)  # testing only
    created_at = db.Column(db.DateTime, default=utcnow)

    def seats_for_state(self, state):
        if self.senate_seats_override:
            return self.senate_seats_override
        return C.SENATE_SEATS.get(state, 12)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'election_date': self.election_date.isoformat(),
            'status': self.status,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
