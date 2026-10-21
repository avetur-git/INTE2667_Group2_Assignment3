# REQ-20: Non-identifying digital receipt

After a ballot is recorded, the voter gets a receipt signed by the election
authority. It proves the ballot was recorded without showing who cast it, how
they voted or when.

## The receipt

```json
{
  "receipt_code": "3F9A1C0B7D2E4A68",
  "election_id": 1,
  "date": "2026-10-22",
  "ballot_hash": "9c1e...e04b",
  "key_id": "a41f0c2d9b7e1356",
  "signature": "5d0f...0a0c"
}
```

| Field | What it is | Why it reveals nothing |
|---|---|---|
| `receipt_code` | 16 random hex characters (`secrets.token_hex`), made by the server when the ballot is stored | Not derived from the voter, the time or the choices |
| `election_id` | Which election | The same for every voter |
| `date` | Day the ballot was recorded | Never the time; ballots store only the date (REQ-01) |
| `ballot_hash` | The REQ-02 chain hash of the ballot | Also covers the random `ballot_uid` and the previous ballot's hash, which the voter is never given |
| `key_id` | First 16 hex characters of SHA-256 of the public key | Says which key signed it, for key rotation |
| `signature` | Ed25519 signature over the other five fields | |

**What is signed:** the five fields as JSON with sorted keys and no spaces, for
example `{"ballot_hash":"…","date":"…","election_id":1,"key_id":"…","receipt_code":"…"}`.
Changing any field makes the signature check fail.

## Public endpoints (no login)

| Endpoint | Returns |
|---|---|
| `GET /api/receipts/public-key` | The Ed25519 verification key (hex and PEM) and its `key_id` |
| `GET /api/receipts/<code>` | `found`, the signed receipt fields, and `valid`. Unknown code: 404 |
| `GET /api/elections/<id>/bulletin-board` | Every stored ballot once: code, hash and signature. 403 until voting has closed |

**What `valid` means:** the lookup rebuilds the receipt from the ballot as it is
stored now and checks it against the signature saved when the receipt was issued.
If someone changes a stored ballot's hash, `valid` becomes `false`.

**Bulletin board order:** entries are sorted by receipt code and never show
sequence numbers or the hash-chain links. Ballot ids follow the same order as
`vote_records` (REQ-01 assumption 9), so a board in voting order would let anyone
who knows the order people voted in match voters to receipts.

## Front end

- **Vote page:** after voting, shows the code, date, a shortened hash and whether
  the receipt is signed, with a **Download receipt** button that saves the full
  receipt as JSON.
- **Results page:** a **Check my receipt** box that calls the public lookup and says
  whether the ballot was recorded and the signature is valid.

## Why the receipt cannot be used to prove a vote

A voter who is pressured to show how they voted can only show the receipt and
claim their choices. To prove the claim they would have to rebuild `ballot_hash`
from those choices. That needs the ballot's random `ballot_uid` and the previous
ballot's hash, and the voter has neither. So the receipt proves *that* they voted,
never *how*. (`test_receipt_does_not_let_voter_prove_their_vote`)

## Failure handling

- **A refused or failed ballot gets no receipt.** The receipt is only issued after
  the ballot is committed.
- **Signing problems never cancel a recorded vote.** `issue_receipt` runs after the
  commit. If the ballot has no hash (REQ-02 not active) or the signature cannot be
  saved, the voter still gets their code with `signature: null`, the server logs
  an error, and the vote stays counted. A receipt is never signed without a hash.

## Key handling

The receipt key is `instance/receipt_key.pem` (git-ignored), created on first use,
the same way as REQ-01's token key and REQ-02's ledger key. **Only documented, not
built:**

- **HSM:** in the real system the private key lives in a hardware security module.
  The application asks the HSM to sign and never sees the key.
- **Key rotation:** a new key gets a new `key_id`. Old public keys stay published
  under their ids, so receipts signed before a rotation can still be checked.

## Only documented, not built

- **Independent publication:** the bulletin board should be copied, when voting
  closes, to a location the election authority does not control (for example a
  public repository or several observers' mirrors). That way the authority cannot
  quietly edit it later.
- **End-to-end verifiable voting (Option C, rejected for cost):** a scheme where
  voters can check their *choices* were counted correctly, without revealing them
  (for example homomorphic tallying or mix-nets). It gives stronger guarantees but
  needs specialist cryptography, independent audits and voter education, which was
  out of proportion for this project.

## Limitations

- **The ordering link inside the database remains.** The public board hides the
  order, but a database insider can still match ballots to `vote_records` by
  insertion order (REQ-01 assumption 9). The criterion "the receipt code cannot be
  linked back to the voter from the database tables" holds for the tables' contents
  (no voter-side table contains the code), not for row order. REQ-01's planned batch
  shuffling would close this.
- **The lookup has no rate limiting.** Guessing a 64-bit code is impractical, but a
  real deployment would still rate-limit it (REQ-07).

## Tests

`tests/test_receipts.py` has all 16 tests listed in issue #7, plus:

- checks that every receipt field is covered by the signature
- a tampered ballot hash failing the lookup check
- the "cannot prove your vote" test
- an unknown election's bulletin board
- a ballot without a hash still being recorded, with an unsigned receipt
