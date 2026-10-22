"""REQ-07 - Throttle abnormal traffic on the login and voting endpoints.

Layers (see docs/REQ-07_DDOS.md):
  1. Cloud DDoS service, CDN and WAF        production only, documented
  2. Reverse proxy (docker/nginx/nginx.conf)  per-IP limits, drops floods before Flask
  3. This module                            per-endpoint limits inside the API

    @rate_limited('login')               # counted per client IP
    @rate_limited('vote', per='user')    # counted per logged-in user (IP if not logged in)

Voting is counted per user because many voters at one polling station share the
station's public IP; a per-IP limit would block a busy station.

Over the limit the API answers 429 with a Retry-After header. The first refusal
for a client in each window is written to the audit log as RATE_LIMITED; later
refusals in the same window are not, so a flood cannot flood the audit log too.

Limits are config values of the form 'requests/seconds', e.g. RATE_LIMIT_LOGIN =
'20/60'. Counters live in this process's memory. Running several app instances
needs a shared store (Redis); see docs/REQ-07_DDOS.md.
"""
import math
import os
import threading
import time
from collections import deque
from functools import wraps

from flask import current_app, jsonify, request
from flask_jwt_extended import get_jwt_identity, verify_jwt_in_request
from werkzeug.middleware.proxy_fix import ProxyFix

from app.services.audit import log_action

HTTP_TOO_MANY_REQUESTS = 429
RATE_LIMITED_ACTION = 'RATE_LIMITED'

# Rule name -> default 'requests/seconds'. Override with RATE_LIMIT_<RULE>.
DEFAULT_LIMITS = {
    'login': '20/60',   # per IP: enough for a polling station, far too few for password guessing
    'vote': '30/60',    # per voter: loading the ballot paper, casting, and retries
}

# Forget idle clients once this many are tracked, so a flood of new addresses
# cannot grow memory without bound.
MAX_TRACKED_KEYS = 50_000


def parse_limit(text):
    """'20/60' -> (20, 60). Raises ValueError for anything else."""
    try:
        count, seconds = (int(part) for part in str(text).split('/'))
    except ValueError:
        raise ValueError(f"rate limit must look like 'requests/seconds', got {text!r}") from None
    if count < 1 or seconds < 1:
        raise ValueError(f'rate limit must be positive, got {text!r}')
    return count, seconds


class RateLimiter:
    """Sliding-window counter: at most `limit` requests per key in any `window` seconds."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self._hits = {}      # key -> (window, deque of request times)
        self._logged = {}    # key -> time until which a breach is already logged
        self._lock = threading.Lock()

    def hit(self, key, limit, window):
        """Count one request. Returns (allowed, retry_after_seconds, first_breach)."""
        now = self.clock()
        with self._lock:
            _, hits = self._hits.setdefault(key, (window, deque()))
            while hits and hits[0] <= now - window:
                hits.popleft()
            if len(hits) < limit:
                hits.append(now)
                return True, 0, False

            # Refused requests are not counted, so the client is let back in as
            # soon as its oldest counted request leaves the window.
            free_at = hits[0] + window
            retry_after = max(1, math.ceil(free_at - now))
            first_breach = self._logged.get(key, 0) <= now
            if first_breach:
                self._logged[key] = free_at
            if len(self._hits) > MAX_TRACKED_KEYS:
                self._forget_idle(now)
            return False, retry_after, first_breach

    def _forget_idle(self, now):
        for key, (window, hits) in list(self._hits.items()):
            if not hits or hits[-1] <= now - window:
                del self._hits[key]
                self._logged.pop(key, None)


def _logged_in_user_id():
    try:
        verify_jwt_in_request(optional=True)
        return get_jwt_identity()
    except Exception:   # missing, expired or forged token: fall back to the IP
        return None


def _audit_breach(rule, limit, window, counted_by, user_id):
    user = None
    if user_id is not None:
        from app.models.user import User   # imported here to avoid a circular import
        user = User.query.get(int(user_id))
    try:
        log_action(RATE_LIMITED_ACTION, user=user, object_type='endpoint',
                   object_id=f'{request.method} {request.path}',
                   details={'rule': rule, 'limit': limit, 'window_seconds': window,
                            'counted_by': counted_by},
                   success=False)
    except Exception:
        # Keep refusing the flood even if the audit store is struggling.
        current_app.logger.exception('REQ-07: could not write RATE_LIMITED audit entry')


def rate_limited(rule, per='ip'):
    """Limit a route with the named rule. `per` is 'ip' or 'user'."""
    if rule not in DEFAULT_LIMITS:
        raise ValueError(f'unknown rate limit rule {rule!r}')   # caught at import, like a typo
    if per not in ('ip', 'user'):
        raise ValueError("per must be 'ip' or 'user'")

    def wrapper(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not current_app.config.get('RATE_LIMIT_ENABLED', True):
                return f(*args, **kwargs)

            limit, window = parse_limit(current_app.config[f'RATE_LIMIT_{rule.upper()}'])
            user_id = _logged_in_user_id() if per == 'user' else None
            if user_id is not None:
                counted_by, key = 'user', f'{rule}:user:{user_id}'
            else:
                counted_by, key = 'ip', f'{rule}:ip:{request.remote_addr}'

            limiter = current_app.extensions['rate_limiter']
            allowed, retry_after, first_breach = limiter.hit(key, limit, window)
            if allowed:
                return f(*args, **kwargs)

            if first_breach:
                _audit_breach(rule, limit, window, counted_by, user_id)
            response = jsonify({'error': 'Too many requests. Please wait and try again.',
                                'retry_after': retry_after})
            response.status_code = HTTP_TOO_MANY_REQUESTS
            response.headers['Retry-After'] = str(retry_after)
            return response

        decorated.rate_limit_rule = rule
        return decorated
    return wrapper


def init_app(app):
    """Read the limits (failing at startup on a bad value) and trust the reverse
    proxy's client address when one is configured."""
    for rule, default in DEFAULT_LIMITS.items():
        name = f'RATE_LIMIT_{rule.upper()}'
        app.config.setdefault(name, os.environ.get(name, default))
        parse_limit(app.config[name])
    app.config.setdefault('RATE_LIMIT_ENABLED',
                          os.environ.get('RATE_LIMIT_ENABLED', 'true').lower() == 'true')
    app.extensions['rate_limiter'] = RateLimiter()

    # Behind nginx every request comes from the proxy's address, so all clients
    # would share one limit. TRUSTED_PROXY_COUNT=1 takes the client address from
    # the X-Forwarded-For header the proxy sets. Only turn it on when the app
    # cannot be reached except through the proxy, or the header can be forged.
    proxies = int(app.config.get('TRUSTED_PROXY_COUNT', os.environ.get('TRUSTED_PROXY_COUNT', 0)))
    if proxies > 0:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=proxies)
