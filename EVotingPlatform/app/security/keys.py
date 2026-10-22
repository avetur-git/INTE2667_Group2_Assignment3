"""REQ-01 / REQ-02 / REQ-20: signing keys.

token_key   - the voter system signs one-time ballot tokens (REQ-01).
ledger_key  - signs ballot hash-chain checkpoints (REQ-02).
receipt_key - signs voters' ballot receipts (REQ-20).
All are files in instance/ (git-ignored) that simulate an HSM or KMS.

REQ-08: with several app replicas, instance/ is a volume they share, so every
replica signs and verifies with the same keys. A key is created at most once:
whichever replica writes it first wins, and the others load that file."""
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from flask import current_app

_cache = {}


def _private_key(name):
    path = Path(current_app.instance_path) / f'{name}.pem'
    if path not in _cache:
        if not path.exists():
            _create_once(path, Ed25519PrivateKey.generate())
        _cache[path] = serialization.load_pem_private_key(path.read_bytes(), password=None)
    return _cache[path]


def _create_once(path, key):
    """Write `key` to `path` unless another replica already has. The key is
    written to a temporary file and then hard-linked into place, which fails if
    the name exists, so no replica can overwrite or half-read another's key."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f'.{path.name}.{os.getpid()}.tmp')
    tmp.write_bytes(key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()))
    try:
        os.link(tmp, path)
    except FileExistsError:
        pass   # another replica created it first; the caller loads that one
    finally:
        tmp.unlink()


def token_private_key():
    return _private_key('token_key')


def token_public_key():
    return token_private_key().public_key()


def ledger_private_key():
    return _private_key('ledger_key')


def ledger_public_key():
    return ledger_private_key().public_key()


def receipt_private_key():
    return _private_key('receipt_key')


def receipt_public_key():
    return receipt_private_key().public_key()
