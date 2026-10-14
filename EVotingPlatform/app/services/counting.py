"""Vote counting. Pure functions - no database, no Flask - so they are easy to
test and easy to replace.

Tie-breaking: where two candidates are level, the lower candidate id is
eliminated first (and wins a final tie). This keeps results repeatable. The
real process uses a returning-officer draw - replace `_tie_key` if you need that.
"""
from fractions import Fraction


def _tie_key(candidate_id):
    return candidate_id


def _highest(candidates, tallies):
    """Candidate with the most votes; a tie goes to the lowest id."""
    top = max(tallies[c] for c in candidates)
    return min((c for c in candidates if tallies[c] == top), key=_tie_key)


def _lowest(candidates, tallies):
    """Candidate with the fewest votes; a tie goes to the lowest id."""
    bottom = min(tallies[c] for c in candidates)
    return min((c for c in candidates if tallies[c] == bottom), key=_tie_key)


# ---------------------------------------------------------------------------
# House of Representatives: full preferential (instant-runoff)
# ---------------------------------------------------------------------------
def count_house(ballots, candidate_ids):
    """ballots: list of candidate-id lists, most preferred first.
    candidate_ids: candidates still in the contest (excluded ones left out).
    Returns {'winner': id|None, 'formal_ballots': n, 'rounds': [...]}"""
    continuing = set(candidate_ids)
    rounds = []
    if not continuing:
        return {'winner': None, 'formal_ballots': len(ballots), 'rounds': rounds}

    while True:
        tallies = {c: 0 for c in continuing}
        exhausted = 0
        for prefs in ballots:
            for c in prefs:
                if c in continuing:
                    tallies[c] += 1
                    break
            else:
                exhausted += 1
        counted = sum(tallies.values())
        rounds.append({'round': len(rounds) + 1, 'tallies': dict(tallies), 'exhausted': exhausted})

        leader = _highest(continuing, tallies)
        if len(continuing) == 1 or tallies[leader] * 2 > counted:
            return {'winner': leader, 'formal_ballots': len(ballots), 'rounds': rounds}
        lowest = _lowest(continuing, tallies)
        rounds[-1]['eliminated'] = lowest
        continuing.remove(lowest)


# ---------------------------------------------------------------------------
# Senate: single transferable vote with fractional surplus transfers
# (weighted inclusive Gregory style)
# ---------------------------------------------------------------------------
def senate_quota(formal_ballots, seats):
    return formal_ballots // (seats + 1) + 1


def count_senate(ballots, candidate_ids, seats):
    """ballots: list of candidate-id lists, most preferred first (above-the-line
    votes already expanded to candidates).
    Returns {'elected': [ids in order], 'quota': n, 'formal_ballots': n, 'rounds': [...]}

    Simplification: candidates over quota are elected one at a time (largest
    first) rather than all at once; the surplus is passed on by multiplying the
    weight of every ballot in that candidate's pile by surplus / tally."""
    seats = min(seats, len(candidate_ids))
    quota = senate_quota(len(ballots), seats) if seats else 0
    weights = [Fraction(1)] * len(ballots)
    continuing = set(candidate_ids)
    elected, rounds = [], []

    while len(elected) < seats and continuing:
        tallies = {c: Fraction(0) for c in continuing}
        piles = {c: [] for c in continuing}
        for i, prefs in enumerate(ballots):
            for c in prefs:
                if c in continuing:
                    tallies[c] += weights[i]
                    piles[c].append(i)
                    break

        step = {'round': len(rounds) + 1, 'tallies': {c: float(t) for c, t in tallies.items()}}

        over = [c for c in continuing if tallies[c] >= quota]
        if over:
            winner = _highest(over, tallies)
            total = tallies[winner]
            factor = (total - quota) / total
            for i in piles[winner]:
                weights[i] *= factor
            elected.append(winner)
            continuing.remove(winner)
            step['elected'] = winner
            step['surplus'] = float(total - quota)
        elif len(continuing) <= seats - len(elected):
            # Only as many candidates left as seats: they are all elected.
            for c in sorted(continuing, key=lambda c: (-tallies[c], _tie_key(c))):
                elected.append(c)
            step['elected_remaining'] = [c for c in elected[-len(continuing):]]
            continuing = set()
        else:
            lowest = _lowest(continuing, tallies)
            continuing.remove(lowest)
            step['eliminated'] = lowest
        rounds.append(step)

    return {'elected': elected, 'quota': quota, 'formal_ballots': len(ballots), 'rounds': rounds}
