from app import db
from app.utils.helpers import utcnow


class Terminal(db.Model):
    """A registered voting terminal (polling station or diplomatic mission)."""
    __tablename__ = 'terminals'

    id = db.Column(db.Integer, primary_key=True)
    terminal_id = db.Column(db.String(50), unique=True, nullable=False)
    channel = db.Column(db.String(20), nullable=False)          # polling_station | diplomatic_mission
    location_name = db.Column(db.String(150), nullable=False)
    state = db.Column(db.String(3), nullable=True)
    country = db.Column(db.String(60), default='Australia')
    software_version = db.Column(db.String(40), nullable=True)
    software_signature = db.Column(db.Text, nullable=True)      # REQ-03 stores/verifies this
    is_active = db.Column(db.Boolean, default=True)
    last_seen = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'terminal_id': self.terminal_id,
            'channel': self.channel,
            'location_name': self.location_name,
            'state': self.state,
            'country': self.country,
            'software_version': self.software_version,
            'is_active': self.is_active,
            'last_seen': self.last_seen.isoformat() if self.last_seen else None,
        }
