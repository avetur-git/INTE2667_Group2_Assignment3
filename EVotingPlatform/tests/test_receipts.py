"""REQ-20: non-identifying, signed ballot receipts."""
import json
import re

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from sqlalchemy import select

from app import db
from app.models import Ballot, Candidate, ReceiptSignature, User
from app.security.chain import ballot_payload, entry_hash
from app.security.data_protection import unprotect_data
from app.security.receipts import verify_receipt
from app.seed import seed_demo
from tests.test_api_flow import full_vote, login

RECEIPT_FIELDS = {'receipt_code', 'election_id', 'date', 'ballot_hash', 'key_id', 'signature'}


@pytest.fixture()
def election_id(app):
    return seed_demo()['election_id']


def vote(client, username, election_id):
    r = full_vote(client, login(client, username), election_id)
    assert r.status_code == 201, r.get_json()
    return r.get_json()['receipt']


def close_voting(client, election_id):
    r = client.put(f'/api/elections/{election_id}/status', json={'status': 'closed'},
                   headers=login(client, 'admin'))
    assert r.status_code == 200


def canonical(receipt):
    """The signed message, rebuilt from the published format (not the app's code)."""
    fields = {k: receipt[k] for k in ('receipt_code', 'election_id', 'date', 'ballot_hash', 'key_id')}
    return json.dumps(fields, sort_keys=True, separators=(',', ':')).encode()


# ------------------------------------------------ issuing (acceptance, issue #7)

def test_successful_ballot_returns_signed_receipt(client, election_id):
    receipt = vote(client, 'voter1', election_id)
    assert set(receipt) == RECEIPT_FIELDS
    assert receipt['signature']
    assert verify_receipt(receipt)


def test_refused_ballot_returns_no_receipt(client, election_id):
    voter = login(client, 'voter2')
    assert full_vote(client, voter, election_id).status_code == 201
    second = client.post('/api/voting/cast', json={'election_id': election_id, 'channel': 'online',
                                                   'house_preferences': [1]}, headers=voter)  # already voted
    assert second.status_code == 409 and 'receipt' not in second.get_json()
    bad = client.post('/api/voting/cast', json={'election_id': election_id, 'house_preferences': []},
                      headers=login(client, 'voter1'))        # invalid ballot
    assert bad.status_code >= 400 and 'receipt' not in bad.get_json()
    assert ReceiptSignature.query.count() == 1


def test_receipt_signature_verifies_with_public_key(client, election_id):
    receipt = vote(client, 'voter1', election_id)
    published = client.get('/api/receipts/public-key').get_json()
    key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(published['public_key_hex']))
    key.verify(bytes.fromhex(receipt['signature']), canonical(receipt))   # raises if invalid
    assert published['key_id'] == receipt['key_id']


@pytest.mark.parametrize('field, value', [('receipt_code', '0000000000000000'), ('election_id', 999),
                                          ('date', '1999-01-01'), ('ballot_hash', 'f' * 64)])
def test_modified_receipt_fails_signature_check(client, election_id, field, value):
    receipt = vote(client, 'voter1', election_id)
    forged = {**receipt, field: value}
    assert not verify_receipt(forged)
    key = Ed25519PublicKey.from_public_bytes(
        bytes.fromhex(client.get('/api/receipts/public-key').get_json()['public_key_hex']))
    with pytest.raises(InvalidSignature):
        key.verify(bytes.fromhex(forged['signature']), canonical(forged))


def test_receipt_contains_ballot_hash(client, election_id):
    receipt = vote(client, 'voter1', election_id)
    ballot = Ballot.query.filter_by(receipt_code=receipt['receipt_code']).one()
    assert ballot.ballot_hash and receipt['ballot_hash'] == ballot.ballot_hash


# ---------------------------------------------------------------------- lookup

