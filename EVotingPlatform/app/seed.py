"""Demo data so the platform can be tried straight away.
Fictional divisions, parties and people. DEVELOPMENT PASSWORDS - never reuse."""
from datetime import date
from app import db
from app.models import User, Voter, Election, Division, Candidate, Terminal
from app.utils import constants as C

DEMO_PASSWORD = 'ChangeMe123!'

DIVISIONS = [('Northfield', 'VIC'), ('Riverbend', 'VIC'), ('Hillcrest', 'VIC')]
PARTIES = ['Harbour Party', 'Unity Alliance', 'Green Valley', 'Workers First',
           'Heritage League', 'Coastal Voice', 'Future Forward', 'Rural Union']
FIRST = ['Alex', 'Sam', 'Jordan', 'Taylor', 'Casey', 'Morgan', 'Riley', 'Jamie', 'Drew', 'Quinn']
LAST = ['Nguyen', 'Smith', 'Patel', 'Brown', 'Wilson', 'Taylor', 'Martin', 'Lee', 'Walker', 'Young']


def seed_demo(election_status=C.ELECTION_OPEN, house_per_division=4,
              senate_groups=8, senate_per_group=3, seats=None):
    """Create staff, divisions, one election with candidates, terminals and voters."""
    if User.query.filter_by(username='admin').first():
        return {'message': 'Demo data already present'}

    for username, role in [('admin', C.ROLE_ADMIN), ('officer', C.ROLE_AEC_OFFICER),
                           ('delegate', C.ROLE_DELEGATE), ('delegate2', C.ROLE_DELEGATE)]:
        db.session.add(User(username, f'{username}@example.test', DEMO_PASSWORD, role))

    divisions = []
    for name, state in DIVISIONS:
        d = Division(name=name, state=state)
        db.session.add(d)
        divisions.append(d)
    db.session.flush()

    election = Election(name='Demo Federal Election', election_date=date(2028, 5, 20),
                        status=C.ELECTION_DRAFT, senate_seats_override=seats)
    db.session.add(election)
    db.session.flush()

    delegate = User.query.filter_by(username='delegate').first()
    n = 0
    for d in divisions:
        for pos in range(house_per_division):
            n += 1
            db.session.add(Candidate(election_id=election.id, contest_type=C.CONTEST_HOUSE,
                                     division_id=d.id, name=f'{FIRST[n % 10]} {LAST[(n * 3) % 10]}',
                                     party=PARTIES[pos % len(PARTIES)], ballot_position=pos + 1,
                                     created_by=delegate.id))
    for g in range(senate_groups):
        code = chr(ord('A') + g)
        for order in range(senate_per_group):
            n += 1
            db.session.add(Candidate(election_id=election.id, contest_type=C.CONTEST_SENATE,
                                     state='VIC', name=f'{FIRST[n % 10]} {LAST[(n * 7) % 10]}',
                                     party=PARTIES[g % len(PARTIES)], group_code=code,
                                     group_order=order + 1, created_by=delegate.id))
    for i in range(2):
        n += 1
        db.session.add(Candidate(election_id=election.id, contest_type=C.CONTEST_SENATE, state='VIC',
                                 name=f'{FIRST[n % 10]} {LAST[(n * 7) % 10]} (Independent)',
                                 party='Independent', group_code=C.UNGROUPED, group_order=i + 1,
                                 created_by=delegate.id))
    election.status = election_status

    for i in range(1, 11):
        db.session.add(Terminal(terminal_id=f'PS-{i:04d}', channel=C.CHANNEL_POLLING_STATION,
                                location_name=f'Demo Polling Place {i}', state='VIC',
                                software_version='base-0.1'))
    db.session.add(Terminal(terminal_id='DM-0001', channel=C.CHANNEL_DIPLOMATIC_MISSION,
                            location_name='Demo Australian Embassy', country='Demo Country',
                            software_version='base-0.1'))

    voters = [('voter1', 0, False), ('voter2', 0, False), ('voter3', 1, False),
              ('voter4', 1, False), ('voter5', 2, False), ('voter_os', 0, True)]
    for idx, (username, div_index, overseas) in enumerate(voters):
        user = User(username, f'{username}@example.test', DEMO_PASSWORD, C.ROLE_VOTER)
        db.session.add(user)
        db.session.flush()
        db.session.add(Voter(user_id=user.id, first_name=FIRST[idx], last_name=LAST[idx],
                             date_of_birth=date(1980 + idx, 3, 14), is_overseas=overseas,
                             address_line=f'{idx + 1} Example St', suburb='Exampleton', state='VIC',
                             postcode='3000', division_id=divisions[div_index].id,
                             enrolment_status=C.ENROLMENT_ENROLLED, registered_via='aec_officer'))
    db.session.commit()
    return {'message': 'Demo data created', 'election_id': election.id, 'password': DEMO_PASSWORD}


def seed_demo_voters(count, prefix='demo'):
    """REQ-08 / REQ-09 demo: extra enrolled voters (demo001, demo002, ...) for the
    failover demo and the load test. Skips usernames that already exist.

    All of them have the demo password, so it is hashed once at the normal cost
    and the hash reused: creating thousands of voters stays quick, while every
    login still pays the full bcrypt cost, as a real one would."""
    # The voters below live in VIC, so they go only in VIC divisions (seed-demo
    # also creates divisions in other states).
    divisions = Division.query.filter_by(state='VIC').order_by(Division.id).all()
    if not divisions:
        raise ValueError('run seed-demo first')
    existing = {u for (u,) in db.session.query(User.username).filter(User.username.like(f'{prefix}%'))}
    shared_hash = None
    created = []
    for i in range(1, count + 1):
        username = f'{prefix}{i:03d}'
        if username in existing:
            continue
        user = User(username, f'{username}@example.test', DEMO_PASSWORD, C.ROLE_VOTER,
                    password_hash=shared_hash)
        shared_hash = user.password_hash
        db.session.add(user)
        db.session.flush()
        db.session.add(Voter(user_id=user.id, first_name=FIRST[i % len(FIRST)],
                             last_name=LAST[(i // len(FIRST)) % len(LAST)],
                             date_of_birth=date(1970 + i % 30, 1 + i % 12, 1 + i % 28),
                             address_line=f'{i} Demo Rd', suburb='Exampleton', state='VIC',
                             postcode='3000', division_id=divisions[i % len(divisions)].id,
                             enrolment_status=C.ENROLMENT_ENROLLED, registered_via='aec_officer'))
        created.append(username)
    db.session.commit()
    return created
