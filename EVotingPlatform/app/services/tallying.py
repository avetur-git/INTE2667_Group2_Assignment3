from app import db
from app.models.candidate import Candidate
from app.models.election import Division
from app.models.tally import TallyResult
from app.security.hooks import hooks
from app.services import ballot_store
from app.services.counting import count_house, count_senate
from app.services.errors import ServiceError
from app.utils import constants as C
from app.utils.helpers import utcnow


def _cand_info(c):
    return {'id': c.id, 'name': c.name, 'party': c.party, 'group_code': c.group_code}


def tally_election(election, user):
    """Count every House division and every Senate state, store the results,
    and mark the election as counted. Can be re-run (for example after a
    court-ordered candidate exclusion)."""
    if election.status not in (C.ELECTION_CLOSED, C.ELECTION_COUNTED):
        raise ServiceError('Close the election before counting', 409)

    # REQ-02: verify every ballot hash first. Base hook reports nothing.
    flagged = hooks.run('verify_ballots', ballot_store.raw_ballots(election.id))

    ballots = ballot_store.all_ballots(election.id)
    candidates = Candidate.query.filter_by(election_id=election.id).all()
    by_id = {c.id: c for c in candidates}

    TallyResult.query.filter_by(election_id=election.id).delete()

    summary = {'house': [], 'senate': [], 'flagged_ballots': flagged}

    for division in Division.query.order_by(Division.id).all():
        contesting = [c for c in candidates if c.contest_type == C.CONTEST_HOUSE
                      and c.division_id == division.id and not c.is_excluded]
        if not contesting and not any(b['division_id'] == division.id for b in ballots):
            continue
        div_ballots = [b['house_order'] for b in ballots
                       if b['division_id'] == division.id and b['ballot_uid'] not in flagged]
        out = count_house(div_ballots, [c.id for c in contesting])
        result = {
            'division': division.to_dict(),
            'winner': _cand_info(by_id[out['winner']]) if out['winner'] else None,
            'formal_ballots': out['formal_ballots'],
            'rounds': out['rounds'],
        }
        db.session.add(TallyResult(election_id=election.id, contest_type=C.CONTEST_HOUSE,
                                   division_id=division.id, result=result, computed_by=user.id))
        summary['house'].append(result)

    for state in C.STATES:
        contesting = [c for c in candidates if c.contest_type == C.CONTEST_SENATE
                      and c.state == state and not c.is_excluded]
        if not contesting:
            continue
        state_ballots = [b['senate_order'] for b in ballots
                         if b['state'] == state and b['ballot_uid'] not in flagged]
        seats = election.seats_for_state(state)
        out = count_senate(state_ballots, [c.id for c in contesting], seats)
        result = {
            'state': state,
            'seats': seats,
            'quota': out['quota'],
            'formal_ballots': out['formal_ballots'],
            'elected': [_cand_info(by_id[cid]) for cid in out['elected']],
            'rounds': out['rounds'],
        }
        db.session.add(TallyResult(election_id=election.id, contest_type=C.CONTEST_SENATE,
                                   state=state, result=result, computed_by=user.id))
        summary['senate'].append(result)

    election.status = C.ELECTION_COUNTED
    db.session.commit()
    return summary


def stored_results(election_id):
    rows = TallyResult.query.filter_by(election_id=election_id).all()
    return {
        'house': [r.result for r in rows if r.contest_type == C.CONTEST_HOUSE],
        'senate': [r.result for r in rows if r.contest_type == C.CONTEST_SENATE],
    }


def export_results(election):
    """Shareable results package for external systems."""
    data = stored_results(election.id)
    return {
        'election': election.to_dict(),
        'generated_at': utcnow().isoformat(),
        'house': [{'division': r['division'], 'winner': r['winner'],
                   'formal_ballots': r['formal_ballots']} for r in data['house']],
        'senate': [{'state': r['state'], 'seats': r['seats'], 'quota': r['quota'],
                    'formal_ballots': r['formal_ballots'], 'elected': r['elected']}
                   for r in data['senate']],
    }
