"""REQ-20: non-identifying, signed ballot receipts.

After a ballot is saved, the voter gets a receipt signed with the receipt key:

    {receipt_code, election_id, date, ballot_hash, key_id}  +  signature

- receipt_code: random, made by the server when the ballot is stored
  (secrets.token_hex), with no link to the voter, the time or the choices.
- ballot_hash: the REQ-02 chain hash. It covers the random ballot_uid and the
  previous ballot's hash, neither of which the voter is given, so the voter
  cannot use the receipt to prove how they voted.
- date only, never the time of voting.

Anyone can check a receipt with the public key (GET /api/receipts/public-key),
look a code up (GET /api/receipts/<code>) and, once voting has closed, read the
bulletin board of every code and hash (GET /api/elections/<id>/bulletin-board).
"""
import hashlib
import json
import re

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from flask import current_app

from app import db
from app.models.ballot import Ballot, ReceiptSignature
from app.security.hooks import hooks
from app.security.keys import receipt_private_key, receipt_public_key

RECEIPT_CODE_RE = re.compile(r'^[0-9A-F]{16}$')   # matches generate_receipt_code()


class ReceiptError(Exception):
    pass


def public_key_bytes():
    return receipt_public_key().public_bytes(encoding=serialization.Encoding.Raw,
                                             format=serialization.PublicFormat.Raw)


def public_key_pem():
    return receipt_public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo).decode()


def key_id():
    """Short fingerprint of the public key, so a receipt says which key signed it."""
    return hashlib.sha256(public_key_bytes()).hexdigest()[:16]


def receipt_fields(ballot, signer_key_id):
    """The signed part of a receipt. Exactly these fields, nothing about the voter."""
    return {
        'receipt_code': ballot.receipt_code,
        'election_id': ballot.election_id,
        'date': ballot.cast_date.isoformat() if ballot.cast_date else None,
        'ballot_hash': ballot.ballot_hash,
        'key_id': signer_key_id,
    }


def signed_message(fields):
    """Canonical bytes that are signed and verified (sorted keys, no spaces)."""
    return json.dumps(fields, sort_keys=True, separators=(',', ':')).encode()


def verify_receipt(receipt, public_key=None):
    """True if `receipt` (the dict given to the voter) carries a valid signature."""
    fields = {k: receipt.get(k) for k in ('receipt_code', 'election_id', 'date', 'ballot_hash', 'key_id')}
    try:
        (public_key or receipt_public_key()).verify(bytes.fromhex(receipt.get('signature') or ''),
                                                    signed_message(fields))
    except (InvalidSignature, ValueError):
        return False
    return True


def issue_signed_receipt(ballot):
    """Hook issue_receipt. Runs AFTER the ballot is committed, so it must not raise:
    an error here would tell the voter their vote failed when it was recorded.
    If signing is impossible the voter still gets their code, unsigned."""
    fields = receipt_fields(ballot, key_id())
    try:
        if not ballot.ballot_hash:
            # REQ-02 must have hashed the ballot; never sign a receipt without it
            raise ReceiptError('ballot has no hash')
        signature = receipt_private_key().sign(signed_message(fields)).hex()
        db.session.add(ReceiptSignature(receipt_code=ballot.receipt_code, signature=signature,
                                        key_id=fields['key_id']))
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error('Receipt %s could not be signed: %s', ballot.receipt_code, exc)
        return {**fields, 'signature': None}
    return {**fields, 'signature': signature}


def lookup(receipt_code):
    """Public lookup. Returns the published receipt, or None for an unknown code.
    `valid` is False if the stored ballot no longer matches its signature."""
    code = (receipt_code or '').strip().upper()
    if not RECEIPT_CODE_RE.match(code):
        return None
    ballot = Ballot.query.filter_by(receipt_code=code).first()
    stored = db.session.get(ReceiptSignature, code)
    if not ballot or not stored:
        return None
    receipt = {**receipt_fields(ballot, stored.key_id), 'signature': stored.signature}
    return {**receipt, 'valid': verify_receipt(receipt)}


def bulletin_board(election_id):
    """Every stored ballot once: code, hash, signature. Sorted by receipt code so the
    list never shows the order ballots were cast in (REQ-01 assumption 9)."""
    ballots = Ballot.query.filter_by(election_id=election_id).all()
    signatures = {s.receipt_code: s.signature for s in ReceiptSignature.query.filter(
        ReceiptSignature.receipt_code.in_([b.receipt_code for b in ballots])).all()} if ballots else {}
    return sorted(({'receipt_code': b.receipt_code, 'ballot_hash': b.ballot_hash,
                    'signature': signatures.get(b.receipt_code)} for b in ballots),
                  key=lambda e: e['receipt_code'])


def register_signed_receipts():
    hooks.register('issue_receipt', issue_signed_receipt)
