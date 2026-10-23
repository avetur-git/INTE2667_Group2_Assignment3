"""REQ-14 - encrypt sensitive voter and election data at rest and in transit.

At rest, these prove:
  1. Ballot choices and voter addresses are stored as ciphertext, never plain text.
  2. The app still reads them normally, and the count still works.
  3. The same vote is stored differently each time (no patterns to compare).
  4. Altered ciphertext is never decrypted into altered votes, and the REQ-02
     hash chain flags that ballot so the tally leaves it out.
  5. Without the right key, the data cannot be read.
In transit, these prove:
  6. The server accepts TLS 1.3 and refuses TLS 1.2 (a real handshake).
  7. HTTPS responses carry HSTS; plain HTTP responses do not.
"""
import base64
import importlib.util
import os
import socket
import ssl
import stat
import threading
from pathlib import Path

import pytest
from sqlalchemy import text
from werkzeug.serving import make_server

from app import db
from app.models import Ballot, Voter
from app.security.data_protection import DecryptionError, PREFIX, decrypt_text, unprotect_data
from app.security.transport import HSTS, tls_context
from app.seed import seed_demo
from app.services import ballot_store
from tests.test_api_flow import full_vote, login


@pytest.fixture()
def eid(client, app):
    return seed_demo()['election_id']


def voter_record(username):
    return Voter.query.join(Voter.user).filter_by(username=username).one()


def stored_address(voter_id):
    """The address exactly as it sits in the database file."""
    return db.session.execute(text('SELECT address_line FROM voters WHERE id = :id'),
                              {'id': voter_id}).scalar()


# ------------------------------- at rest -------------------------------------

def test_ballot_choices_are_encrypted_in_the_database(client, eid):
    assert full_vote(client, login(client, 'voter1'), eid).status_code == 201
    stored = Ballot.query.one()                     # the raw row, before any decryption
    for field in (stored.house_order, stored.senate_order, stored.senate_atl):
        assert isinstance(field, str) and field.startswith(PREFIX)

    readable = ballot_store.all_ballots(eid)[0]     # the app's own read path
    assert isinstance(readable['house_order'], list) and readable['house_order']


def test_same_choices_are_stored_differently(client, eid):
    """A fresh random nonce per value: identical votes give different ciphertext."""
    for username in ('voter1', 'voter2'):          # same division, same full_vote choices
        assert full_vote(client, login(client, username), eid).status_code == 201
    first, second = Ballot.query.order_by(Ballot.id).all()
    assert unprotect_data(first.house_order) == unprotect_data(second.house_order)
    assert first.house_order != second.house_order


def test_tally_still_counts_encrypted_ballots(client, eid):
    for username in ('voter1', 'voter3'):
        assert full_vote(client, login(client, username), eid).status_code == 201
    admin = login(client, 'admin')
    client.put(f'/api/elections/{eid}/status', json={'status': 'closed'}, headers=admin)
    r = client.post(f'/api/elections/{eid}/tally', headers=admin)
    assert r.status_code == 200 and r.get_json()['flagged_ballots'] == []
    assert len(client.get(f'/api/results/{eid}').get_json()['house']) >= 1


def test_altered_ciphertext_is_rejected_and_flagged(client, eid):
    assert full_vote(client, login(client, 'voter1'), eid).status_code == 201
    ballot = Ballot.query.one()
    raw = bytearray(base64.b64decode(ballot.house_order[len(PREFIX):]))
    raw[-1] ^= 0x01                                 # flip one bit of the stored vote
    ballot.house_order = PREFIX + base64.b64encode(bytes(raw)).decode()
    db.session.commit()

    assert unprotect_data(ballot.house_order) is None        # never decrypted into a fake vote
    admin = login(client, 'admin')
    client.put(f'/api/elections/{eid}/status', json={'status': 'closed'}, headers=admin)
    r = client.post(f'/api/elections/{eid}/tally', headers=admin)
    assert r.status_code == 200
    assert r.get_json()['flagged_ballots'] == [ballot.ballot_uid]   # REQ-02 leaves it out


def test_voter_address_encrypted_at_rest_but_readable_in_the_app(client, eid):
    voter = voter_record('voter1')
    raw = stored_address(voter.id)
    assert raw.startswith(PREFIX) and voter.address_line not in raw

    me = client.get('/api/voters/me', headers=login(client, 'voter1')).get_json()
    assert me['voter']['address_line'] == voter.address_line and not me['voter']['address_line'].startswith(PREFIX)


def test_data_cannot_be_read_with_another_key(client, eid, monkeypatch):
    raw = stored_address(voter_record('voter1').id)
    monkeypatch.setenv('DATA_ENCRYPTION_KEY', base64.b64encode(os.urandom(32)).decode())
    with pytest.raises(DecryptionError):
        decrypt_text(raw)


@pytest.mark.skipif(os.name == 'nt', reason='POSIX file permissions')
def test_key_file_is_readable_by_its_owner_only(app, eid):
    if os.environ.get('DATA_ENCRYPTION_KEY'):
        pytest.skip('key supplied by the environment')
    mode = stat.S_IMODE((Path(app.instance_path) / 'data_key.bin').stat().st_mode)
    assert mode & 0o077 == 0


# ------------------------------- in transit ----------------------------------

def _make_dev_cert(directory):
    path = Path(__file__).resolve().parent.parent / 'scripts' / 'make_dev_cert.py'
    spec = importlib.util.spec_from_file_location('make_dev_cert', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.create_cert(directory)


def _handshake(port, version):
    client_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    client_ctx.check_hostname = False
    client_ctx.verify_mode = ssl.CERT_NONE                # self-signed test certificate
    client_ctx.minimum_version = client_ctx.maximum_version = version
    with socket.create_connection(('127.0.0.1', port), timeout=5) as sock:
        with client_ctx.wrap_socket(sock, server_hostname='localhost') as tls:
            return tls.version()


def test_server_accepts_tls13_and_refuses_tls12(app, tmp_path):
    cert, key = _make_dev_cert(tmp_path)
    server = make_server('127.0.0.1', 0, app, ssl_context=tls_context(cert, key))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert _handshake(server.port, ssl.TLSVersion.TLSv1_3) == 'TLSv1.3'
        with pytest.raises(ssl.SSLError):
            _handshake(server.port, ssl.TLSVersion.TLSv1_2)
    finally:
        server.shutdown()


def test_hsts_header_only_over_https(client):
    assert client.get('/health', base_url='https://localhost').headers.get('Strict-Transport-Security') == HSTS
    assert 'Strict-Transport-Security' not in client.get('/health').headers
