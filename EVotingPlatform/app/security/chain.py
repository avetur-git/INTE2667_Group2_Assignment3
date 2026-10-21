"""REQ-02: hash chain for ballots with signed checkpoints.

Each ballot's hash covers its content AND the previous ballot's hash, so changing
or deleting one ballot breaks every link after it. A signed checkpoint is saved
every few ballots, so rewriting the whole chain is also detected."""
import hashlib
import json

from cryptography.exceptions import InvalidSignature

GENESIS = '0' * 64


def ballot_payload(ballot):
    """The ballot fields covered by the hash (used both when storing and verifying)."""
    return {
        'ballot_uid': ballot.ballot_uid,
        'election_id': ballot.election_id,
        'division_id': ballot.division_id,
        'state': ballot.state,
        'house_order': ballot.house_order,
        'senate_atl': ballot.senate_atl,
        'senate_btl': ballot.senate_btl,
        'senate_order': ballot.senate_order,
        'senate_source': ballot.senate_source,
        'receipt_code': ballot.receipt_code,
    }


def entry_hash(prev_hash, payload):
    body = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256((prev_hash + body).encode()).hexdigest()


def checkpoint_message(election_id, seq, head_hash):
    return f'{election_id}:{seq}:{head_hash}'.encode()


def _signature_ok(public_key, election_id, seq, head_hash, signature):
    try:
        public_key.verify(bytes.fromhex(signature), checkpoint_message(election_id, seq, head_hash))
        return True
    except (InvalidSignature, ValueError):
        return False


def find_problems(ballots, checkpoints, public_key, election_id):
    """ballots: list of (seq, ballot_uid, payload, prev_hash, stored_hash) in seq order.
    checkpoints: list of (seq, head_hash, signature_hex).
    Returns a list of (ballot_uid, reason) for everything that does not verify."""
    problems = []
    prev, expected, by_seq = GENESIS, 1, {}
    for seq, uid, payload, prev_hash, stored_hash in ballots:
        if seq != expected or prev_hash != prev:
            problems.append((uid, f'chain broken before ballot #{seq}'))
        if entry_hash(prev_hash, payload) != stored_hash:
            problems.append((uid, f'ballot #{seq} was altered'))
        prev, expected = stored_hash, seq + 1
        by_seq[seq] = (uid, stored_hash)
    for seq, head_hash, signature in checkpoints:
        if not _signature_ok(public_key, election_id, seq, head_hash, signature):
            problems.append((f'CHECKPOINT:{seq}', f'checkpoint #{seq} has an invalid signature'))
        elif seq not in by_seq:
            problems.append((f'MISSING:{seq}', f'ballot #{seq} is missing'))
        elif by_seq[seq][1] != head_hash:
            problems.append((by_seq[seq][0], f'ballot #{seq} does not match its signed checkpoint'))
    return problems
