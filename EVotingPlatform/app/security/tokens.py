import secrets

from cryptography.exceptions import InvalidSignature

from app.security.keys import token_private_key, token_public_key


class InvalidToken(Exception):
    pass


def issue_token():
    """Voter side: a random single-use token, signed, and NOT derived from the voter."""
    nonce = secrets.token_hex(16)
    sig = token_private_key().sign(nonce.encode()).hex()
    return f'{nonce}.{sig}'


def verify_token(token):
    """Ballot side: return the nonce if the signature is valid, otherwise raise InvalidToken."""
    try:
        nonce, sig = token.split('.')
        token_public_key().verify(bytes.fromhex(sig), nonce.encode())
    except (ValueError, AttributeError, InvalidSignature) as exc:
        raise InvalidToken('Invalid ballot token') from exc
    return nonce
