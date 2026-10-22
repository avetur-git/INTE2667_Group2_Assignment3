# Paper fallback procedure (REQ-08)

What polling officials and the election operations team do when electronic
voting is not available. Automatic failover (see
[REQ-08_AVAILABILITY.md](REQ-08_AVAILABILITY.md)) handles the failure of a
server, a zone or a region. This procedure is for the worst case, when the
platform as a whole is down or cannot be trusted, so that **no eligible voter is
turned away and nobody can vote twice**.

## 1. When to switch to paper

| Situation | Who decides | Scope |
|---|---|---|
| Terminals at one polling place cannot reach the platform for **15 minutes**, and the operations desk confirms the platform is up elsewhere (local network fault) | Officer in Charge (OIC) of the polling place, after calling the operations desk | That polling place |
| The platform is unavailable in both regions for **15 minutes**, or the operations desk declares a major incident | Electoral Commissioner's delegate on duty | All polling places, missions and online voting |
| The platform is reachable but its results cannot be trusted (suspected compromise, ballot chain verification fails, wrong ballot papers shown) | Electoral Commissioner's delegate, advised by the security team | All channels, immediately, regardless of the 15 minutes |

The operations desk records the decision, the time and the person deciding.
When the platform is back, the same person decides when to switch back (section 5).

## 2. Kept ready at every polling place before polling day

- **Printed ballot papers** for every division served by the polling place,
  House and Senate, enough for the full expected turnout plus 20%.
- **Certified list of voters**: a printed copy of the electoral roll for the
  polling place's divisions, produced the evening before polling day, with a
  column for marking voters off. Overseas missions hold the list for their
  registered voters.
- **Declaration vote envelopes** for voters not found on the list.
- **Sealed ballot boxes** with numbered seals, and the fallback log sheet.
- This procedure, the operations desk phone number, and a second way to reach
  it (mobile phone) in case the polling place network is down.

## 3. Switching to paper at a polling place

1. **Stop the terminals.** Close any open ballot on screen without submitting
   it. A voter whose vote was not confirmed with a receipt votes again on
   paper (step 6 explains how the double vote risk is handled).
2. **Record the time** and the reason on the fallback log sheet.
3. **Phone the operations desk.** Get a fallback reference number and the time
   electronic voting is recorded as stopped at this polling place.
4. **Open sealed ballot boxes** and record their seal numbers on the log sheet.
5. **For each voter:**
   1. Ask the standard questions (full name, address, voted before in this
      election?).
   2. Find the voter on the certified list. If they are on it and not already
      marked, mark them off and hand over the House and Senate papers for
      their division.
   3. If they are already marked off, or not on the list, issue a
      **declaration vote**: the ballot papers go into a signed declaration
      envelope that is checked before counting.
   4. The voter marks the papers in a screen and puts them in the ballot box.
6. **Voters who may have voted electronically before the outage.** The
   certified list was printed before polling day and does not show electronic
   votes. Any voter who says they started voting electronically today, or
   whose electronic status cannot be checked, gets a declaration vote, not an
   ordinary vote.
7. **Online voters** see a maintenance page that lists the nearest open
   polling places and any extended hours. **Overseas missions** issue paper
   ballots under the same steps and return them by diplomatic bag.

## 4. During the fallback

- The OIC phones the operations desk every 30 minutes with the number of
  papers issued and the stock remaining. The desk sends more ballot papers if
  stock falls below 25%.
- The operations desk decides on extended voting hours if the outage was long
  or queues formed, and announces them.
- Ballot boxes stay sealed and in sight of officials and scrutineers.

## 5. Returning to electronic voting

1. The operations desk confirms the platform is healthy (all `/health` checks
   passing, ballot chain verification passing) and the delegate approves the
   switch back.
2. **Reconcile before reopening.** The OIC reads or sends the list of voters
   marked off on paper to the operations desk. Each one is recorded on the
   platform as having voted (channel `paper`, polling place, fallback reference)
   **before terminals reopen**, so they cannot vote again electronically. This
   is done by election staff and is written to the audit log.
3. Terminals reopen; the time is recorded on the log sheet and at the desk.
4. Paper ballots stay in their sealed boxes for counting.

## 6. Counting and the one-vote rule

- Paper ballots are counted by hand under the normal scrutiny rules, and the
  totals are entered into the tally next to the electronic results.
- Declaration envelopes are checked against the electronic vote records and
  the paper mark-offs. A declaration vote from a voter who also has an
  electronic vote or another paper mark-off is set aside unopened and referred
  as a possible multiple vote, as with paper elections today.
- After polling, the paper mark-offs from all fallback periods are compared
  with the electronic vote records to find any voter recorded twice.

## 7. Records

Kept with the election records: fallback log sheets, ballot box seal numbers,
fallback reference numbers, operations desk decision log, and the audit log
entries from reconciliation (section 5).

## 8. Practice

Every polling place team runs this procedure once in training before polling
day. The operations desk runs a full fallback drill, including reconciliation,
during the pre-election test period.
