from app.security import codesign
from app.security.secure_boot import start_allowed


def _signed_package(tmp_path):
    folder = tmp_path / 'terminal_software'
    folder.mkdir()
    (folder / 'terminal_app.py').write_text("print('terminal running')\n")
    ca_key, ca_cert = codesign.create_ca()
    signer_key, signer_cert = codesign.create_signer(ca_key, ca_cert)
    codesign.sign_package(folder, signer_key, signer_cert)
    ca_path = tmp_path / 'ca.crt'
    codesign.save_certificate(ca_path, ca_cert)
    return folder, ca_path


def test_signed_software_is_allowed_to_start(tmp_path):
    folder, ca_path = _signed_package(tmp_path)
    allowed, message = start_allowed(folder, ca_path)
    assert allowed and 'verified' in message


def test_tampered_software_is_refused(tmp_path):
    folder, ca_path = _signed_package(tmp_path)
    (folder / 'terminal_app.py').write_text("print('evil')\n")
    allowed, message = start_allowed(folder, ca_path)
    assert not allowed and 'terminal_app.py' in message


def test_unsigned_software_is_refused(tmp_path):
    folder, ca_path = _signed_package(tmp_path)
    (folder / codesign.SIGNATURE).unlink()
    assert start_allowed(folder, ca_path)[0] is False


def test_start_is_refused_without_an_approved_authority(tmp_path):
    folder, ca_path = _signed_package(tmp_path)
    ca_path.unlink()
    allowed, message = start_allowed(folder, ca_path)
    assert not allowed and 'approved certificate authority' in message