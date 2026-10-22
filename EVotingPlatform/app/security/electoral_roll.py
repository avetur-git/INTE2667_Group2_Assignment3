"""
REQ-12 - Real-time eligibility check against the authoritative electoral roll
=============================================================================

Before a voter sees a ballot paper or casts a vote, the platform asks the
authoritative electoral roll - live, every time - whether that voter is
enrolled and eligible. Part Two chose Option B: a secure real-time call to the
roll, where both sides prove who they are (mutual TLS) and the request carries
only what is needed.

In this prototype the roll runs in the same process, but it sits behind the
same boundary a network call would cross:

    voting platform  --request (roll reference + one-time id)-->  electoral roll
    voting platform  <--signed answer (only eligibility fields)--  electoral roll

The platform trusts an answer only if ALL of these hold, otherwise it refuses
the voter ("fail closed"):
  * the roll's digital signature verifies     -> nobody altered or forged it
  * it echoes this request's one-time id      -> not an answer to another request
  * it was issued within the last 30 seconds  -> not an old answer replayed

Production would replace `transport` with an HTTPS call using mutual TLS to
the AEC's roll service; the checks in `lookup_electoral_roll` stay the same.
"""
import json
import secrets
import time
from datetime import date

from cryptography.exceptions import InvalidSignature

from app import db
from app.security.hooks import hooks
from app.security.keys import _private_key   # same simulated HSM as REQ-01/02/20

MAX_ANSWER_AGE_SECONDS = 30


class RollUnavailable(Exception):
    """The roll could not be reached, or its answer could not be trusted."""


def _roll_key():
    """The roll's signing key. Only the roll holds the private half; the
    platform only ever uses the public half to verify answers."""
    return _private_key('roll_key')


# ---------------------------------------------------------------------------
# The authoritative roll (stands in for the AEC's separate roll system)
# ---------------------------------------------------------------------------

def roll_service(request_bytes):
    """Answer one eligibility request. Returns (answer_bytes, signature_hex).
    Reveals only what an eligibility decision needs - never name or address."""
    from app.models.voter import Voter   # imported here to avoid a circular import
    request = json.loads(request_bytes)
    voter = db.session.get(Voter, request['roll_ref'])
    answer = {
        'request_id': request['request_id'],
        'issued_at': time.time(),
        'found': voter is not None,
    }
    if voter is not None:
        answer.update({
            'enrolled': voter.enrolment_status == 'enrolled',
            'is_citizen': bool(voter.is_citizen),
            'date_of_birth': voter.date_of_birth.isoformat(),
            'division_id': voter.division_id,
            'state': voter.state,
        })
    answer_bytes = json.dumps(answer, sort_keys=True).encode()
    return answer_bytes, _roll_key().sign(answer_bytes).hex()


def transport(request_bytes):
    """How the platform reaches the roll. Production: HTTPS with mutual TLS.
    Tests replace this to simulate an outage, a forged answer or a replay."""
    return roll_service(request_bytes)


# ---------------------------------------------------------------------------
# The voting platform's side: ask, then verify before trusting
# ---------------------------------------------------------------------------

def lookup_electoral_roll(voter):
    """Hook 'lookup_electoral_roll'. Returns the roll's answer as a dict, or
    raises RollUnavailable so the caller refuses the voter."""
    request_id = secrets.token_hex(16)
    request_bytes = json.dumps({'roll_ref': voter.id, 'request_id': request_id}).encode()

    try:
        answer_bytes, signature_hex = transport(request_bytes)
    except Exception as exc:
        raise RollUnavailable('The electoral roll could not be reached') from exc

    try:
        _roll_key().public_key().verify(bytes.fromhex(signature_hex), answer_bytes)
    except (InvalidSignature, ValueError):
        raise RollUnavailable('The electoral roll answer failed its signature check')

    answer = json.loads(answer_bytes)
    if answer.get('request_id') != request_id:
        raise RollUnavailable('The electoral roll answer does not match this request')
    age = time.time() - answer.get('issued_at', 0)
    if not 0 <= age <= MAX_ANSWER_AGE_SECONDS:
        raise RollUnavailable('The electoral roll answer is out of date')

    if not answer['found']:
        return {'enrolled': False}
    return {
        'enrolled': answer['enrolled'],
        'is_citizen': answer['is_citizen'],
        'date_of_birth': date.fromisoformat(answer['date_of_birth']),
        'division_id': answer['division_id'],
        'state': answer['state'],
    }


def register_electoral_roll():
    hooks.register('lookup_electoral_roll', lookup_electoral_roll)
