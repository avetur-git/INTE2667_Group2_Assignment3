"""REQ-07 demo: send a burst of requests and count the status codes.

    python scripts/flood_test.py                          # 40 failed logins at http://localhost:9001
    python scripts/flood_test.py --path /health --requests 300 --workers 50
    python scripts/flood_test.py --url http://localhost:3000   # app without the proxy

A 429 from the app has a JSON body with "retry_after"; a 429 from nginx does
not, so the summary shows which layer refused the request. For local testing
only - do not point this at systems you do not own.
"""
import argparse
import json
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor


def send(url, body):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method='POST' if data else 'GET',
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:   # nosec B310 - local demo URL
            return r.status, ''
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors='replace')
    except OSError as e:
        return f'error: {e.__class__.__name__}', ''


def label(status, text):
    if status == 429:
        return '429 (app)' if 'retry_after' in text else '429 (nginx)'
    return str(status)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--url', default='http://localhost:9001')
    parser.add_argument('--path', default='/api/auth/login')
    parser.add_argument('--requests', type=int, default=40)
    parser.add_argument('--workers', type=int, default=1,
                        help='parallel senders (1 = one after another)')
    args = parser.parse_args()

    target = args.url.rstrip('/') + args.path
    body = {'username': 'flood-test', 'password': 'wrong'} if args.path.endswith('/login') else None

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(lambda _: send(target, body), range(args.requests)))

    order = [label(s, t) for s, t in results]
    print(f'{args.requests} requests to {target}')
    print('in order:', ' '.join(order))
    for code, count in sorted(Counter(order).items()):
        print(f'  {code:>12}: {count}')


if __name__ == '__main__':
    main()
