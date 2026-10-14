"""Run a whole demo election in memory: create voters, cast random ballots,
close the election, count, and print the results.

    python scripts/simulate_election.py --voters 200

Useful for checking the counting code, and later as a starting point for
load testing (REQ-09). Uses an in-memory database, so nothing is saved.
"""
import argparse
import os
import random
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app, db                      # noqa: E402
from app.models import Voter, User, Division        # noqa: E402
from app.seed import seed_demo, DEMO_PASSWORD       # noqa: E402
from app.utils import constants as C                # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--voters', type=int, default=200)
    parser.add_argument('--seed', type=int, default=1)
    args = parser.parse_args()
    random.seed(args.seed)

    app = create_app({'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:', 'BCRYPT_ROUNDS': 4})
    with app.app_context():
        election_id = seed_demo(seats=6)['election_id']
        divisions = Division.query.all()
        for i in range(args.voters):
            user = User(f'sim{i}', f'sim{i}@example.test', DEMO_PASSWORD, C.ROLE_VOTER)
            db.session.add(user)
            db.session.flush()
            db.session.add(Voter(user_id=user.id, first_name='Sim', last_name=str(i),
                                 date_of_birth=date(1990, 1, 1), address_line='1 Sim St',
                                 suburb='Simtown', state='VIC', postcode='3000',
                                 division_id=random.choice(divisions).id,
                                 enrolment_status=C.ENROLMENT_ENROLLED))
        db.session.commit()

        client = app.test_client()

        def login(username):
            r = client.post('/api/auth/login', json={'username': username, 'password': DEMO_PASSWORD})
            return {'Authorization': f"Bearer {r.get_json()['access_token']}"}

        cast = 0
        for i in range(args.voters):
            h = login(f'sim{i}')
            paper = client.get(f'/api/voting/ballot-paper?election_id={election_id}', headers=h).get_json()
            house = [c['id'] for c in paper['house_candidates']]
            random.shuffle(house)
            groups = [g['group_code'] for g in paper['senate_groups']]
            random.shuffle(groups)
            r = client.post('/api/voting/cast', headers=h, json={
                'election_id': election_id, 'channel': 'online', 'house_preferences': house,
                'senate_atl': {g: n + 1 for n, g in enumerate(groups[:6])}})
            cast += r.status_code == 201
        print(f'Ballots cast: {cast} of {args.voters}')

        admin = login('admin')
        client.put(f'/api/elections/{election_id}/status', json={'status': 'closed'}, headers=admin)
        client.post(f'/api/elections/{election_id}/tally', headers=admin)
        results = client.get(f'/api/results/{election_id}').get_json()
        for h in results['house']:
            print(f"House - {h['division']['name']}: {h['winner']['name']} ({h['winner']['party']})")
        for s in results['senate']:
            print(f"Senate - {s['state']} ({s['seats']} seats):")
            for n, c in enumerate(s['elected'], 1):
                print(f'   {n}. {c["name"]} ({c["party"]})')


if __name__ == '__main__':
    main()
