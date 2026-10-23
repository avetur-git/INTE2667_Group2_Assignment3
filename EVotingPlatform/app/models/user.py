from app import db
from flask import current_app
from app.utils.helpers import utcnow
from app.utils import constants as C
import bcrypt


class User(db.Model):
    """A login account. Voters and staff (AEC officers, delegates, admins) all
    use this table; the role decides what they can do."""
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    role = db.Column(db.String(30), nullable=False, default=C.ROLE_VOTER)
    created_at = db.Column(db.DateTime, default=utcnow)
    is_active = db.Column(db.Boolean, default=True)

    voter = db.relationship('Voter', backref='user', uselist=False,
                            foreign_keys='Voter.user_id')

    def __init__(self, username, email, password, role=C.ROLE_VOTER, password_hash=None):
        self.username = username
        self.email = email
        self.role = role
        if password_hash:   # REQ-09: bulk demo data reuses one hash of the same password
            self.password_hash = password_hash
        else:
            self.set_password(password)

    def set_password(self, password):
        rounds = int(current_app.config.get('BCRYPT_ROUNDS', 12))
        salt = bcrypt.gensalt(rounds=rounds)
        self.password_hash = bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')

    def check_password(self, password):
        return bcrypt.checkpw(password.encode('utf-8'), self.password_hash.encode('utf-8'))

    @property
    def is_privileged(self):
        return self.role in C.PRIVILEGED_ROLES

    def to_dict(self):
        return {
            'id': self.id,
            'username': self.username,
            'email': self.email,
            'role': self.role,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'is_active': self.is_active,
        }

    def __repr__(self):
        return f'<User {self.username} ({self.role})>'
