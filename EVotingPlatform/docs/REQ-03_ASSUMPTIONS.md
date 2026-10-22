# REQ-03 assumptions and limits: verify the digital signature of voting-terminal software

## What is implemented
- A software package (the `terminal_software/` folder) is described by a manifest of SHA-256 hashes of every file.
- The manifest is signed with an Ed25519 code-signing certificate issued by the approved certificate authority (CA).
- At start-up (`scripts/start_terminal.py`) a terminal refuses to run unless the certificate, the signature and every file match.
- The server keeps the signed release (manifest, signature, certificate) on each terminal record and refuses votes from a
  terminal whose release does not verify (`verify_terminal` hook, `app/security/terminal_verification.py`).

## Assumptions
1. The approved CA and signer keys in `instance/codesign/` simulate an HSM or managed PKI. In production the CA private key
   would never leave the HSM. These keys are never committed (`instance/` is ignored by git).
2. The server holds the approved CA certificate (`instance/codesign/ca.crt`, or `TERMINAL_CA_PATH`). Replacing it is an
   administrator action. If no CA certificate is installed, every terminal is refused (fail closed).
3. A signer certificate is accepted only if it was issued by the approved CA, is in date, and has the code-signing usage.
4. Any change to the package (a changed, added or removed file) invalidates the signature.
5. Registering a signed release on a terminal (`scripts/provision_terminals.py`) is a trusted administrative step done by
   authorised staff when a terminal is provisioned or updated.
6. Terminals are provisioned and updated by authorised staff only, so the stored release is the one that was installed.
7. `TERMINAL_SIGNATURE_REQUIRED` defaults to on. Tests switch it off so that other requirements can be tested alone.
8. The signature check runs first. REQ-19 (mission allow-list and location checks) wraps the registered check, so REQ-19
   checks and their audit entries follow once the software signature passes.

## Known limits
- The server checks the signed release recorded for a terminal. Without hardware attestation (TPM or secure boot) it cannot
  prove the terminal is running exactly that release at the moment of voting. This is the main residual risk.
- Certificate revocation (CRL or OCSP) is not implemented. A compromised signer is handled by expiry and by replacing
  the approved CA certificate.
- There is no trusted timestamp, so releases signed with a certificate that has since expired must be re-signed.
- The prototype signs and verifies a folder of Python files. A production terminal would use a signed, locked-down image.