def test_public_lookup_returns_hash_for_valid_code(client, election_id):
    receipt = vote(client, 'voter1', election_id)
    for code in (receipt['receipt_code'], receipt['receipt_code'].lower()):
        r = client.get(f'/api/receipts/{code}')      # no login needed
        assert r.status_code == 200
        body = r.get_json()
        assert body['found'] is True and body['valid'] is True
        assert body['ballot_hash'] == receipt['ballot_hash']
        assert body['signature'] == receipt['signature']


def test_public_lookup_returns_not_found_for_unknown_code(client, election_id):
    vote(client, 'voter1', election_id)
    for code in ('0123456789ABCDEF', 'not-a-code', '%27%20OR%201%3D1'):
        r = client.get(f'/api/receipts/{code}')
        assert r.status_code == 404 and r.get_json()['found'] is False


def test_tampered_ballot_hash_fails_lookup_check(client, election_id):
    receipt = vote(client, 'voter1', election_id)
    Ballot.query.filter_by(receipt_code=receipt['receipt_code']).update({'ballot_hash': 'e' * 64})
    db.session.commit()
    body = client.get(f"/api/receipts/{receipt['receipt_code']}").get_json()
    assert body['found'] is True and body['valid'] is False


# ------------------------------------------------- nothing identifying or revealing

def test_receipt_does_not_contain_voter_identity(client, election_id):
    receipt = vote(client, 'voter1', election_id)
    user = User.query.filter_by(username='voter1').one()
    text = json.dumps(receipt)
    assert set(receipt) == RECEIPT_FIELDS
    for value in (user.username, user.email, user.voter.first_name, user.voter.last_name):
        assert value not in text


def test_receipt_does_not_contain_vote_selection(client, election_id):
    receipt = vote(client, 'voter1', election_id)
    ballot = Ballot.query.filter_by(receipt_code=receipt['receipt_code']).one()
    names = [c.name for c in Candidate.query.all()]
    text = json.dumps(receipt)
    assert not any(name in text for name in names)
    assert ballot.ballot_uid not in text                       # needed to rebuild the hash


def test_receipt_does_not_contain_exact_vote_time(client, election_id):
    receipt = vote(client, 'voter1', election_id)
    assert re.fullmatch(r'\d{4}-\d{2}-\d{2}', receipt['date'])
    assert not re.search(r'\d{2}:\d{2}', json.dumps(receipt))


def test_receipt_code_not_linked_to_voter_in_database(client, election_id):
    receipt = vote(client, 'voter1', election_id)
    code = receipt['receipt_code']
    # db.metadata holds only the main (voter-side) database: users, voters,
    # vote_records, audit_logs... The ballot database has its own metadata.
    voter_side = db.metadata.sorted_tables
    assert {'users', 'voters', 'vote_records', 'audit_logs'} <= {t.name for t in voter_side}
    for table in voter_side:
        rows = db.session.execute(select(table)).all()
        assert code not in str(rows), f'receipt code found in {table.name}'
    # ...and the ballot-side tables have no column that could identify a voter
    for model in (Ballot, ReceiptSignature):
        columns = {c.name for c in model.__table__.columns}
        assert not columns & {'voter_id', 'user_id', 'username', 'session_id', 'ip_address', 'voted_at'}


def test_receipt_does_not_let_voter_prove_their_vote(client, election_id):
    """Everything the voter knows (receipt + their choices) cannot rebuild the hash,
    because it also covers the random ballot_uid and the previous ballot's hash."""
    receipt = vote(client, 'voter1', election_id)
    ballot = Ballot.query.filter_by(receipt_code=receipt['receipt_code']).one()
    known_to_voter = {**ballot_payload(ballot), 'ballot_uid': None}
    assert entry_hash('', known_to_voter) != receipt['ballot_hash']
    assert entry_hash(ballot.prev_hash, ballot_payload(ballot)) == receipt['ballot_hash']


