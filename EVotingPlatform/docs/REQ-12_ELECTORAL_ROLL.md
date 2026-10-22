# REQ-12: Real-time eligibility check against the electoral roll

Solution implemented: Option B (a secure, real-time request to the authoritative
roll, carrying only what is needed), with the roll simulated inside the app.

## What was built

- **A roll service.** `app/security/electoral_roll.py` stands in for the AEC's
  electoral roll. The voting platform sends it only a roll reference and a
  one-time request id. It answers with only what an eligibility decision needs:
  enrolled, citizen, date of birth, division and state. Never a name or address.
- **A live check, every time.** `check_eligibility` in
  `app/services/eligibility.py` asks the roll on every call: when the ballot paper
  is shown, when a voter checks their eligibility, and again at the moment of
  casting. Nothing is cached, so a voter removed after logging in is refused.
- **Registered at start-up** in `app/__init__.py` as the `lookup_electoral_roll` hook.

## Trusting the answer

The platform accepts an answer only if all of these hold. Otherwise it refuses
the voter.

1. **Signed by the roll.** Every answer carries an Ed25519 signature from the
   roll's key (`instance/roll_key.pem`). If anyone changes the answer in
   transit, for example turning "removed" into "enrolled", the signature fails.
2. **Tied to this request.** The answer must echo the one-time request id. A
   recorded "enrolled" answer cannot be replayed later for a voter who has
   since been removed.
3. **Fresh.** The answer must have been issued within the last 30 seconds.

## Fail closed

- If the roll cannot be reached, or its answer fails any check above, the voter
  is refused with reason `roll_unavailable` and the failure is logged as
  `ROLL_LOOKUP_FAILED`. The system never lets a voter through because it could not check.
- If the roll's division or state differs from the voter's local record, the
  voter is refused with reason `roll_mismatch`. The ballot paper is built from
  the local record, so a tampered or out-of-date division would give the wrong paper.

## Tests

`tests/test_electoral_roll.py` (8 tests):

- `test_voter_removed_after_login_cannot_vote`: real time; the roll is asked again at casting.
- `test_roll_reveals_only_eligibility_fields`: data minimisation, in both the request and the answer.
- `test_roll_unreachable_fails_closed`: refused and logged.
- `test_forged_answer_is_rejected`: "enrolled" flipped in transit.
- `test_replayed_answer_is_rejected`: an earlier genuine answer reused.
- `test_out_of_date_answer_is_rejected`: correctly signed but two minutes old.
- `test_local_record_that_disagrees_with_roll_is_refused`
- `test_eligible_voter_still_votes_normally`

The tests were checked by removing each protection in turn: the signature check,
the request id check, the freshness check, failing closed, the mismatch check,
data minimisation, and registering the roll at start-up. A test failed in every case.

## Simulated vs real

- The roll runs in the same process as the platform and reads the same voter
  table. In production it is the AEC's separate system, and the one `transport`
  function would become an HTTPS call with mutual TLS, so both sides prove who
  they are. The checks in `lookup_electoral_roll` stay the same.
- The roll's signing key is a file in `instance/`, standing in for an HSM or
  KMS (as in REQ-01, REQ-02 and REQ-20). The platform only needs the public half.

## Assumptions and limitations

1. Because the prototype roll reads the same table the app uses, "real time" here
   means "asked again on every decision, through a trusted boundary". The base
   build already read the local table live; REQ-12 adds the trust in the answer.
2. Refusals for `roll_unavailable` return 403, the same as other eligibility
   refusals, with the reason in `reason_code`. A 503 would describe an outage more precisely.
3. The 30-second freshness window assumes the platform and the roll keep their
   clocks in sync (for example with NTP).
4. Every roll lookup is a round trip. At 40,000 terminals the roll service would
   need to scale with the platform (REQ-09).
5. `instance/roll_key.pem` is created on first run, is git-ignored and must never be committed.
