import click
from app.seed import seed_demo


def register_cli(app):
    @app.cli.command('seed-demo')
    def seed_demo_command():
        """Create demo staff, voters, candidates, terminals and an open election."""
        result = seed_demo()
        click.echo(result['message'])
        if 'password' in result:
            click.echo(f"Logins: admin / officer / delegate / voter1..voter5 / voter_os  (password: {result['password']})")

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
