"""REQ-14: create a self-signed certificate so the app can run over HTTPS locally.

    python scripts/make_dev_cert.py

Writes instance/dev_tls_cert.pem and instance/dev_tls_key.pem (git-ignored) and
prints the two lines to add to .env. Browsers will warn that the certificate is
self-signed; that is expected for local use. Production uses a certificate from
a certificate authority on the load balancer (infra/load_balancer.tf).
"""
import datetime
import ipaddress
import os
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

INSTANCE = Path(__file__).resolve().parent.parent / 'instance'
CERT, KEY = INSTANCE / 'dev_tls_cert.pem', INSTANCE / 'dev_tls_key.pem'


def create_cert(directory):
    """Write a self-signed certificate and key into `directory`; return their paths."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    cert_path, key_path = directory / CERT.name, directory / KEY.name
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'localhost')])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=30))
            .add_extension(x509.SubjectAlternativeName([
                x509.DNSName('localhost'),
                x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), critical=False)
            .sign(key, hashes.SHA256()))

    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)   # owner-only
    with os.fdopen(fd, 'wb') as f:
        f.write(key.private_bytes(serialization.Encoding.PEM,
                                  serialization.PrivateFormat.PKCS8,
                                  serialization.NoEncryption()))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path


def main():
    create_cert(INSTANCE)
    print(f'Created {CERT.name} and {KEY.name} in instance/ (valid 30 days).')
    print('Add these two lines to your .env file, then run: python run.py')
    print('TLS_CERT_FILE=instance/dev_tls_cert.pem')
    print('TLS_KEY_FILE=instance/dev_tls_key.pem')


if __name__ == '__main__':
    main()
