"""REQ-09: run the vote-flow load test at several replica counts and compare.

    docker compose up --build -d
    docker compose exec web flask --app run seed-demo
    python scripts/run_load_test.py                       # 1, 2 and 3 replicas
    python scripts/run_load_test.py --replicas 1,3 --votes 600 --terminals 60

For each replica count it scales the API (`web` plus N-1 `web2` containers, each
limited to 1 CPU), waits until haproxy reports exactly N healthy replicas, then
runs loadtest/vote_flow.js with k6 inside the Compose network against the load
balancer. Every vote uses a fresh voter (load001, load002, ...), created here.
Results go to loadtest/results/: one JSON file per run and results.md, a table
for the report. Afterwards the stack is put back to the default two replicas.

k6 talks to haproxy directly, behind the nginx edge: nginx limits each client
address, and here all simulated terminals come from one machine. In production
the edge (CloudFront/WAF, REQ-07) scales on its own; this test measures the API
tier, which is what the replicas add capacity to.
"""
import argparse
import csv
import io
import json
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # EVotingPlatform/
LOADTEST = ROOT / 'loadtest'
RESULTS = LOADTEST / 'results'
K6_IMAGE = 'grafana/k6:1.2.3'
STATS_URL = 'http://127.0.0.1:8404/;csv'


def compose(*args, capture=True):
    result = subprocess.run(['docker', 'compose', *args], cwd=ROOT, text=True,
                            capture_output=capture)
    if result.returncode != 0:
        sys.exit(f"docker compose {' '.join(args)} failed:\n{result.stderr}")
    return result.stdout.strip() if capture else ''


def in_app(code):
    """Run Python inside the `web` container with the app context; return its output."""
    script = ('from app import create_app\napp = create_app()\n'
              'with app.app_context():\n' + ''.join(f'    {line}\n' for line in code.splitlines()))
    return compose('exec', '-T', 'web', 'python', '-c', script).splitlines()[-1]


def healthy_replicas():
    try:
        data = urllib.request.urlopen(STATS_URL, timeout=3).read().decode()   # nosec B310 - local
    except OSError:
        return -1
    rows = csv.reader(io.StringIO(data.lstrip('# ')))
    return sum(1 for r in rows if len(r) > 17 and r[0] == 'replicas'
               and r[1] != 'BACKEND' and r[17] == 'UP')


def scale_to(replicas):
    print(f'\nScaling to {replicas} replica(s)...', flush=True)
    compose('up', '-d', '--no-recreate', '--scale', f'web2={replicas - 1}', 'web', 'web2')
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        if healthy_replicas() == replicas:
            time.sleep(3)          # let the health state settle
            if healthy_replicas() == replicas:
                print(f'haproxy reports {replicas} healthy replica(s)', flush=True)
                return
        time.sleep(1)
    sys.exit(f'haproxy did not report {replicas} healthy replicas within 3 minutes')


def compose_network():
    container = compose('ps', '-q', 'lb')
    if not container:
        sys.exit('The stack is not running: docker compose up --build -d')
    out = subprocess.run(['docker', 'inspect', '-f',
                          '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}} {{end}}',
                          container], text=True, capture_output=True).stdout
    return out.split()[0]


def run_k6(network, label, first, votes, terminals, election_id):
    cmd = ['docker', 'run', '--rm', '--network', network,
           '-v', f'{LOADTEST}:/scripts:ro', '-v', f'{RESULTS}:/results',
           '-e', f'LABEL={label}', '-e', f'FIRST_VOTER={first}', '-e', f'VOTES={votes}',
           '-e', f'TERMINALS={terminals}', '-e', f'ELECTION_ID={election_id}',
           K6_IMAGE, 'run', '--quiet', '/scripts/vote_flow.js']
    subprocess.run(cmd, check=True)
    return json.loads((RESULTS / f'{label}.json').read_text())


def write_table(results, args):
    base = results[0]['votes_per_second'] or None     # a run with 0 votes has no speed-up
    lines = [
        f"Load test run {datetime.now():%Y-%m-%d %H:%M}: {args.terminals} concurrent terminals, "
        f"{args.votes} votes per run, each replica limited to 1 CPU.",
        '',
        '| Replicas | Votes recorded | Failed | Time (s) | Throughput (votes/s) | Speed-up '
        '| Vote flow median (ms) | Vote flow p95 (ms) | Cast median (ms) |',
        '|---|---|---|---|---|---|---|---|---|',
    ]
    for r in results:
        speed_up = f"{r['votes_per_second'] / base:.2f}x" if base else '-'
        lines.append(
            f"| {r['replicas']} | {r['votes_recorded']} | {r['votes_failed']} | {r['duration_s']:.1f} "
            f"| {r['votes_per_second']:.2f} | {speed_up} "
            f"| {r['vote_flow_ms']['median']:.0f} | {r['vote_flow_ms']['p95']:.0f} "
            f"| {r['cast_ms']['median']:.0f} |")
    text = '\n'.join(lines) + '\n'
    (RESULTS / 'results.md').write_text(text, encoding='utf-8')
    print('\n' + text)
    print(f'Saved to {RESULTS / "results.md"}')


def main():
    parser = argparse.ArgumentParser(description='REQ-09 load test at several replica counts.')
    parser.add_argument('--replicas', default='1,2,3', help='comma-separated replica counts')
    parser.add_argument('--votes', type=int, default=400, help='votes per run')
    parser.add_argument('--terminals', type=int, default=50, help='concurrent terminals')
    args = parser.parse_args()
    counts = [int(c) for c in args.replicas.split(',')]
    RESULTS.mkdir(parents=True, exist_ok=True)

    network = compose_network()
    election_id = in_app("from app.models import Election\n"
                         "e = Election.query.filter_by(status='open').first()\n"
                         "print(e.id if e else '')")
    if not election_id:
        sys.exit('No open election: run  docker compose exec web flask --app run seed-demo')

    # Every vote needs a voter who has not voted yet: continue after the highest
    # numbered load voter who has voted (not the count: failed votes leave gaps).
    used = int(in_app("from app.models import User, VoteRecord, Voter\n"
                      "names = [u for (u,) in VoteRecord.query"
                      ".join(Voter, Voter.id == VoteRecord.voter_id)"
                      ".join(User, User.id == Voter.user_id)"
                      ".filter(User.username.like('load%'))"
                      ".with_entities(User.username)]\n"
                      "print(max((int(n[4:]) for n in names), default=0))"))
    needed = used + args.votes * len(counts)
    print(f'Creating load-test voters up to load{needed:03d} ...', flush=True)
    compose('exec', '-T', 'web', 'flask', '--app', 'run', 'seed-demo-voters',
            '--count', str(needed), '--prefix', 'load')

    results, first = [], used + 1
    try:
        for replicas in counts:
            scale_to(replicas)
            label = f'replicas-{replicas}'
            result = run_k6(network, label, first, args.votes, args.terminals, election_id)
            result['replicas'] = replicas
            results.append(result)
            first += args.votes
    finally:
        print('\nRestoring the default two replicas...', flush=True)
        compose('up', '-d', '--no-recreate', '--scale', 'web2=1', 'web', 'web2')
    write_table(results, args)


if __name__ == '__main__':
    main()
