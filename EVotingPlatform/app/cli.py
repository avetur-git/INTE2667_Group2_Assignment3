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
