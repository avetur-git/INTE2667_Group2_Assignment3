from sqlalchemy import func

from app import db
from app.models.candidateHistory import CandidateHistory
from app.utils.helpers import get_request_meta

ACTIONS = ('created', 'updated', 'excluded')
TRACKED_FIELDS = ('name', 'party', 'group_code', 'group_order', 'ballot_position')


def record_change(candidate, action, changes, user):
    """Add a history entry for one change to a candidate.

    changes: {"field": {"old": ..., "new": ...}, ...}
    Does NOT commit. The caller commits, so the change and its history
    entry are saved together (or neither is saved).
    """
    if action not in ACTIONS:
        raise ValueError(f'Unknown action: {action}')
    if candidate.id is None:
        # A new candidate has no id until it is flushed.
        raise ValueError('Call db.session.flush() before recording history for a new candidate')

    latest = (db.session.query(func.max(CandidateHistory.version))
              .filter(CandidateHistory.candidate_id == candidate.id)
              .scalar())

    meta = get_request_meta()
    entry = CandidateHistory(
        candidate_id=candidate.id,
        version=(latest or 0) + 1,
        action=action,
        changes=changes,
        changed_by=user.id,
        session_id=meta.get('session_id'),
        user_agent=meta.get('user_agent'),
    )
    db.session.add(entry)
    return entry


def diff_fields(candidate, new_values, fields=TRACKED_FIELDS):
    """Compare the candidate's current values with new_values.
    Call this BEFORE changing the candidate. Returns only the fields that
    actually change, as {"field": {"old": ..., "new": ...}}."""
    changes = {}
    for field in fields:
        if field in new_values and getattr(candidate, field) != new_values[field]:
            changes[field] = {'old': getattr(candidate, field), 'new': new_values[field]}
    return changes