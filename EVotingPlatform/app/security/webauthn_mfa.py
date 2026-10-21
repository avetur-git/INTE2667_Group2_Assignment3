"""REQ-10: phishing-resistant MFA for privileged users with FIDO2 / WebAuthn.

Staff log in with their password plus a signed assertion from a registered
security key. The assertion is bound to our origin and to a one-time server
challenge, so a phishing site cannot use it and a captured one cannot be replayed.

Enforcement is controlled by the MFA_REQUIRED setting (off by default for now,
see docs/REQ-10_WEBAUTHN.md). Registration and verification work either way.
"""
import hashlib
import json
import secrets
from datetime import timedelta

from flask import current_app
from webauthn import (generate_authentication_options, generate_registration_options,
                      options_to_json, verify_authentication_response,
                      verify_registration_response)
from webauthn.helpers import base64url_to_bytes
from webauthn.helpers.exceptions import WebAuthnException
from webauthn.helpers.structs import (AuthenticatorSelectionCriteria,
                                      PublicKeyCredentialDescriptor,
                                      ResidentKeyRequirement,
                                      UserVerificationRequirement)

from app import db
from app.models.webauthn import EnrolmentCode, WebAuthnChallenge, WebAuthnCredential
from app.security.hooks import hooks
from app.utils.helpers import utcnow

CHALLENGE_TTL = timedelta(minutes=2)
ENROLMENT_CODE_TTL = timedelta(minutes=15)
# No 0/O/1/I/L, so codes can be read out or typed without mistakes
_CODE_ALPHABET = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'


class MFAError(Exception):
    pass


def _rp_id():
    return current_app.config['WEBAUTHN_RP_ID']


def _origins():
    return current_app.config['WEBAUTHN_ORIGINS']


# ---------------------------------------------------------------- challenges

def _new_challenge(user, purpose):
    # Drop this user's expired challenges so the table does not grow
    WebAuthnChallenge.query.filter(WebAuthnChallenge.user_id == user.id,
                                   WebAuthnChallenge.expires_at < utcnow()).delete()
    challenge = secrets.token_bytes(32)
    db.session.add(WebAuthnChallenge(user_id=user.id, purpose=purpose, challenge=challenge,
                                     expires_at=utcnow() + CHALLENGE_TTL))
    db.session.commit()
    return challenge


def _consume_challenge(user, purpose, credential):
    """Find the challenge the authenticator signed and delete it (single use)."""
    try:
        client_data = json.loads(base64url_to_bytes(credential['response']['clientDataJSON']))
        challenge = base64url_to_bytes(client_data['challenge'])
    except (KeyError, TypeError, ValueError) as exc:
        raise MFAError('Malformed WebAuthn response') from exc
    row = WebAuthnChallenge.query.filter_by(user_id=user.id, purpose=purpose,
                                            challenge=challenge).first()
    if not row:
        raise MFAError('Unknown or already used challenge')
    db.session.delete(row)
    db.session.commit()
    if row.expires_at < utcnow():
        raise MFAError('Challenge expired')
    return challenge


# --------------------------------------------------------------- registration

def registration_options(user):
    existing = [PublicKeyCredentialDescriptor(id=c.credential_id)
                for c in WebAuthnCredential.query.filter_by(user_id=user.id)]
    options = generate_registration_options(
        rp_id=_rp_id(),
        rp_name=current_app.config['WEBAUTHN_RP_NAME'],
        user_name=user.username,
        user_id=str(user.id).encode(),
        challenge=_new_challenge(user, 'register'),
        exclude_credentials=existing,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.DISCOURAGED,
            user_verification=UserVerificationRequirement.PREFERRED),
    )
    return json.loads(options_to_json(options))


