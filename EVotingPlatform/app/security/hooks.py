"""
Security hook registry
======================

The base build contains NO security solutions. Instead, every place where one
of the 20 security requirements (REQ-01 .. REQ-20) will plug in calls a named
hook. Each hook has a plain default here that does the minimum needed for the
platform to work. To add a security solution later, write a function with the
same signature and register it:

    from app.security.hooks import hooks
    hooks.register('verify_terminal', my_signature_check)

Nothing else in the application has to change. docs/SECURITY_HOOKS.md maps
every requirement to its hook and the file that calls it.
"""
from app import db
from app.utils.helpers import generate_receipt_code, utcnow


class HookRegistry:
    def __init__(self):
        self._hooks = {}
        self._defaults = {}

    def set_default(self, name, fn):
        self._defaults[name] = fn
        self._hooks.setdefault(name, fn)

    def register(self, name, fn):
        if name not in self._defaults:
            raise KeyError(f'Unknown hook: {name}')
        self._hooks[name] = fn

    def reset(self, name=None):
        """Put one hook (or all hooks) back to the base behaviour."""
        names = [name] if name else list(self._defaults)
        for n in names:
            self._hooks[n] = self._defaults[n]

    def run(self, name, *args, **kwargs):
        return self._hooks[name](*args, **kwargs)

    def names(self):
        return sorted(self._defaults)


hooks = HookRegistry()


# ---------------------------------------------------------------------------
# Default (base) behaviour for each hook
# ---------------------------------------------------------------------------

def default_verify_mfa(user, request_data):
    """REQ-10 - second login factor for privileged users.
    Base: no second factor. Return True to allow login, False to refuse."""
    return True


def default_authorise_action(action, actor, target, request_data):
    """REQ-18 - dual authorisation for sensitive actions such as candidate
    exclusion. Base: a single user may perform the action.
    Return (allowed: bool, message: str)."""
    return True, ''


def default_lookup_electoral_roll(voter):
    """REQ-12 - real-time check against the authoritative electoral roll.
    Base: reads the local voters table. Return a dict describing the voter."""
    return {
        'enrolled': voter.enrolment_status == 'enrolled',
        'is_citizen': voter.is_citizen,
        'date_of_birth': voter.date_of_birth,
        'division_id': voter.division_id,
        'state': voter.state,
    }


def default_verify_terminal(terminal, request_meta):
    """REQ-03 (terminal software signature) and REQ-19 (diplomatic mission
    allow-list and location). Base: any active registered terminal is accepted.
    Return (allowed: bool, message: str)."""
    return True, ''


def default_has_already_voted(voter, election):
    """REQ-13 - one ballot per voter across all channels.
    Base: a plain read of vote_records (not atomic, per-database only)."""
    from app.models.voter import VoteRecord
    return VoteRecord.query.filter_by(voter_id=voter.id, election_id=election.id).first() is not None


def default_pre_cast(voter, election, channel, request_meta):
    """Extra checks just before a ballot is accepted (any requirement).
    Return (allowed: bool, message: str)."""
    return True, ''


def default_protect_data(value):
    """REQ-14 - encrypt sensitive values before they are stored. Base: no-op."""
    return value


def default_unprotect_data(value):
    """REQ-14 - reverse of protect_data. Base: no-op."""
    return value


def default_on_ballot_stored(ballot):
    """REQ-02 - create the hash for a newly stored ballot (set ballot.ballot_hash).
    Called inside the same database transaction, before commit. Base: no-op."""
    return None


def default_verify_ballots(ballots):
    """REQ-02 - verify every ballot hash before tallying.
    Return a list of ballot_uids that FAILED verification (empty = all good)."""
    return []


def default_issue_receipt(ballot):
    """REQ-20 - non-identifying receipt for the voter.
    Base: the random receipt code created when the ballot was saved."""
    return {
        'receipt_code': ballot.receipt_code,
        'election_id': ballot.election_id,
        'recorded_on': ballot.cast_date.isoformat() if ballot.cast_date else None,
    }


def default_write_audit_record(entry):
    """REQ-04 / REQ-05 / REQ-15 - store an audit record.
    Base: a normal row in the audit_logs table. Later: hash-chained entries
    forwarded to the SIEM and archived to write-once storage."""
    from app.models.audit import AuditLog
    db.session.add(AuditLog(**entry))
    db.session.commit()


def register_default_hooks():
    defaults = {
        'verify_mfa': default_verify_mfa,
        'authorise_action': default_authorise_action,
        'lookup_electoral_roll': default_lookup_electoral_roll,
        'verify_terminal': default_verify_terminal,
        'has_already_voted': default_has_already_voted,
        'pre_cast': default_pre_cast,
        'protect_data': default_protect_data,
        'unprotect_data': default_unprotect_data,
        'on_ballot_stored': default_on_ballot_stored,
        'verify_ballots': default_verify_ballots,
        'issue_receipt': default_issue_receipt,
        'write_audit_record': default_write_audit_record,
    }
    for name, fn in defaults.items():
        hooks.set_default(name, fn)


register_default_hooks()
