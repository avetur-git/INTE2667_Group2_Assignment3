from app import create_app
from app.security.transport import tls_context
import os

app = create_app()

if __name__ == '__main__':
    # Development server
    debug_mode = os.environ.get('FLASK_ENV') == 'development'

    # REQ-14: serve HTTPS (TLS 1.3 only) when a certificate is configured.
    # Create one for local use with: python scripts/make_dev_cert.py
    cert_file, key_file = os.environ.get('TLS_CERT_FILE'), os.environ.get('TLS_KEY_FILE')
    ssl_context = tls_context(cert_file, key_file) if cert_file and key_file else None
    if ssl_context is None:
        print('WARNING: TLS_CERT_FILE / TLS_KEY_FILE not set - serving plain HTTP. '
              'Development only; see docs/REQ-14_ENCRYPTION.md.')

    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 3000)), debug=debug_mode,
            ssl_context=ssl_context)
