from app import db
from app.models.candidate import Candidate
from app.models.terminal import Terminal
from app.models.voter import VoteRecord
from app.security.hooks import hooks
from app.services import ballot_store
from app.services.audit import log_action
from app.services.ballot_validation import validate_house, validate_senate
from app.services.eligibility import check_eligibility
from app.services.errors import ServiceError
from app.utils import constants as C


def _house_candidates(election_id, division_id):
    return (Candidate.query.filter_by(election_id=election_id, contest_type=C.CONTEST_HOUSE,
                                      division_id=division_id, is_excluded=False)
            .order_by(Candidate.ballot_position, Candidate.id).all())


def _senate_candidates(election_id, state):
    return (Candidate.query.filter_by(election_id=election_id, contest_type=C.CONTEST_SENATE,
                                      state=state, is_excluded=False)
            .order_by(Candidate.group_code, Candidate.group_order, Candidate.id).all())


def build_ballot_paper(voter, election):
    """The candidates this voter sees: their division for the House and their
    state for the Senate."""
    house = _house_candidates(election.id, voter.division_id)
    senate = _senate_candidates(election.id, voter.state)
    groups = {}
    ungrouped = []
    for c in senate:
        if c.group_code and c.group_code != C.UNGROUPED:
            groups.setdefault(c.group_code, []).append(c.to_dict())
        else:
            ungrouped.append(c.to_dict())
    return {
        'election': election.to_dict(),
        'division': voter.division.to_dict() if voter.division else None,
        'state': voter.state,
        'house_candidates': [c.to_dict() for c in house],
        'senate_groups': [{'group_code': code, 'candidates': cands,
                           'party': cands[0]['party']} for code, cands in sorted(groups.items())],
        'senate_ungrouped': ungrouped,
        'rules': {'senate_atl_min': C.SENATE_ATL_MIN, 'senate_btl_min': C.SENATE_BTL_MIN},
    }


def cast_vote(voter, election, payload, channel, terminal_id, user, request_meta):
    """Validate and record one ballot. Returns the receipt dict."""
    if election.status != C.ELECTION_OPEN:
        raise ServiceError(C.ERROR_ELECTION_NOT_OPEN, 409)
    if channel not in C.ALL_CHANNELS:
        raise ServiceError('Unknown voting channel', 400)

    # Channel rules from the systems description
    if voter.is_overseas and channel != C.CHANNEL_DIPLOMATIC_MISSION:
        raise ServiceError('Overseas voters can vote at diplomatic missions only', 403)
    if channel == C.CHANNEL_DIPLOMATIC_MISSION and not voter.is_overseas:
        raise ServiceError('Diplomatic mission voting is for overseas voters only', 403)

    # Terminal checks (REQ-03 / REQ-19 plug in through the hook)
    terminal = None
    if channel in C.TERMINAL_CHANNELS:
        terminal = Terminal.query.filter_by(terminal_id=terminal_id, is_active=True).first() if terminal_id else None
        if not terminal or terminal.channel != channel:
            raise ServiceError(C.ERROR_TERMINAL_INVALID, 403)
        ok, message = hooks.run('verify_terminal', terminal, request_meta)
        if not ok:
            raise ServiceError(message or C.ERROR_TERMINAL_INVALID, 403)

    # Eligibility (REQ-12 / REQ-13 plug in through hooks)
    eligible, code, message = check_eligibility(voter, election)
    if not eligible:
        raise ServiceError(message, 409 if code == 'already_voted' else 403, code)

    ok, message = hooks.run('pre_cast', voter, election, channel, request_meta)
    if not ok:
        raise ServiceError(message or 'Ballot refused', 403)

    # Formal-vote checks
    house_candidates = _house_candidates(election.id, voter.division_id)
    senate_candidates = _senate_candidates(election.id, voter.state)
    if not house_candidates or not senate_candidates:
        raise ServiceError('Candidates have not been set up for this voter\'s contests', 409)
    house_order = validate_house(payload.get('house_preferences'), house_candidates)
    senate_order, senate_source = validate_senate(payload.get('senate_atl'), payload.get('senate_btl'),
                                                  senate_candidates)

    # Store: one record that the voter voted, one anonymous ballot, one commit.
    try:
        db.session.add(VoteRecord(voter_id=voter.id, election_id=election.id, channel=channel,
                                  terminal_id=terminal.terminal_id if terminal else None))
        ballot = ballot_store.save_ballot(
            election=election, division_id=voter.division_id, state=voter.state,
            house_order=house_order,
            senate_atl=payload.get('senate_atl') or None,
            senate_btl=payload.get('senate_btl') or None,
            senate_order=senate_order, senate_source=senate_source)
        hooks.run('on_ballot_stored', ballot)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    receipt = hooks.run('issue_receipt', ballot)
    log_action('CAST_BALLOT', user=user, object_type='election', object_id=election.id,
               details={'channel': channel, 'terminal_id': terminal.terminal_id if terminal else None})
    return receipt
