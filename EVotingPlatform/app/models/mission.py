from app import db
from app.utils.helpers import utcnow


class Mission(db.Model):
    """A verified Australian diplomatic mission allowed to run overseas voting (REQ-19).

    A voting session from this mission is accepted only if the terminal is listed here, the
    caller's IP is inside `allowed_cidrs` (stands in for the site-to-site VPN), the device
    certificate fingerprint is in `cert_fingerprints`, and the geo-IP country matches `country`."""
    __tablename__ = 'missions'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), unique=True, nullable=False)
    country = db.Column(db.String(2), nullable=False)             # ISO two-letter code
    allowed_cidrs = db.Column(db.JSON, nullable=False)            # ["198.51.100.0/24"]
    cert_fingerprints = db.Column(db.JSON, nullable=False)        # approved SHA-256 hex fingerprints
    revoked_fingerprints = db.Column(db.JSON, nullable=False, default=list)
    terminal_ids = db.Column(db.JSON, nullable=False)             # ["DM-0001"]
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_by = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow)
    deactivated_at = db.Column(db.DateTime, nullable=True)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'country': self.country,
            'allowed_cidrs': self.allowed_cidrs,
            'cert_fingerprints': self.cert_fingerprints,
            'revoked_fingerprints': self.revoked_fingerprints,
            'terminal_ids': self.terminal_ids,
            'is_active': self.is_active,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'deactivated_at': self.deactivated_at.isoformat() if self.deactivated_at else None,
        }