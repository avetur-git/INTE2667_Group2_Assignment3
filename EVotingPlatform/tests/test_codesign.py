import datetime
import json

import pytest

from app.security import codesign


@pytest.fixture()
def package(tmp_path):
    """A small terminal software package, signed by a code-signing certificate."""
    folder = tmp_path / 'terminal_software'
    folder.mkdir()
    (folder / 'terminal_app.py').write_text("print('terminal running')\n")
    (folder / 'config.json').write_text('{"version": "1.0"}')
    ca_key, ca_cert = codesign.create_ca()
    signer_key, signer_cert = codesign.create_signer(ca_key, ca_cert)
    codesign.sign_package(folder, signer_key, signer_cert)
    return folder, ca_key, ca_cert


def test_signed_package_passes(package):
    folder, _ca_key, ca_cert = package
    assert codesign.verify_package(folder, ca_cert) == (True, '')


def test_python_cache_files_are_ignored(package):
    folder, _ca_key, ca_cert = package
    (folder / '__pycache__').mkdir()
    (folder / '__pycache__' / 'terminal_app.cpython-313.pyc').write_bytes(b'cache')
    assert codesign.verify_package(folder, ca_cert)[0] is True


def test_modified_file_is_rejected(package):
    folder, _ca_key, ca_cert = package
    (folder / 'terminal_app.py').write_text("print('terminal running')\nprint('extra')\n")
    ok, reason = codesign.verify_package(folder, ca_cert)
    assert not ok and 'terminal_app.py' in reason


def test_added_file_is_rejected(package):
    folder, _ca_key, ca_cert = package
    (folder / 'extra.py').write_text('print(1)')
    ok, reason = codesign.verify_package(folder, ca_cert)
    assert not ok and 'extra.py' in reason


def test_removed_file_is_rejected(package):
    folder, _ca_key, ca_cert = package
    (folder / 'config.json').unlink()
    ok, reason = codesign.verify_package(folder, ca_cert)
    assert not ok and 'config.json' in reason


def test_edited_file_with_rewritten_manifest_is_rejected(package):
    folder, _ca_key, ca_cert = package
    (folder / 'terminal_app.py').write_text("print('evil')\n")
    manifest = codesign.build_manifest(folder)
    (folder / codesign.MANIFEST).write_bytes(
        json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode())
    ok, reason = codesign.verify_package(folder, ca_cert)
    assert not ok and 'signature' in reason


def test_forged_signature_is_rejected(package):
    folder, _ca_key, ca_cert = package
    (folder / codesign.SIGNATURE).write_text('00' * 64)
    ok, reason = codesign.verify_package(folder, ca_cert)
    assert not ok and 'signature' in reason


def test_certificate_from_another_authority_is_rejected(package):
    folder, _ca_key, _ca_cert = package
    _other_key, other_ca_cert = codesign.create_ca('Some Other CA')
    ok, reason = codesign.verify_package(folder, other_ca_cert)
    assert not ok and 'approved authority' in reason


def test_expired_certificate_is_rejected(tmp_path):
    folder = tmp_path / 'terminal_software'
    folder.mkdir()
    (folder / 'terminal_app.py').write_text('print(1)')
    ca_key, ca_cert = codesign.create_ca()
    old_start = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=10)
    signer_key, signer_cert = codesign.create_signer(ca_key, ca_cert, days=5, start=old_start)
    codesign.sign_package(folder, signer_key, signer_cert)
    ok, reason = codesign.verify_package(folder, ca_cert)
    assert not ok and 'expired' in reason


def test_certificate_that_is_not_for_code_signing_is_rejected(tmp_path):
    folder = tmp_path / 'terminal_software'
    folder.mkdir()
    (folder / 'terminal_app.py').write_text('print(1)')
    ca_key, ca_cert = codesign.create_ca()
    codesign.sign_package(folder, ca_key, ca_cert)      # the CA certificate has no code-signing usage
    ok, reason = codesign.verify_package(folder, ca_cert)
    assert not ok and 'code-signing' in reason


def test_missing_signature_files_are_rejected(package):
    folder, _ca_key, ca_cert = package
    (folder / codesign.SIGNATURE).unlink()
    assert codesign.verify_package(folder, ca_cert)[0] is False


def test_fingerprint_changes_when_the_software_changes(tmp_path):
    ca_key, ca_cert = codesign.create_ca()
    signer_key, signer_cert = codesign.create_signer(ca_key, ca_cert)
    fingerprints = []
    for text in ('print(1)', 'print(2)'):
        folder = tmp_path / text[-2]
        folder.mkdir()
        (folder / 'terminal_app.py').write_text(text)
        codesign.sign_package(folder, signer_key, signer_cert)
        fingerprints.append(codesign.package_fingerprint(folder))
    assert fingerprints[0] != fingerprints[1]
