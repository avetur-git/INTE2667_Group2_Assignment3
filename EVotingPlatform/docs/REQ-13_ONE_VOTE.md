# REQ-13: One electronic ballot per voter, across all channels

Solution implemented: Option B (one central "has voted" register shared by every
channel, updated atomically).

## The problem in the base build

Casting a vote did two separate things: ask "has this voter already voted?",
then save a "has voted" record and the ballot. Two requests from the same voter
arriving together, for example online and at a polling station, could both ask
before either had saved, both get "no", and both be stored. This is a race
condition (time of check vs time of use). It was reproduced on the base build:
one voter, two ballots stored. REQ-01's ballot tokens do not prevent it, because
each attempt is issued its own fresh token.

## What was built

- **The database enforces the rule.** `vote_records` has a unique constraint on
  `(voter_id, election_id)` (`app/models/voter.py`). The database itself refuses
  a second row for the same voter and election.
- **One register for every channel.** Online, polling station and diplomatic
  mission voting all write to `vote_records`, so the rule covers all of them.
- **Mark first, then store.** In `cast_vote` (`app/services/voting.py`), the
  "has voted" record is sent to the database (`flush`) before any ballot token
  is issued or ballot stored. If the voter is already marked, the database
  refuses at that point, so a refused attempt leaves no token, ballot or record behind.
- **A clear answer.** The refusal is caught and returned as 409 with reason
  `already_voted`, instead of a server error.
- **Refusals are logged** as `DUPLICATE_VOTE_REFUSED`, with the channel that was
  tried and nothing about any ballot. Repeated attempts are a possible fraud signal.
- **The early check stays.** The `has_already_voted` hook still gives voters a
  fast answer, for example before the ballot paper is shown. It is no longer the
  guarantee; the database constraint is.

## Tests

`tests/test_one_vote.py` (5 tests):

- `test_second_vote_through_another_channel_is_refused`: online, then a polling station.
- `test_race_where_both_requests_pass_the_check`: the early check is forced to
  answer "not voted" (as in a race). The database still refuses, and exactly one
  ballot, one record and one token remain.
- `test_database_refuses_a_second_record_directly`: defence in depth; even code
  that skips the voting service cannot mark a voter twice.
- `test_refused_attempts_are_logged`
- `test_other_voters_are_not_affected`

Without the fix, three of these fail on the base build. They were also checked
by removing the constraint, the early flush and the logging in turn; a test
failed in every case.

## Running it locally

`db.create_all()` does not add a constraint to an existing table. After pulling
this change, delete `instance/evoting.db` and `instance/ballots.db` and run
`flask --app run seed-demo` once.

## Assumptions and limitations

1. The rule covers electronic ballots, as the requirement states. Paper votes
   (pre-poll, postal) are not in `vote_records`; production would need the paper
   process to mark the same register.
2. Since REQ-01, the vote record and the ballot are saved to two database files,
   one after the other, not as one combined step (see REQ-01 assumption 10). A
   crash between the two could leave a voter marked as voted without their
   ballot stored. Production would use one database server, a two-phase commit
   or a reliable queue.
3. SQLite applies the constraint within one database file. A production
   deployment with several database servers would need the register in one
   shared, consistent store.
4. There is no official override (for example a voter who says someone else
   voted with their login). Any override would have to deal with the fact that
   the earlier ballot cannot be found or removed, by design (REQ-01).
