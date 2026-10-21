import json
import subprocess

import pytest

from app.models import AuditLog
from infra.rollback import RollbackError, run_rollback


def _git(repo_path, *args):
    result = subprocess.run(["git", *args], cwd=str(repo_path), capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return result.stdout.strip()


def _make_repo(tmp_path):
    repo = tmp_path / 'rollback-demo'
    repo.mkdir()
    subprocess.run(['git', 'init'], cwd=str(repo), check=True, capture_output=True, text=True)
    subprocess.run(['git', 'config', 'user.name', 'Ops Bot'], cwd=str(repo), check=True, capture_output=True, text=True)
    subprocess.run(['git', 'config', 'user.email', 'ops@example.test'], cwd=str(repo), check=True, capture_output=True, text=True)

    infra_dir = repo / 'infra'
    infra_dir.mkdir()
    (infra_dir / 'security.conf').write_text('ALLOW_443=true\n', encoding='utf-8')
    subprocess.run(['git', 'add', '.'], cwd=str(repo), check=True, capture_output=True, text=True)
    subprocess.run(['git', 'commit', '-m', 'Initial infra config'], cwd=str(repo), check=True, capture_output=True, text=True)

    (infra_dir / 'security.conf').write_text('ALLOW_22=true\n# bad change\n', encoding='utf-8')
    subprocess.run(['git', 'add', '.'], cwd=str(repo), check=True, capture_output=True, text=True)
    subprocess.run(['git', 'commit', '-m', 'Bad firewall change'], cwd=str(repo), check=True, capture_output=True, text=True)
    return repo


def test_rollback_reverts_to_previous_commit(tmp_path):
    repo = _make_repo(tmp_path)
    result = run_rollback(
        repo,
        redeploy_command='python -c "print(\'redeploy ok\')"',
        health_check_command='python -c "print(\'healthy\')"',
        audit_log=False,
    )
    assert result.success is True
    assert (repo / 'infra' / 'security.conf').read_text(encoding='utf-8') == 'ALLOW_443=true\n'
    assert result.previous_commit
    assert result.reverted_commit


def test_rollback_keeps_git_history(tmp_path):
    repo = _make_repo(tmp_path)
    before = int(_git(repo, 'rev-list', '--count', 'HEAD'))
    run_rollback(
        repo,
        redeploy_command='python -c "print(\'redeploy ok\')"',
        health_check_command='python -c "print(\'healthy\')"',
        audit_log=False,
    )
    after = int(_git(repo, 'rev-list', '--count', 'HEAD'))
    assert after == before + 1
    assert 'Revert' in _git(repo, 'log', '--oneline', '-1')


def test_rollback_completes_within_15_minutes(tmp_path):
    repo = _make_repo(tmp_path)
    result = run_rollback(
        repo,
        redeploy_command='python -c "print(\'redeploy ok\')"',
        health_check_command='python -c "print(\'healthy\')"',
        audit_log=False,
    )
    assert result.duration_seconds <= 900


def test_health_check_passes_after_rollback(tmp_path):
    repo = _make_repo(tmp_path)
    result = run_rollback(
        repo,
        redeploy_command='python -c "print(\'redeploy ok\')"',
        health_check_command='python -c "print(\'healthy\')"',
        audit_log=False,
    )
    assert result.success is True
    assert result.message.lower().startswith('rollback succeeded')


def test_rollback_reports_failure_if_health_check_fails(tmp_path):
    repo = _make_repo(tmp_path)
    with pytest.raises(RollbackError, match='Health check failed'):
        run_rollback(
            repo,
            redeploy_command='python -c "print(\'redeploy ok\')"',
            health_check_command='python -c "import sys; sys.exit(1)"',
            audit_log=False,
        )


def test_rollback_refuses_without_previous_version(tmp_path):
    repo = tmp_path / 'single-commit'
    repo.mkdir()
    subprocess.run(['git', 'init'], cwd=str(repo), check=True, capture_output=True, text=True)
    subprocess.run(['git', 'config', 'user.name', 'Ops Bot'], cwd=str(repo), check=True, capture_output=True, text=True)
    subprocess.run(['git', 'config', 'user.email', 'ops@example.test'], cwd=str(repo), check=True, capture_output=True, text=True)
    (repo / 'infra').mkdir()
    (repo / 'infra' / 'security.conf').write_text('ALLOW_443=true\n', encoding='utf-8')
    subprocess.run(['git', 'add', '.'], cwd=str(repo), check=True, capture_output=True, text=True)
    subprocess.run(['git', 'commit', '-m', 'Only commit'], cwd=str(repo), check=True, capture_output=True, text=True)

    with pytest.raises(RollbackError, match='No previous version to return to'):
        run_rollback(repo, audit_log=False)


def test_rollback_duration_is_recorded(tmp_path):
    repo = _make_repo(tmp_path)
    result = run_rollback(
        repo,
        redeploy_command='python -c "print(\'redeploy ok\')"',
        health_check_command='python -c "print(\'healthy\')"',
        audit_log=False,
    )
    assert result.duration_seconds >= 0
    assert result.started_at
    assert result.ended_at


def test_rollback_event_written_to_audit_log(app, tmp_path):
    repo = _make_repo(tmp_path)
    with app.app_context():
        run_rollback(
            repo,
            redeploy_command='python -c "print(\'redeploy ok\')"',
            health_check_command='python -c "print(\'healthy\')"',
            audit_log=True,
        )

    assert AuditLog.query.filter_by(action='ROLLBACK').count() >= 1


def test_rollback_drill_runs_end_to_end(tmp_path):
    repo = _make_repo(tmp_path)
    result = run_rollback(
        repo,
        redeploy_command='python -c "print(\'redeploy ok\')"',
        health_check_command='python -c "print(\'healthy\')"',
        audit_log=False,
    )
    assert result.success is True
    assert result.duration_seconds <= 900
    assert 'ALLOW_443=true' in (repo / 'infra' / 'security.conf').read_text(encoding='utf-8')
