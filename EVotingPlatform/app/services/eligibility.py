from app.security.electoral_roll import RollUnavailable
from app.security.hooks import hooks
from app.utils import constants as C

ERROR_ROLL_UNAVAILABLE = ('Your enrolment could not be confirmed with the electoral roll '
                          'right now. Please try again shortly or ask an AEC officer.')
ERROR_ROLL_MISMATCH = ('Your enrolment details do not match the electoral roll. '
                       'Please contact the AEC.')


def _age_on(dob, on_date):
    return on_date.year - dob.year - ((on_date.month, on_date.day) < (dob.month, dob.day))


def check_eligibility(voter, election):
    """Is this voter allowed to vote in this election right now?
    Returns (eligible: bool, code: str|None, message: str|None).

    REQ-12: the answer comes live from the authoritative electoral roll on
    every call (ballot paper, eligibility check and casting), never from a
    copy. If the roll cannot be reached or its answer cannot be trusted, the
    voter is refused (fail closed). The duplicate check goes through
    'has_already_voted' (REQ-13)."""
    if voter is None:
        return False, 'not_enrolled', C.ERROR_NOT_ENROLLED

    try:
        roll = hooks.run('lookup_electoral_roll', voter)
    except RollUnavailable as exc:
        from app.services.audit import log_action   # avoid a circular import
        log_action('ROLL_LOOKUP_FAILED', user=getattr(voter, 'user', None),
                   object_type='voter', object_id=voter.id, success=False,
                   details={'reason': str(exc)})
        return False, 'roll_unavailable', ERROR_ROLL_UNAVAILABLE

    if not roll or not roll.get('enrolled'):
        return False, 'not_enrolled', C.ERROR_NOT_ENROLLED
    # The ballot paper is built from the local record, so it must agree with
    # the roll: a tampered or out-of-date division would give the wrong paper.
    if (roll.get('division_id'), roll.get('state')) != (voter.division_id, voter.state):
        return False, 'roll_mismatch', ERROR_ROLL_MISMATCH
    if not roll.get('is_citizen'):
        return False, 'not_citizen', C.ERROR_NOT_CITIZEN
    if _age_on(roll['date_of_birth'], election.election_date) < C.VOTING_AGE:
        return False, 'underage', C.ERROR_UNDERAGE
    if hooks.run('has_already_voted', voter, election):
        return False, 'already_voted', C.ERROR_ALREADY_VOTED
    return True, None, None
