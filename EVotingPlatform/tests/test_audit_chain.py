import pytest
from sqlalchemy import text

from app import db
from app.models.audit import AuditLog
from app.security import audit_chain
from app.security.chain import GENESIS
from app.security.hooks import hooks


def add(action, **fields):
    entry = dict(user_id=1, username='officer', action=action, object_type='election', object_id='1',
                 details={'note': 'x'}, ip_address='203.0.113.5', user_agent='pytest',
                 session_id='s1', success=True)
    entry.update(fields)
    hooks.run('write_audit_record', entry)


def rows():
    return AuditLog.query.order_by(AuditLog.id).all()


def run_sql(sql, **params):
    """Change the table underneath the application, like someone with database access would."""
    db.session.execute(text(sql), params)
    db.session.commit()
    db.session.expire_all()


@pytest.fixture()
def chain(app):
    for name in ('a', 'b', 'c', 'd'):
        add(name)
    return rows()


def test_entries_are_numbered_and_linked(chain):
    assert [r.seq for r in chain] == [1, 2, 3, 4]
    assert chain[0].prev_hash == GENESIS
    for earlier, later in zip(chain, chain[1:]):
        assert later.prev_hash == earlier.entry_hash
    assert len({r.entry_hash for r in chain}) == 4


def test_untouched_chain_verifies(chain):
    assert audit_chain.find_problems() == []


def test_empty_log_verifies(app):
    assert audit_chain.find_problems() == []


def test_nested_details_verify(app):
    add('nested', details={'a': [1, 2, {'b': None}], 'c': {'d': 'e'}})
    add('no_details', details=None, success=False)
    assert audit_chain.find_problems() == []


def test_altered_entry_is_detected(chain):
    run_sql('DROP TRIGGER audit_logs_no_update')          # REQ-05 bypassed, e.g. by a database administrator
    run_sql("UPDATE audit_logs SET action = 'nothing_to_see' WHERE id = :i", i=chain[1].id)
    problems = audit_chain.find_problems()
    assert [p[0] for p in problems] == [chain[1].id]
    assert 'altered' in problems[0][1]


def test_changed_success_flag_is_detected(chain):
    add('failed_login', success=False)
    run_sql('DROP TRIGGER audit_logs_no_update')
    run_sql('UPDATE audit_logs SET success = 1 WHERE success = 0')
    assert any('altered' in reason for _id, reason in audit_chain.find_problems())


def test_deleted_entry_is_detected(chain):
    run_sql('DROP TRIGGER audit_logs_no_delete')
    run_sql('DELETE FROM audit_logs WHERE id = :i', i=chain[1].id)
    problems = audit_chain.find_problems()
    assert len(problems) == 1 and 'chain broken' in problems[0][1]
    assert problems[0][0] == chain[2].id


def test_inserted_entry_without_a_hash_is_detected(chain):
    db.session.add(AuditLog(action='forged', username='nobody'))
    db.session.commit()
    problems = audit_chain.find_problems()
    assert len(problems) == 1 and 'no hash' in problems[0][1]


def test_recomputed_hash_without_the_key_is_detected(chain, app):
    import hashlib
    run_sql('DROP TRIGGER audit_logs_no_update')
    victim = chain[1]
    run_sql("UPDATE audit_logs SET action = 'x', entry_hash = :h WHERE id = :i",
            h=hashlib.sha256(b'guess').hexdigest(), i=victim.id)
    assert audit_chain.find_problems()


def test_wrong_key_fails_every_entry(chain, app):
    app.config['AUDIT_CHAIN_KEY'] = 'a different key'
    assert len(audit_chain.find_problems()) == len(chain)


def test_append_only_triggers_still_block_edits(chain):
    with pytest.raises(Exception):
        run_sql("UPDATE audit_logs SET action = 'x'")
    db.session.rollback()
    assert audit_chain.find_problems() == []


def test_registering_twice_does_not_stack(app):
    audit_chain.init_app(app)
    audit_chain.init_app(app)
    add('once')
    assert AuditLog.query.count() == 1


def test_cli_reports_ok_and_broken(chain, app):
    runner = app.test_cli_runner()
    ok = runner.invoke(args=['verify-audit-chain'])
    assert ok.exit_code == 0 and 'Audit chain OK: 4 entries' in ok.output
    run_sql('DROP TRIGGER audit_logs_no_update')
    run_sql("UPDATE audit_logs SET action = 'tampered' WHERE id = :i", i=chain[2].id)
    bad = runner.invoke(args=['verify-audit-chain'])
    assert bad.exit_code == 1 and 'BROKEN' in bad.output and f'id {chain[2].id}' in bad.output