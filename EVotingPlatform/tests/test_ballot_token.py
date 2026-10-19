from types import SimpleNamespace

import pytest

from app import db
from app.models import Ballot, SpentToken
from app.security.tokens import InvalidToken, issue_token, verify_token
from app.services import ballot_store
from app.services.errors import ServiceError


def _save(token):
    return ballot_store.save_ballot(
        token=token, election=SimpleNamespace(id=1), division_id=1, state='NSW',
        house_order=[1, 2], senate_atl=None, senate_btl=None,
        senate_order=[3, 4], senate_source='atl')


def test_valid_token_is_accepted(app):
    ballot = _save(issue_token())
    db.session.commit()
    assert ballot.id is not None
    assert SpentToken.query.count() == 1


def test_forged_token_is_rejected(app):
    with pytest.raises(ServiceError):
        _save('0' * 32 + '.' + '0' * 128)


def test_malformed_token_is_rejected(app):
    with pytest.raises(InvalidToken):
        verify_token('not-a-token')


def test_token_cannot_be_used_twice(app):
    token = issue_token()
    _save(token)
    db.session.commit()
    with pytest.raises(ServiceError):
        _save(token)
    assert Ballot.query.count() == 1
