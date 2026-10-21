"""A software FIDO2 authenticator for tests (REQ-10).

Builds the same registration and assertion responses a real security key
returns to the browser: ES256 key pair, 'none' attestation, authenticator data
with the RP ID hash, flags and sign counter."""
import hashlib
import json
import secrets
import struct

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url

FLAG_UP = 0x01   # user present
FLAG_UV = 0x04   # user verified
FLAG_AT = 0x40   # attested credential data included


class VirtualAuthenticator:
    def __init__(self, rp_id='localhost', origin='http://localhost:3000'):
        self.rp_id = rp_id
        self.origin = origin
        self.private_key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = secrets.token_bytes(32)
        self.sign_count = 0

    def _cose_public_key(self):
        numbers = self.private_key.public_key().public_numbers()
        return cbor2.dumps({1: 2, 3: -7, -1: 1,
                            -2: numbers.x.to_bytes(32, 'big'),
                            -3: numbers.y.to_bytes(32, 'big')})

    def _client_data(self, ceremony, challenge, origin):
        return json.dumps({'type': ceremony, 'challenge': challenge,
                           'origin': origin or self.origin, 'crossOrigin': False}).encode()

    def _auth_data(self, flags, rp_id=None, attested=b''):
        rp_hash = hashlib.sha256((rp_id or self.rp_id).encode()).digest()
        return rp_hash + bytes([flags]) + struct.pack('>I', self.sign_count) + attested

    def create(self, options, origin=None):
        """Answer registration options as navigator.credentials.create() would."""
        client_data = self._client_data('webauthn.create', options['challenge'], origin)
        attested = (b'\x00' * 16 + struct.pack('>H', len(self.credential_id))
                    + self.credential_id + self._cose_public_key())
        auth_data = self._auth_data(FLAG_UP | FLAG_UV | FLAG_AT, attested=attested)
        attestation = cbor2.dumps({'fmt': 'none', 'attStmt': {}, 'authData': auth_data})
        return {
            'id': bytes_to_base64url(self.credential_id),
            'rawId': bytes_to_base64url(self.credential_id),
            'type': 'public-key',
            'response': {'clientDataJSON': bytes_to_base64url(client_data),
                         'attestationObject': bytes_to_base64url(attestation)},
            'clientExtensionResults': {},
        }

    def get(self, options, origin=None, rp_id=None):
        """Answer authentication options as navigator.credentials.get() would."""
        allowed = [base64url_to_bytes(c['id']) for c in options.get('allowCredentials', [])]
        assert not allowed or self.credential_id in allowed, 'key not allowed by server'
        self.sign_count += 1
        client_data = self._client_data('webauthn.get', options['challenge'], origin)
        auth_data = self._auth_data(FLAG_UP | FLAG_UV, rp_id=rp_id)
        signature = self.private_key.sign(auth_data + hashlib.sha256(client_data).digest(),
                                          ec.ECDSA(hashes.SHA256()))
        return {
            'id': bytes_to_base64url(self.credential_id),
            'rawId': bytes_to_base64url(self.credential_id),
            'type': 'public-key',
            'response': {'clientDataJSON': bytes_to_base64url(client_data),
                         'authenticatorData': bytes_to_base64url(auth_data),
                         'signature': bytes_to_base64url(signature),
                         'userHandle': None},
            'clientExtensionResults': {},
        }
