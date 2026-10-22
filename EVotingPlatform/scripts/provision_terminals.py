"""REQ-03: register the signed terminal software release on every terminal.

    python scripts/provision_terminals.py

Run it after:  python scripts/codesign.py init  and  python scripts/codesign.py sign
It copies the signed release (manifest, signature and signer certificate) into
Terminal.software_signature. The server then accepts votes only from terminals that have
a valid signed release. A terminal added later has no signature until this script is run."""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from app import create_app, db  # noqa: E402
from app.models import Terminal  # noqa: E402
from app.security.terminal_verification import make_attestation  # noqa: E402

PACKAGE = BASE / 'terminal_software'


def main():
    if not (PACKAGE / 'manifest.sig').exists():
        print('terminal_software/ is not signed. Run: python scripts/codesign.py sign')
        return 1
    attestation = make_attestation(PACKAGE)
    app = create_app()
    with app.app_context():
        terminals = Terminal.query.all()
        for terminal in terminals:
            terminal.software_signature = attestation
        db.session.commit()
        print(f'Registered the signed software release on {len(terminals)} terminals.')
    return 0


if __name__ == '__main__':
    sys.exit(main())