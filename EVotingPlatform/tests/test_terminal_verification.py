import json
from types import SimpleNamespace

import pytest

from app.security import codesign, terminal_verification
from app.security.hooks import hooks
from app.security.terminal_verification import make_attestation


@pytest.fixture()
def release(tmp_path, app):
    """A signed software release, the approved CA, and an app set to enforce signatures."""
    folder = tmp_path / 'release'
    folder.mkdir()
    (folder / 'terminal_app.py').write_text("print('terminal running')\n")
    ca_key, ca_cert = codesign.create_ca()
    signer_key, signer_cert = codesign.create_signer(ca_key, ca_cert)
    codesign.sign_package(folder, signer_key, signer_cert)
    ca_path = tmp_path / 'ca.crt'
    codesign.save_certificate(ca_path, ca_cert)
    app.config['TERMINAL_CA_PATH'] = str(ca_path)
    app.config['TERMINAL_SIGNATURE_REQUIRED'] = True
    return folder, ca_key, ca_cert, ca_path


def _terminal(signature):
    return SimpleNamespace(terminal_id='PS-0001', software_signature=signature)


def test_terminal_with_signed_software_is_accepted(release):
    folder = release[0]
    allowed, _message = hooks.run('verify_terminal', _terminal(make_attestation(folder)), {})
    assert allowed


def test_terminal_without_a_signature_is_refused(release):
    allowed, message = hooks.run('verify_terminal', _terminal(None), {})
    assert not allowed and 'signature' in message


def test_unreadable_signature_is_refused(release):
    assert not hooks.run('verify_terminal', _terminal('not json'), {})[0]


def test_signature_from_another_authority_is_refused(release, tmp_path):
    folder = tmp_path / 'other'
    folder.mkdir()
    (folder / 'terminal_app.py').write_text('print(1)')
    other_ca_key, other_ca_cert = codesign.create_ca('Some Other CA')
    signer_key, signer_cert = codesign.create_signer(other_ca_key, other_ca_cert)
    codesign.sign_package(folder, signer_key, signer_cert)
    allowed, message = hooks.run('verify_terminal', _terminal(make_attestation(folder)), {})
    assert not allowed and 'approved authority' in message


def test_altered_manifest_is_refused(release):
    folder = release[0]
    data = json.loads(make_attestation(folder))
    data['manifest'] = data['manifest'].replace('terminal_app.py', 'evil.py')
    allowed, message = hooks.run('verify_terminal', _terminal(json.dumps(data)), {})
    assert not allowed and 'does not match' in message


def test_terminal_is_refused_when_no_authority_is_installed(release):
    folder, _ca_key, _ca_cert, ca_path = release
    ca_path.unlink()
    allowed, message = hooks.run('verify_terminal', _terminal(make_attestation(folder)), {})
    assert not allowed and 'authority' in message


def test_check_can_be_switched_off_for_other_tests(app):
    app.config['TERMINAL_SIGNATURE_REQUIRED'] = False
    assert hooks.run('verify_terminal', _terminal(None), {})[0]


def test_a_check_that_wraps_this_one_still_runs_it_first(release):
    """REQ-19 wraps whatever verify_terminal check is registered. Simulate that, twice."""
    folder = release[0]
    for _ in range(2):
        terminal_verification.register_terminal_verification()
        inner = hooks._hooks['verify_terminal']
        hooks.register('verify_terminal', lambda terminal, meta, inner=inner: inner(terminal, meta))
    assert hooks.run('verify_terminal', _terminal(make_attestation(folder)), {})[0]
    assert not hooks.run('verify_terminal', _terminal(None), {})[0]


def test_registering_twice_does_not_stack_checks(app):
    terminal_verification.register_terminal_verification()
    terminal_verification.register_terminal_verification()
    assert hooks._hooks['verify_terminal'] is terminal_verification.verify_signed_terminal