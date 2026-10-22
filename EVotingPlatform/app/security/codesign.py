"""REQ-03: code signing for the voting-terminal software.

A software package (a folder) is described by a manifest of SHA-256 file hashes.
The manifest is signed with a code-signing certificate that was issued by the
approved certificate authority (CA). A terminal refuses to start unless:
  1. the signer certificate was issued by the approved CA, is in date and is a
     code-signing certificate,
  2. the manifest signature is valid for that certificate, and
  3. every file in the folder matches the manifest (nothing changed, added or removed).

The CA and signer keys in the prototype simulate an HSM / managed PKI."""
import datetime
import hashlib
import json
from pathlib import Path

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

MANIFEST = 'manifest.json'
SIGNATURE = 'manifest.sig'
CERTIFICATE = 'signer.crt'
_SIGNING_FILES = {MANIFEST, SIGNATURE, CERTIFICATE}


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _name(common_name):
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])


def create_ca(common_name='AEC Test Code-Signing CA', days=365):
    """The approved certificate authority. Returns (private_key, certificate)."""
    key = Ed25519PrivateKey.generate()
    start = _now() - datetime.timedelta(minutes=1)
    cert = (x509.CertificateBuilder()
            .subject_name(_name(common_name)).issuer_name(_name(common_name))
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(start).not_valid_after(start + datetime.timedelta(days=days))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .sign(key, None))
    return key, cert


def create_signer(ca_key, ca_cert, common_name='AEC Terminal Software Signer', days=180, start=None):
    """A code-signing certificate issued by the CA. Returns (private_key, certificate)."""
    key = Ed25519PrivateKey.generate()
    start = start or (_now() - datetime.timedelta(minutes=1))
    cert = (x509.CertificateBuilder()
            .subject_name(_name(common_name)).issuer_name(ca_cert.subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(start).not_valid_after(start + datetime.timedelta(days=days))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CODE_SIGNING]), critical=False)
            .sign(ca_key, None))
    return key, cert


def build_manifest(folder):
    """SHA-256 of every file in the package (the signing files and Python caches are skipped)."""
    folder = Path(folder)
    files = {}
    for path in sorted(folder.rglob('*')):
        if not path.is_file() or path.name in _SIGNING_FILES:
            continue
        if '__pycache__' in path.parts or path.suffix == '.pyc':
            continue
        files[path.relative_to(folder).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {'files': files}


def _manifest_bytes(manifest):
    return json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode()


def sign_package(folder, signer_key, signer_cert):
    """Write manifest.json, manifest.sig and signer.crt into the package folder."""
    folder = Path(folder)
    data = _manifest_bytes(build_manifest(folder))
    (folder / MANIFEST).write_bytes(data)
    (folder / SIGNATURE).write_text(signer_key.sign(data).hex())
    (folder / CERTIFICATE).write_bytes(signer_cert.public_bytes(serialization.Encoding.PEM))


def check_certificate(cert, ca_cert):
    """Return (ok, reason) for a signer certificate."""
    try:
        cert.verify_directly_issued_by(ca_cert)
    except (ValueError, TypeError, InvalidSignature):
        return False, 'the certificate was not issued by the approved authority'
    if not cert.not_valid_before_utc <= _now() <= cert.not_valid_after_utc:
        return False, 'the certificate has expired or is not valid yet'
    try:
        usage = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    except x509.ExtensionNotFound:
        return False, 'the certificate is not a code-signing certificate'
    if ExtendedKeyUsageOID.CODE_SIGNING not in usage:
        return False, 'the certificate is not a code-signing certificate'
    return True, ''


def verify_package(folder, ca_cert):
    """Return (ok, reason). ok is True only if the package is exactly what was signed."""
    folder = Path(folder)
    try:
        data = (folder / MANIFEST).read_bytes()
        signature = bytes.fromhex((folder / SIGNATURE).read_text().strip())
        signer = x509.load_pem_x509_certificate((folder / CERTIFICATE).read_bytes())
    except (OSError, ValueError):
        return False, 'the signature files are missing or unreadable'

    ok, reason = check_certificate(signer, ca_cert)
    if not ok:
        return False, reason
    try:
        signer.public_key().verify(signature, data)
    except InvalidSignature:
        return False, 'the signature does not match the manifest'

    try:
        expected = json.loads(data)['files']
    except (ValueError, KeyError, TypeError):
        return False, 'the manifest is unreadable'
    actual = build_manifest(folder)['files']
    changed = sorted(n for n in expected if n in actual and expected[n] != actual[n])
    missing = sorted(n for n in expected if n not in actual)
    added = sorted(n for n in actual if n not in expected)
    if changed or missing or added:
        parts = []
        if changed:
            parts.append('changed: ' + ', '.join(changed))
        if missing:
            parts.append('missing: ' + ', '.join(missing))
        if added:
            parts.append('unexpected: ' + ', '.join(added))
        return False, 'files do not match the signed manifest (' + '; '.join(parts) + ')'
    return True, ''


def package_fingerprint(folder):
    """SHA-256 of the signed manifest, used to identify one exact software version."""
    return hashlib.sha256((Path(folder) / MANIFEST).read_bytes()).hexdigest()


def save_private_key(path, key):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()))


def load_private_key(path):
    return serialization.load_pem_private_key(Path(path).read_bytes(), password=None)


def save_certificate(path, cert):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def load_certificate(path):
    return x509.load_pem_x509_certificate(Path(path).read_bytes())