def register_credential(user, credential):
    challenge = _consume_challenge(user, 'register', credential)
    try:
        verified = verify_registration_response(
            credential=credential, expected_challenge=challenge,
            expected_rp_id=_rp_id(), expected_origin=_origins())
    except WebAuthnException as exc:
        raise MFAError(f'Security key registration failed: {exc}') from exc
    if WebAuthnCredential.query.filter_by(credential_id=verified.credential_id).first():
        raise MFAError('This security key is already registered')
    cred = WebAuthnCredential(user_id=user.id, credential_id=verified.credential_id,
                              public_key=verified.credential_public_key,
                              sign_count=verified.sign_count)
    db.session.add(cred)
    db.session.commit()
    return cred


# --------------------------------------------------------------------- login

def has_credentials(user):
    return WebAuthnCredential.query.filter_by(user_id=user.id).count() > 0


def authentication_options(user):
    creds = [PublicKeyCredentialDescriptor(id=c.credential_id)
             for c in WebAuthnCredential.query.filter_by(user_id=user.id)]
    options = generate_authentication_options(
        rp_id=_rp_id(), allow_credentials=creds,
        challenge=_new_challenge(user, 'login'),
        user_verification=UserVerificationRequirement.PREFERRED)
    return json.loads(options_to_json(options))


def verify_assertion(user, credential):
    """Raise MFAError unless `credential` is a fresh, valid assertion from one of
    the user's keys, made for our origin and RP ID."""
    if not isinstance(credential, dict):
        raise MFAError('Security key response missing')
    challenge = _consume_challenge(user, 'login', credential)
    try:
        raw_id = base64url_to_bytes(credential['rawId'])
    except (KeyError, TypeError, ValueError) as exc:
        raise MFAError('Malformed WebAuthn response') from exc
    stored = WebAuthnCredential.query.filter_by(user_id=user.id, credential_id=raw_id).first()
    if not stored:
        raise MFAError('Security key is not registered to this account')
    try:
        verified = verify_authentication_response(
            credential=credential, expected_challenge=challenge,
            expected_rp_id=_rp_id(), expected_origin=_origins(),
            credential_public_key=stored.public_key,
            credential_current_sign_count=stored.sign_count)
    except WebAuthnException as exc:
        raise MFAError(f'Security key verification failed: {exc}') from exc
    stored.sign_count = verified.new_sign_count
    stored.last_used_at = utcnow()
    db.session.commit()


def verify_mfa(user, request_data):
    """Hook `verify_mfa`: password already checked; require a valid assertion."""
    if not current_app.config.get('MFA_REQUIRED'):
        return True
    try:
        verify_assertion(user, (request_data or {}).get('webauthn'))
    except MFAError as exc:
        current_app.logger.info('MFA failed for %s: %s', user.username, exc)
        return False
    return True


# ------------------------------------------------------------ enrolment codes

def _hash_code(code):
    normalised = code.replace('-', '').replace(' ', '').upper()
    return hashlib.sha256(normalised.encode()).hexdigest()


def issue_enrolment_code(user):
    """Create a new code for `user`, cancelling any unused ones. Returns the plain code."""
    EnrolmentCode.query.filter_by(user_id=user.id, used_at=None).delete()
    raw = ''.join(secrets.choice(_CODE_ALPHABET) for _ in range(12))
    db.session.add(EnrolmentCode(user_id=user.id, code_hash=_hash_code(raw),
                                 expires_at=utcnow() + ENROLMENT_CODE_TTL))
    db.session.commit()
    return '-'.join(raw[i:i + 4] for i in range(0, 12, 4))


def find_enrolment_code(user, code):
    """Return the matching unused, unexpired code row, or raise MFAError."""
    if not code:
        raise MFAError('Enrolment code required')
    row = EnrolmentCode.query.filter_by(user_id=user.id, code_hash=_hash_code(code),
                                        used_at=None).first()
    if not row or row.expires_at < utcnow():
        raise MFAError('Invalid or expired enrolment code')
    return row


def register_webauthn_mfa():
    hooks.register('verify_mfa', verify_mfa)
