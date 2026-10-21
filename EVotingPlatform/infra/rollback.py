"""Git-based rollback helper for infra/security configuration changes.

The prototype intentionally keeps the Git history: a bad change is reverted via
`git revert`, which creates a new commit rather than discarding the previous one.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from typing import Callable, Optional
from urllib import error as urlerror
from urllib import request


class RollbackError(RuntimeError):
    """Raised when the rollback cannot complete safely."""


@dataclass
class RollbackResult:
    repo_path: str
    started_at: str
    ended_at: str
    duration_seconds: float
    previous_commit: str
    reverted_commit: str
    health_url: str
    redeploy_command: str
    success: bool
    message: str

    def to_dict(self):
        return asdict(self)


DEFAULT_REDEPLOY_COMMAND = "docker compose up -d --build"
DEFAULT_HEALTH_URL = "http://localhost:5000/health"
DEFAULT_MAX_DURATION_SECONDS = 900


def _run_git(repo_path: str, args: list[str]) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RollbackError(f"git {' '.join(args)} failed: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout.strip()


def _ensure_git_repo(repo_path: str) -> str:
    repo_path = os.path.abspath(repo_path)
    if not os.path.isdir(os.path.join(repo_path, ".git")):
        raise RollbackError(f"{repo_path} is not a Git repository")
    return repo_path


def _health_check(url: str, command: Optional[str] = None, checker: Optional[Callable[[], bool]] = None) -> bool:
    if checker is not None:
        return bool(checker())
    if command:
        result = subprocess.run(command, shell=True, capture_output=True, text=True, check=False)
        return result.returncode == 0
    if not url:
        raise RollbackError("No health check target was provided")
    try:
        with request.urlopen(url, timeout=10) as response:
            payload = response.read().decode("utf-8")
    except (urlerror.URLError, ValueError):
        return False
    if not payload:
        return False
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return True
    return data.get("status") in (None, "healthy", "ok") or response_ok(data)


def response_ok(data):
    return bool(data) and (
        data.get("status") == "healthy"
        or data.get("healthy") is True
        or data.get("ok") is True
    )


def _record_audit_event(action: str, *, user: Optional[str] = None, success: bool, details: Optional[dict] = None):
    details = details or {}
    try:
        from app.models.audit import AuditLog
        from app.services.audit import log_action
        from app.models.user import User
        from app import db

        user_obj = None
        if user:
            with db.session.no_autoflush:
                user_obj = User.query.filter_by(username=user).first()
        log_action(action, user=user_obj, object_type='infrastructure', object_id='rollback', details=details, success=success)
    except Exception:
        # Fallback: keep working even when no application database is connected.
        return None


def run_rollback(
    repo_path: str = ".",
    *,
    redeploy_command: Optional[str] = None,
    health_url: str = DEFAULT_HEALTH_URL,
    health_check_command: Optional[str] = None,
    health_check_fn: Optional[Callable[[], bool]] = None,
    max_duration_seconds: int = DEFAULT_MAX_DURATION_SECONDS,
    user: str = "rollback-system",
    audit_log: bool = True,
) -> RollbackResult:
    """Revert the latest bad commit, redeploy, and verify the service is healthy.

    The rollback is intentionally implemented using `git revert`, which creates a
    new commit and preserves the repository history. If there is no previous
    commit to revert, a clear error is raised.
    """
    repo_path = _ensure_git_repo(repo_path)
    if os.path.isdir(os.path.join(repo_path, ".git")):
        commit_count = int(_run_git(repo_path, ["rev-list", "--count", "HEAD"]).strip())
    else:
        commit_count = 0

    if commit_count < 2:
        raise RollbackError("No previous version to return to: this repository has fewer than two commits.")

    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    start_monotonic = time.monotonic()
    previous_commit = _run_git(repo_path, ["rev-parse", "HEAD~1"]).strip()
    redeploy_command = redeploy_command or DEFAULT_REDEPLOY_COMMAND

    try:
        _run_git(repo_path, ["revert", "--no-edit", "HEAD"])
        if redeploy_command:
            subprocess.run(redeploy_command, cwd=repo_path, shell=True, check=False)

        healthy = _health_check(health_url, command=health_check_command, checker=health_check_fn)
        if not healthy:
            raise RollbackError("Health check failed after rollback; service was not restored to healthy state.")

        reverted_commit = _run_git(repo_path, ["rev-parse", "HEAD"]).strip()
        duration_seconds = time.monotonic() - start_monotonic
        ended_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        if duration_seconds > max_duration_seconds:
            raise RollbackError(
                f"Rollback exceeded the 15 minute limit: {duration_seconds:.1f}s > {max_duration_seconds}s"
            )

        result = RollbackResult(
            repo_path=repo_path,
            started_at=started_at,
            ended_at=ended_at,
            duration_seconds=round(duration_seconds, 3),
            previous_commit=previous_commit,
            reverted_commit=reverted_commit,
            health_url=health_url,
            redeploy_command=redeploy_command,
            success=True,
            message="Rollback succeeded and the service is healthy.",
        )
        if audit_log:
            _record_audit_event(
                'ROLLBACK',
                user=user,
                success=True,
                details={
                    'repo_path': repo_path,
                    'previous_commit': previous_commit,
                    'reverted_commit': reverted_commit,
                    'duration_seconds': result.duration_seconds,
                    'health_url': health_url,
                    'redeploy_command': redeploy_command,
                },
            )
        return result
    except Exception as exc:
        duration_seconds = time.monotonic() - start_monotonic
        ended_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        result = RollbackResult(
            repo_path=repo_path,
            started_at=started_at,
            ended_at=ended_at,
            duration_seconds=round(duration_seconds, 3),
            previous_commit=previous_commit,
            reverted_commit="",
            health_url=health_url,
            redeploy_command=redeploy_command,
            success=False,
            message=str(exc),
        )
        if audit_log:
            _record_audit_event(
                'ROLLBACK',
                user=user,
                success=False,
                details={
                    'repo_path': repo_path,
                    'previous_commit': previous_commit,
                    'duration_seconds': result.duration_seconds,
                    'health_url': health_url,
                    'redeploy_command': redeploy_command,
                    'error': str(exc),
                },
            )
        raise RollbackError(str(exc)) from exc


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description='Rollback the last infra configuration change and redeploy.')
    parser.add_argument('--repo', default='.', help='Path to the Git repository to roll back.')
    parser.add_argument('--redeploy-command', default=DEFAULT_REDEPLOY_COMMAND, help='Shell command to redeploy the service.')
    parser.add_argument('--health-url', default=DEFAULT_HEALTH_URL, help='Health check URL used after redeploy.')
    parser.add_argument('--health-check-command', default=None, help='Optional shell command used to verify health instead of HTTP.')
    parser.add_argument('--max-duration-seconds', type=int, default=DEFAULT_MAX_DURATION_SECONDS, help='Maximum allowed completion time in seconds.')
    parser.add_argument('--user', default='rollback-system', help='Username recorded in the audit log.')
    args = parser.parse_args(argv)

    try:
        result = run_rollback(
            args.repo,
            redeploy_command=args.redeploy_command,
            health_url=args.health_url,
            health_check_command=args.health_check_command,
            max_duration_seconds=args.max_duration_seconds,
            user=args.user,
        )
        print(json.dumps(result.to_dict(), indent=2))
        return 0
    except RollbackError as exc:
        print(f"ROLLBACK FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