def test_ballot_hash_includes_random_ballot_id(client, election_id):
    first, second = vote(client, 'voter1', election_id), vote(client, 'voter2', election_id)
    b1 = Ballot.query.filter_by(receipt_code=first['receipt_code']).one()
    b2 = Ballot.query.filter_by(receipt_code=second['receipt_code']).one()
    assert unprotect_data(b1.house_order) == unprotect_data(b2.house_order)   # same choices...
    assert b1.house_order != b2.house_order        # ...stored differently (REQ-14 encryption)
    assert b1.ballot_hash != b2.ballot_hash                     # ...different hashes
    assert 'ballot_uid' in ballot_payload(b1)
    changed = {**ballot_payload(b1), 'ballot_uid': 'another-random-id'}
    assert entry_hash(b1.prev_hash, changed) != b1.ballot_hash


# -------------------------------------------------------------- bulletin board

def test_bulletin_board_lists_each_ballot_once(client, election_id):
    receipts = [vote(client, u, election_id) for u in ('voter1', 'voter2')]
    close_voting(client, election_id)
    board = client.get(f'/api/elections/{election_id}/bulletin-board').get_json()
    codes = [e['receipt_code'] for e in board['entries']]
    assert board['total'] == len(codes) == Ballot.query.filter_by(election_id=election_id).count()
    assert len(set(codes)) == len(codes)
    assert set(codes) == {r['receipt_code'] for r in receipts}
    assert codes == sorted(codes)          # never the order ballots were cast in
    by_code = {e['receipt_code']: e for e in board['entries']}
    for r in receipts:
        assert by_code[r['receipt_code']]['ballot_hash'] == r['ballot_hash']
        assert by_code[r['receipt_code']]['signature'] == r['signature']


def test_bulletin_board_hidden_until_voting_closed(client, election_id):
    vote(client, 'voter1', election_id)
    assert client.get(f'/api/elections/{election_id}/bulletin-board').status_code == 403
    close_voting(client, election_id)
    assert client.get(f'/api/elections/{election_id}/bulletin-board').status_code == 200


def test_bulletin_board_contains_no_identity_or_choices(client, election_id):
    vote(client, 'voter1', election_id)
    close_voting(client, election_id)
    board = client.get(f'/api/elections/{election_id}/bulletin-board').get_json()
    for entry in board['entries']:
        assert set(entry) == {'receipt_code', 'ballot_hash', 'signature'}   # no seq, prev_hash or time
    text = json.dumps(board)
    assert 'voter1' not in text
    assert not any(c.name in text for c in Candidate.query.all())


def test_bulletin_board_unknown_election(client, app):
    assert client.get('/api/elections/9999/bulletin-board').status_code == 404


# ---------------------------------------------------------------- key and faults

def test_public_key_endpoint_returns_verification_key(client, app):
    from cryptography.hazmat.primitives.serialization import load_pem_public_key
    body = client.get('/api/receipts/public-key').get_json()
    assert body['algorithm'] == 'Ed25519' and len(body['key_id']) == 16
    pem_key = load_pem_public_key(body['public_key_pem'].encode())
    assert isinstance(pem_key, Ed25519PublicKey)
    assert pem_key.public_bytes_raw().hex() == body['public_key_hex']


def test_ballot_without_hash_still_recorded_but_receipt_unsigned(client, election_id):
    """If REQ-02 hashing is missing, never sign; the vote must still count."""
    from app.security.ballot_chain import register_ballot_chain
    from app.security.hooks import hooks
    hooks.reset('on_ballot_stored')
    try:
        r = full_vote(client, login(client, 'voter1'), election_id)
    finally:
        register_ballot_chain()
    assert r.status_code == 201
    receipt = r.get_json()['receipt']
    assert receipt['signature'] is None and receipt['ballot_hash'] is None
    assert Ballot.query.filter_by(receipt_code=receipt['receipt_code']).count() == 1
    assert ReceiptSignature.query.count() == 0
    assert client.get(f"/api/receipts/{receipt['receipt_code']}").status_code == 404
