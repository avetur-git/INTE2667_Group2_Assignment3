import click
from app.seed import seed_demo, seed_demo_voters


def register_cli(app):
    @app.cli.command('seed-demo')
    def seed_demo_command():
        """Create demo staff, voters, candidates, terminals and an open election."""
        result = seed_demo()
        click.echo(result['message'])
        if 'password' in result:
            click.echo(f"Logins: admin / officer / delegate / voter1..voter5 / voter_os  (password: {result['password']})")

    @app.cli.command('seed-demo-voters')
    @click.option('--count', default=100, show_default=True, help='Number of voters to add.')
    def seed_demo_voters_command(count):
        """REQ-08 demo: add enrolled voters demo001..demoNNN (run seed-demo first)."""
        created = seed_demo_voters(count)
        click.echo(f'Added {len(created)} voters (demo001..demo{count:03d}, same password as seed-demo)')

    @app.cli.command('issue-enrolment-code')
    @click.argument('username')
    def issue_enrolment_code_command(username):
        """REQ-10: one-time code a staff member uses to register a security key."""
        from app.models.user import User
        from app.security.webauthn_mfa import ENROLMENT_CODE_TTL, issue_enrolment_code
        from app.services.audit import log_action
        user = User.query.filter_by(username=username).first()
        if not user:
            raise click.ClickException(f'No user named {username!r}')
        if not user.is_privileged:
            raise click.ClickException('Security keys are only for staff accounts')
        code = issue_enrolment_code(user)
        log_action('ENROLMENT_CODE_ISSUED', object_type='user', object_id=user.id,
                   details={'username': user.username, 'via': 'cli'})
        minutes = int(ENROLMENT_CODE_TTL.total_seconds() // 60)
        click.echo(f'Enrolment code for {user.username}: {code}')
        click.echo(f'Single use, expires in {minutes} minutes. Register the key at /mfa/enrol')

    @app.cli.command('archive-audit-logs')
    def archive_audit_logs_command():
        """REQ-05: copy any audit entries not yet archived to the write-once store now."""
        from app.security.audit_protection import archive_enabled, archive_pending
        if not archive_enabled():
            raise click.ClickException('Audit archive is not configured (AUDIT_ARCHIVE_ENDPOINT)')
        batch = archive_pending(force=True)
        if batch:
            click.echo(f'Archived entries {batch.first_id}-{batch.last_id} to {batch.object_key}, '
                       f'locked until {batch.retain_until:%Y-%m-%d %H:%M} UTC')
        else:
            click.echo('Nothing to archive')

    @app.cli.command('verify-audit-archive')
    def verify_audit_archive_command():
        """REQ-05: check the audit table still matches what was locked in the archive."""
        from app.models.audit import AuditArchiveBatch
        from app.security.audit_protection import archive_enabled, verify_archive
        if not archive_enabled():
            raise click.ClickException('Audit archive is not configured (AUDIT_ARCHIVE_ENDPOINT)')
        problems = verify_archive()
        for problem in problems:
            click.echo(f'PROBLEM {problem}')
        if problems:
            raise click.ClickException(f'{len(problems)} archived batch(es) do not match the database')
        click.echo(f'OK: {AuditArchiveBatch.query.count()} archived batch(es) match the database')
