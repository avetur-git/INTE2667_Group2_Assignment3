from app.services.counting import count_house, count_senate, senate_quota


def test_house_majority_in_first_round():
    ballots = [[1, 2, 3]] * 5 + [[2, 1, 3]] * 2 + [[3, 1, 2]] * 1
    out = count_house(ballots, [1, 2, 3])
    assert out['winner'] == 1
    assert len(out['rounds']) == 1


def test_house_preferences_flow_after_elimination():
    # First round: A=3, B=3, C=2 -> C eliminated, both C ballots go to B -> B=5 of 8
    ballots = [['A', 'B', 'C']] * 3 + [['B', 'A', 'C']] * 3 + [['C', 'B', 'A']] * 2
    out = count_house(ballots, ['A', 'B', 'C'])
    assert out['winner'] == 'B'
    assert out['rounds'][0]['eliminated'] == 'C'
    assert out['rounds'][1]['tallies']['B'] == 5


def test_house_excluded_candidate_is_skipped():
    ballots = [[1, 2, 3]] * 4 + [[2, 3, 1]] * 3 + [[3, 2, 1]] * 2
    out = count_house(ballots, [2, 3])   # candidate 1 excluded
    assert out['winner'] == 2            # 1's ballots go to 2: 2=7 of 9


def test_senate_quota_is_droop():
    assert senate_quota(100, 3) == 26
    assert senate_quota(1000, 12) == 77


def test_senate_elects_requested_number_and_no_duplicates():
    cands = list(range(1, 9))
    ballots = []
    for i in range(200):
        order = cands[i % 8:] + cands[:i % 8]
        ballots.append(order)
    out = count_senate(ballots, cands, 3)
    assert len(out['elected']) == 3
    assert len(set(out['elected'])) == 3


def test_senate_surplus_transfers_to_next_preference():
    # 3 seats, 100 ballots -> quota 26. Candidate 1 has 60 first prefs (surplus 34 goes to 2).
    ballots = [[1, 2, 3, 4]] * 60 + [[3, 4, 2, 1]] * 20 + [[4, 3, 2, 1]] * 20
    out = count_senate(ballots, [1, 2, 3, 4], 3)
    assert out['quota'] == 26
    assert out['elected'][0] == 1
    assert set(out['elected']) == {1, 2, 3}


def test_senate_no_ballots_still_fills_seats():
    out = count_senate([], [1, 2, 3, 4], 2)
    assert len(out['elected']) == 2
