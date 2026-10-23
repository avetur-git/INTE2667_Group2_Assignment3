"""
REQ-14 - Encrypt sensitive data at rest
=======================================

Sensitive values are encrypted by the application before they reach the
database, so a copied database file, backup or disk shows only ciphertext:

  * ballot contents (House and Senate preferences)  - via the protect_data /
    unprotect_data hooks that every ballot read and write already goes through
  * voter addresses (address line, suburb, postcode) - via the EncryptedText
    column type on the Voter model

Algorithm: AES-256-GCM (authenticated encryption, as in Lab 2). Each value gets
a fresh random 96-bit nonce, so the same vote stored twice looks different, and
any change to the stored bytes makes decryption fail instead of returning
altered data.

Stored form:  enc:v1:<base64 of nonce + ciphertext + tag>
The "v1" label names the algorithm and key version, so the platform can move to
a new key or algorithm later without guessing what old values are.

Key: 32 random bytes, taken from the DATA_ENCRYPTION_KEY environment variable
(base64) when set - production would supply it from a KMS or HSM - otherwise
from instance/data_key.bin, created on first use. The key is never stored in
the database or in Git.
"""
import base64
import json
import os
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from flask import current_app
from sqlalchemy.types import Text, TypeDecorator

from app.security.hooks import hooks

PREFIX = 'enc:v1:'
NONCE_BYTES = 12
_cache = {}


class DecryptionError(Exception):
    """A stored value could not be decrypted: wrong key, or it was altered."""


def _key():
    """The 256-bit data key: from the environment if given, else instance/data_key.bin."""
    from_env = os.environ.get('DATA_ENCRYPTION_KEY')
    if from_env:
        key = base64.b64decode(from_env)
        if len(key) != 32:
            raise ValueError('DATA_ENCRYPTION_KEY must be 32 bytes, base64-encoded')
        return key
    path = Path(current_app.instance_path) / 'data_key.bin'
    if path not in _cache:
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            # Create the file readable by its owner only (0600).
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as f:
                f.write(AESGCM.generate_key(bit_length=256))
        _cache[path] = path.read_bytes()
    return _cache[path]


def is_encrypted(value):
    return isinstance(value, str) and value.startswith(PREFIX)


def encrypt_text(plaintext):
    nonce = os.urandom(NONCE_BYTES)
    sealed = AESGCM(_key()).encrypt(nonce, plaintext.encode('utf-8'), None)
    return PREFIX + base64.b64encode(nonce + sealed).decode('ascii')


def decrypt_text(stored):
    try:
        raw = base64.b64decode(stored[len(PREFIX):])
        return AESGCM(_key()).decrypt(raw[:NONCE_BYTES], raw[NONCE_BYTES:], None).decode('utf-8')
    except (InvalidTag, ValueError) as exc:
        raise DecryptionError('Stored value could not be decrypted') from exc


# ---------------------------------------------------------------------------
# Ballots: the protect_data / unprotect_data hooks used by ballot_store
# ---------------------------------------------------------------------------

def protect_data(value):
    """Encrypt a ballot field (a list or dict) before it is stored."""
    if value is None:
        return None
    return encrypt_text(json.dumps(value, sort_keys=True))


def unprotect_data(value):
    """Decrypt a ballot field after it is read.

    - A value that is not in the encrypted form is returned unchanged (ballots
      stored before REQ-14). REQ-02's hash chain still covers those.
    - A value that fails to decrypt returns None rather than crashing the count.
      It can only fail if the stored bytes were altered, and the REQ-02 hash
      chain then flags that ballot so the tally leaves it out."""
    if not is_encrypted(value):
        return value
    try:
        return json.loads(decrypt_text(value))
    except DecryptionError:
        return None


# ---------------------------------------------------------------------------
# Voter details: a column type that encrypts on save and decrypts on load
# ---------------------------------------------------------------------------

class EncryptedText(TypeDecorator):
    """A text column stored encrypted. Code reads and writes plain strings;
    the database only ever holds ciphertext. Not searchable or sortable."""
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None or is_encrypted(value):
            return value
        return encrypt_text(str(value))

    def process_result_value(self, value, dialect):
        if value is None or not is_encrypted(value):
            return value          # written before REQ-14
        return decrypt_text(value)


def register_data_protection():
    hooks.register('protect_data', protect_data)
    hooks.register('unprotect_data', unprotect_data)
