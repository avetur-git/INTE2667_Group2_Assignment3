"""REQ-03 helper: create the test CA and signer, sign the terminal software, or verify it.

    python scripts/codesign.py init      # once: approved CA + a code-signing certificate
    python scripts/codesign.py sign      # sign terminal_software/
    python scripts/codesign.py verify    # check terminal_software/ against the approved CA

Keys are written to instance/codesign/ (git-ignored). Never commit them."""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from app.security import codesign  # noqa: E402

KEYS = BASE / 'instance' / 'codesign'
PACKAGE = BASE / 'terminal_software'


def init():
    if (KEYS / 'ca.crt').exists():
        print('Already initialised: instance/codesign/ca.crt exists.')
        return 0
    ca_key, ca_cert = codesign.create_ca()
    signer_key, signer_cert = codesign.create_signer(ca_key, ca_cert)
    codesign.save_private_key(KEYS / 'ca.key.pem', ca_key)
    codesign.save_certificate(KEYS / 'ca.crt', ca_cert)
    codesign.save_private_key(KEYS / 'signer.key.pem', signer_key)
    codesign.save_certificate(KEYS / 'signer.crt', signer_cert)
    print('Created the approved CA and a code-signing certificate in instance/codesign/.')
    return 0


def sign():
    signer_key = codesign.load_private_key(KEYS / 'signer.key.pem')
    signer_cert = codesign.load_certificate(KEYS / 'signer.crt')
    codesign.sign_package(PACKAGE, signer_key, signer_cert)
    print('Signed terminal_software/. Fingerprint:', codesign.package_fingerprint(PACKAGE))
    return 0


def verify():
    ok, reason = codesign.verify_package(PACKAGE, codesign.load_certificate(KEYS / 'ca.crt'))
    print('Signature OK.' if ok else f'Signature check FAILED: {reason}')
    return 0 if ok else 1


COMMANDS = {'init': init, 'sign': sign, 'verify': verify}

if __name__ == '__main__':
    if len(sys.argv) != 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        sys.exit(2)
    sys.exit(COMMANDS[sys.argv[1]]())