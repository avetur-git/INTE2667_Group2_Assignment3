from app.security.hooks import hooks
from app.utils import constants as C


def _age_on(dob, on_date):
    return on_date.year - dob.year - ((on_date.month, on_date.day) < (dob.month, dob.day))


def check_eligibility(voter, election):
    """Is this voter allowed to vote in this election right now?
    Returns (eligible: bool, code: str|None, message: str|None).

    The roll lookup goes through the 'lookup_electoral_roll' hook (REQ-12) and
    the duplicate check through 'has_already_voted' (REQ-13)."""
    if voter is None:
        return False, 'not_enrolled', C.ERROR_NOT_ENROLLED

    roll = hooks.run('lookup_electoral_roll', voter)
    if not roll or not roll.get('enrolled'):
        return False, 'not_enrolled', C.ERROR_NOT_ENROLLED
    if not roll.get('is_citizen'):
        return False, 'not_citizen', C.ERROR_NOT_CITIZEN
    if _age_on(roll['date_of_birth'], election.election_date) < C.VOTING_AGE:
        return False, 'underage', C.ERROR_UNDERAGE
    if hooks.run('has_already_voted', voter, election):
        return False, 'already_voted', C.ERROR_ALREADY_VOTED
    return True, None, None
