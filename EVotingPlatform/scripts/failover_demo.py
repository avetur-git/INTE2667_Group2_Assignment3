"""REQ-08 demo: keep voting while one API replica is killed.

    docker compose up --build -d
    docker compose exec web flask --app run seed-demo
    docker compose exec web flask --app run seed-demo-voters --count 60
    python scripts/failover_demo.py

While it runs, in a second terminal:

    docker compose kill web        # kill one replica mid-vote
    docker compose start web       # bring it back later

Each line shows which replica (X-Served-By, set by haproxy) recorded the vote.
After the kill every vote goes to web2 and none fail. Watch
http://localhost:8404 to see haproxy mark the replica down and up again.

Votes are paced (default one every 4 s) to stay under the REQ-07 login limit of
20 per minute per IP: every demo voter logs in from this one machine.
"""
import argparse
import json
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime

PASSWORD = 'ChangeMe123!'   # demo password from app/seed.py


def call(base, method, path, body=None, token=None):
    """(status, json body, served-by replica, seconds taken)."""
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=15) as r:   # nosec B310 - local demo URL
            status, raw, served_by = r.status, r.read(), r.headers.get('X-Served-By')
    except urllib.error.HTTPError as e:
        status, raw, served_by = e.code, e.read(), e.headers.get('X-Served-By')
    except OSError as e:
        return f'ERR {e.__class__.__name__}', {}, None, time.monotonic() - started
    try:
        payload = json.loads(raw or b'{}')
    except ValueError:
        payload = {}
    return status, payload, served_by, time.monotonic() - started


def vote(base, username, election_id):
    """Log in, fetch the ballot paper, cast. Returns the cast call's result."""
    status, body, served_by, took = call(base, 'POST', '/api/auth/login',
                                         {'username': username, 'password': PASSWORD})
    if status != 200:
        return f'login {status}', served_by, took
    token = body['access_token']
    status, paper, served_by, took = call(
        base, 'GET', f'/api/voting/ballot-paper?election_id={election_id}', token=token)
    if status != 200:
        return f'paper {status}', served_by, took
    ballot = {'election_id': election_id, 'channel': 'online',
              'house_preferences': [c['id'] for c in paper['house_candidates']],
              'senate_atl': {g['group_code']: i + 1
                             for i, g in enumerate(paper['senate_groups'][:6])}}
    status, _, served_by, took = call(base, 'POST', '/api/voting/cast', ballot, token=token)
    return status, served_by, took


def main():
    parser = argparse.ArgumentParser(description='Keep casting votes during a replica failure.')
    parser.add_argument('--url', default='http://localhost:9001')
    parser.add_argument('--election', type=int, default=1)
    parser.add_argument('--first', type=int, default=1, help='first demo voter number')
    parser.add_argument('--voters', type=int, default=30, help='how many votes to cast')
    parser.add_argument('--interval', type=float, default=4.0, help='seconds between votes')
    args = parser.parse_args()
    base = args.url.rstrip('/')

    print(f'Casting {args.voters} votes at {base}, one every {args.interval:g}s.')
    print('Kill a replica now in another terminal:  docker compose kill web\n')
    print(f"{'time':8}  {'voter':8}  {'result':10}  {'replica':7}  took")

    results, replicas = Counter(), Counter()
    for n in range(args.first, args.first + args.voters):
        username = f'demo{n:03d}'
        status, served_by, took = vote(base, username, args.election)
        ok = status == 201
        results['recorded' if ok else f'failed ({status})'] += 1
        if ok:
            replicas[served_by or 'unknown'] += 1
        print(f"{datetime.now():%H:%M:%S}  {username:8}  {str(status):10}  "
              f"{served_by or '-':7}  {took:.2f}s")
        time.sleep(args.interval)

    print('\nSummary')
    for outcome, count in sorted(results.items()):
        print(f'  {outcome}: {count}')
    for replica, count in sorted(replicas.items()):
        print(f'  recorded by {replica}: {count}')


if __name__ == '__main__':
    main()
