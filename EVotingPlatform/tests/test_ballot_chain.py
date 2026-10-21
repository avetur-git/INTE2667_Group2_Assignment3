from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.security.chain import GENESIS, checkpoint_message, entry_hash, find_problems

KEY = Ed25519PrivateKey.generate()
ELECTION = 1


def build(n=5, checkpoint_every=2):
    ballots, checkpoints, prev = [], [], GENESIS
    for seq in range(1, n + 1):
        payload = {'choice': f'cand{seq}'}
        h = entry_hash(prev, payload)
        ballots.append([seq, f'uid{seq}', payload, prev, h])
        if seq % checkpoint_every == 0:
            sig = KEY.sign(checkpoint_message(ELECTION, seq, h)).hex()
            checkpoints.append((seq, h, sig))
        prev = h
    return ballots, checkpoints


def check(ballots, checkpoints):
    problems = find_problems([tuple(b) for b in ballots], checkpoints, KEY.public_key(), ELECTION)
    return {uid for uid, _reason in problems}


def test_clean_chain_has_no_problems():
    ballots, checkpoints = build()
    assert check(ballots, checkpoints) == set()


def test_edited_ballot_is_flagged():
    ballots, checkpoints = build()
    ballots[2][2] = {'choice': 'X'}
    assert 'uid3' in check(ballots, checkpoints)


def test_edit_with_recomputed_hash_breaks_the_next_link():
    ballots, checkpoints = build()
    ballots[2][2] = {'choice': 'X'}
    ballots[2][4] = entry_hash(ballots[2][3], ballots[2][2])
    assert 'uid4' in check(ballots, checkpoints)


def test_deleted_ballot_is_flagged():
    ballots, checkpoints = build()
    del ballots[2]
    assert 'uid4' in check(ballots, checkpoints)


def test_deleted_last_ballots_are_caught_by_checkpoint():
    ballots, checkpoints = build()
    assert 'MISSING:4' in check(ballots[:3], checkpoints)


def test_forged_checkpoint_is_flagged():
    ballots, checkpoints = build()
    checkpoints[0] = (2, checkpoints[0][1], '00' * 64)
    assert 'CHECKPOINT:2' in check(ballots, checkpoints)


def test_rewritten_chain_is_caught_by_signed_checkpoint():
    ballots, checkpoints = build()
    ballots[1][2] = {'choice': 'X'}
    prev = ballots[0][4]
    for b in ballots[1:]:
        b[3] = prev
        b[4] = entry_hash(prev, b[2])
        prev = b[4]
    assert 'uid2' in check(ballots, checkpoints)
