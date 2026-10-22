"""REQ-03: secure-boot loader for a voting terminal.

    python scripts/start_terminal.py

The terminal software in terminal_software/ only starts if its signature verifies
against the approved certificate authority (instance/codesign/ca.crt)."""
import runpy
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from app.security.secure_boot import start_allowed  # noqa: E402

PACKAGE = BASE / 'terminal_software'
APPROVED_CA = BASE / 'instance' / 'codesign' / 'ca.crt'


def main():
    allowed, message = start_allowed(PACKAGE, APPROVED_CA)
    if not allowed:
        print(f'REFUSED TO START: {message}')
        return 1
    print(f'Signature OK: {message}')
    runpy.run_path(str(PACKAGE / 'terminal_app.py'), run_name='__main__')
    return 0


if __name__ == '__main__':
    sys.exit(main())