"""REQ-01: signing key for ballot tokens.

The voter system signs tokens with the private key. The ballot system only needs
the public key to check them. The key is a file in instance/ (git-ignored) that
simulates an HSM or KMS."""
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from flask import current_app

_cache = {}


def token_private_key():
    path = Path(current_app.instance_path) / 'token_key.pem'
    if path not in _cache:
        if path.exists():
            key = serialization.load_pem_private_key(path.read_bytes(), password=None)
        else:
            key = Ed25519PrivateKey.generate()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption()))
        _cache[path] = key
    return _cache[path]


def token_public_key():
    return token_private_key().public_key()
