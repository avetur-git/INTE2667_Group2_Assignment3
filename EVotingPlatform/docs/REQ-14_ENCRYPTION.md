# REQ-14: Encrypt sensitive data at rest and in transit

Solution implemented: Option A (TLS 1.3 for network traffic, plus storage
encryption with a managed key), with the most sensitive fields encrypted by the
application itself so the prototype can show it working on SQLite.

## At rest: what was built

- **Encryption module.** `app/security/data_protection.py` encrypts with
  AES-256-GCM (authenticated encryption, as in Lab 2). Every value gets a fresh
  random 96-bit nonce. Any change to the stored bytes makes decryption fail
  instead of returning altered data.
- **Ballot choices.** House and Senate preferences are encrypted through the
  `protect_data` / `unprotect_data` hooks, which every ballot read and write
  already goes through (`app/services/ballot_store.py`). The ballots database
  holds only ciphertext; the tally reads them through the same hooks.
- **Voter addresses.** `address_line`, `suburb` and `postcode` on the Voter model
  use the `EncryptedText` column type. The application reads and writes plain
  text; the database file holds only ciphertext.
- **Stored form:** `enc:v1:<base64 of nonce + ciphertext + tag>`. The `v1`
  label names the algorithm and key version, so a later key or algorithm change
  can tell old values from new ones.
- **The key.** 32 random bytes. Taken from the `DATA_ENCRYPTION_KEY` environment
  variable (base64) when set, which is where production would supply it from a
  KMS or HSM. Otherwise read from `instance/data_key.bin`, created on first use
  with owner-only permissions (0600). The key is never in the database or in Git.
- **Switched on** in `app/__init__.py` (`register_data_protection()`).

## How it fits with other requirements

- **REQ-02 (ballot hash chain):** the chain covers the stored values, which are
  now ciphertext. Changing even one bit of a stored ballot both fails decryption
  and breaks the chain, so the tally flags that ballot and leaves it out.
- **REQ-01 (separation):** a copied `ballots.db` now shows no choices at all,
  not just no voter identity.
- **REQ-12:** the electoral roll lookup still sees the voter record normally; the
  address is decrypted by the model, and the roll never returns it anyway.

## In transit: what was built

- **TLS 1.3 only.** `app/security/transport.py` builds a server TLS context whose
  minimum version is TLS 1.3. `run.py` serves HTTPS with it when `TLS_CERT_FILE`
  and `TLS_KEY_FILE` are set, and warns when they are not.
- **Local certificate.** `python scripts/make_dev_cert.py` creates a self-signed
  certificate and key in `instance/` (git-ignored, key 0600) and prints the two
  `.env` lines to add. Browsers warn about a self-signed certificate; that is
  expected locally.
- **HSTS.** Every HTTPS response carries
  `Strict-Transport-Security: max-age=31536000; includeSubDomains`, so the
  browser keeps using HTTPS for this site even if a link says `http://`.

## Running it over HTTPS

```
python scripts/make_dev_cert.py
# add the two printed lines to .env
python run.py          # then open https://localhost:3000
```

## Tests

`tests/test_encryption.py` (9 tests):

- `test_ballot_choices_are_encrypted_in_the_database`: raw rows are ciphertext; the app still reads lists.
- `test_same_choices_are_stored_differently`: identical votes, different ciphertext.
- `test_tally_still_counts_encrypted_ballots`
- `test_altered_ciphertext_is_rejected_and_flagged`: one flipped bit is never
  decrypted into a fake vote, and the REQ-02 chain flags the ballot.
- `test_voter_address_encrypted_at_rest_but_readable_in_the_app`
- `test_data_cannot_be_read_with_another_key`
- `test_key_file_is_readable_by_its_owner_only`
- `test_server_accepts_tls13_and_refuses_tls12`: a real TLS handshake against the running app.
- `test_hsts_header_only_over_https`

The tests were checked by breaking each protection in turn: storing ballots in
plain text, using a fixed nonce, letting a tampered value crash the count,
storing addresses in plain text, allowing TLS 1.2, dropping HSTS, and creating
the key file world-readable. A test failed in every case.

`tests/test_receipts.py` (REQ-20) needed one line changed: two ballots with the
same choices no longer store identical values, so the test now compares the
decrypted choices and also checks that the stored values differ.

## Simulated vs real

- **Production at rest** is described in the REQ-06 Terraform: the database uses
  `storage_encrypted = true` with a customer-managed KMS key
  (`infra/database.tf`). Application-level encryption of ballots and addresses
  is kept on top of that, so a database administrator or a leaked backup still
  sees only ciphertext.
- **Production in transit** terminates HTTPS at the load balancer
  (`infra/load_balancer.tf`). Its policy `ELBSecurityPolicy-TLS13-1-2-2021-06`
  allows TLS 1.3 and TLS 1.2. For TLS 1.3 only, it would change to
  `ELBSecurityPolicy-TLS13-1-3-2021-06`; some older clients would then be refused.
- The prototype's key file stands in for a KMS or HSM, like the signing keys in
  REQ-01, REQ-02, REQ-12 and REQ-20.

## Assumptions and limitations

1. Only the most sensitive fields are encrypted by the application: ballot
   choices and voter addresses. Names stay searchable for AEC officers, and date
   of birth, division and state are needed for eligibility and counting. Full
   storage encryption covers the rest in production.
2. Encrypted fields cannot be searched or sorted in the database.
3. Ballot ciphertext is not bound to its ballot id (no associated data), so it
   could in principle be swapped between two ballots. The REQ-02 hash chain covers
   both the ballot id and the stored ciphertext, so a swap is still detected.
4. Ballots and addresses stored before this change stay in plain text until
   rewritten. Run a fresh `flask --app run seed-demo` (after deleting
   `instance/evoting.db` and `instance/ballots.db`) for a fully encrypted demo database.
5. Losing the key means losing the encrypted data. Production would back the key
   up in the KMS and rotate it, using the `v1` label to tell key versions apart.
6. Behind a load balancer that terminates TLS, Flask sees plain HTTP, so HSTS
   would be set by the load balancer or by trusting its forwarded headers.
7. Private keys that were committed to the repo earlier have been removed, but
   they remain in Git history. New keys are generated per machine, so the old ones
   no longer sign or decrypt anything in use.
