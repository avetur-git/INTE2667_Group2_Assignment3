from app import db
from app.utils.helpers import utcnow


class WebAuthnCredential(db.Model):
    """REQ-10: a FIDO2 security key registered to a privileged user.
    Only the public key is stored; the private key never leaves the authenticator."""
    __tablename__ = 'webauthn_credentials'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    credential_id = db.Column(db.LargeBinary, unique=True, nullable=False)
    public_key = db.Column(db.LargeBinary, nullable=False)
    sign_count = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=utcnow)
    last_used_at = db.Column(db.DateTime, nullable=True)


class WebAuthnChallenge(db.Model):
    """REQ-10: a one-time challenge for a registration or login ceremony.
    Deleted as soon as it is used, so a captured assertion cannot be replayed."""
    __tablename__ = 'webauthn_challenges'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    purpose = db.Column(db.String(10), nullable=False)  # 'register' or 'login'
    challenge = db.Column(db.LargeBinary, unique=True, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)


class EnrolmentCode(db.Model):
    """REQ-10: single-use, short-lived code that lets a privileged user register
    their first security key. Issued by an operator with `flask issue-enrolment-code`.
    Only a SHA-256 hash of the code is stored."""
    __tablename__ = 'enrolment_codes'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    code_hash = db.Column(db.String(64), unique=True, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    used_at = db.Column(db.DateTime, nullable=True)
