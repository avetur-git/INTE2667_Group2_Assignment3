# REQ-10: Phishing-resistant MFA for privileged users

Staff accounts (`aec_officer`, `commissioner_delegate`, `election_admin`) log in with
their password **and** a FIDO2 security key (WebAuthn). Voters are not affected.

## Why it is phishing-resistant

- The key signs the website origin and the RP ID. A response made on a look-alike
  site fails the origin check, so a phished response is useless.
- Every login uses a fresh 32-byte server challenge that expires after 2 minutes and
  is deleted as soon as it is used. A captured response cannot be replayed.
- The server keeps each key's signature counter and rejects a counter that goes
  backwards, which would point to a cloned key.
- Only the public key is stored. There is no shared secret to steal from the database.

## Switching it on

Enforcement is controlled by `MFA_REQUIRED` and is **off for now** so the group can
keep logging in as staff with a password while other requirements are built.
Registration and verification work whether it is on or off.

| Setting | Default | Meaning |
|---|---|---|
| `MFA_REQUIRED` | `false` | `true` makes staff logins require a security key |
| `WEBAUTHN_RP_ID` | `localhost` | The domain keys are bound to |
| `WEBAUTHN_ORIGINS` | `http://localhost:3000,http://localhost:9001` | Accepted origins (local run, Docker) |

The shared test setup (`tests/conftest.py`) sets `MFA_REQUIRED` to `False` so other
tests can log staff in with just a password. `tests/test_webauthn_mfa.py` turns it on.
This is a test setting only. Production must run with `MFA_REQUIRED=true`.

## Registering a key

1. An operator issues a one-time enrolment code from the server:

   ```
   flask --app run issue-enrolment-code admin
   ```

   The code is single use, expires after 15 minutes, and only its SHA-256 hash is
   stored. Issuing a new code cancels any unused one for that user.
2. The staff member opens `/mfa/enrol`, enters username, password and the code, and
   touches their key. Windows Hello or a phone passkey also work.

Without a security key, use Chrome's virtual authenticator: DevTools → More tools →
WebAuthn → *Enable virtual authenticator environment* → *Add* (protocol ctap2,
transport usb). WebAuthn works on `http://localhost`. Any other host needs HTTPS.

## Login flow

1. `POST /api/auth/webauthn/login/begin` with username and password. Returns
   `mfa_required` and, for staff when enforced, the challenge options.
2. The browser asks the key to sign the challenge (`navigator.credentials.get`).
3. `POST /api/auth/login` with username, password and the signed response in
   `webauthn`. The `verify_mfa` hook checks it. A JWT is only issued if it passes, so
   no admin API can be reached with the password alone.

## Tests

`tests/test_webauthn_mfa.py` uses a software authenticator
(`tests/virtual_authenticator.py`) that builds the same responses a real key does.
It covers the acceptance tests from issue #15 (`test_admin_login_requires_webauthn`,
`test_wrong_origin_rejected`, `test_replayed_assertion_rejected`) plus wrong RP ID,
unregistered key, cloned key counter, expired challenge, and the enrolment code rules.

## Limitations

- There is no self-service key management (list, rename or revoke keys). An admin
  would remove a lost key from the `webauthn_credentials` table and issue a new code.
- Attestation is not checked (`none`). Any FIDO2 authenticator is accepted, not only
  approved models. A real deployment could require attestation from an allow-list.
- The enrolment code is printed on the server console. In production it would be
  delivered over a separate trusted channel.
