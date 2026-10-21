"""
REQ-15 - Full details of every privileged action
================================================

One decorator, ``@audited``, writes the audit record for every privileged
route, so every entry has the same fixed format:

    who      user_id, username             (from permission_required)
    what     action                        (named on the decorator)
    on what  object_type, object_id        (decorator, URL argument or the route)
    when     created_at                    (set when the row is written)
    where    ip_address, user_agent, session_id (from the request and JWT)
    outcome  success                       (from the response status)

Routes no longer call log_action themselves. When a route knows more than
the decorator (the id of a new object, a more specific outcome, extra detail),
it says so through ``audit``:

    @bp.route('/', methods=['POST'])
    @permission_required(P.ELECTION_MANAGE)
    @audited('ELECTION_CREATED', object_type='election')
    def create_election(current_user):
        ...
        audit.target(election.id)
        audit.details['name'] = election.name

``@audited`` goes directly under ``@permission_required``. Refused requests
never reach it - permission_required already logs those as ACCESS_DENIED.

At start-up, check_privileged_routes_audited() refuses to run the app if any
route that needs a staff permission is missing ``@audited``.
"""
from functools import wraps

from flask import current_app, g
from werkzeug.local import LocalProxy

from app import db
from app.security.rbac import ALL_PERMISSIONS, permissions_for
from app.services.audit import log_action
from app.utils import constants as C

# Staff permissions: everything a voter does not hold
PRIVILEGED_PERMISSIONS = ALL_PERMISSIONS - permissions_for(C.ROLE_VOTER)


class AuditContext:
    """What the current privileged request did. Filled in by the decorator and,
    where it knows more, by the route through ``audit``."""

    def __init__(self, action, object_type, object_id):
        self.action = action
        self.object_type = object_type
        self.object_id = object_id
        self.details = {}
        self._extra = []

    def target(self, object_id, object_type=None):
        """Name the object this request acted on (for example a newly created row)."""
        self.object_id = object_id
        if object_type:
            self.object_type = object_type

    def also(self, action, object_type, object_id, details=None):
        """Record a second object changed by the same request, for example the
        candidate excluded when an approval request is executed."""
        self._extra.append((action, object_type, object_id, details))


def _current_context():
    ctx = g.get('audit_context')
    if ctx is None:
        raise RuntimeError('audit is only available inside an @audited route')
    return ctx


audit = LocalProxy(_current_context)


def audited(action, object_type, id_arg=None):
    """Audit a privileged route. `action` and `object_type` are required, so an
    entry can never be written without them. The object id is taken from the
    URL argument `id_arg` (default `<object_type>_id`) when the route has one."""
    if not action or not object_type:
        raise ValueError('@audited needs an action and an object_type')
    id_arg = id_arg or f'{object_type}_id'

    def wrapper(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            user = kwargs.get('current_user')
            if user is None:
                raise RuntimeError(f'@audited on {f.__name__} must sit under @permission_required')
            ctx = AuditContext(action, object_type, kwargs.get(id_arg))
            g.audit_context = ctx
            try:
                response = current_app.make_response(f(*args, **kwargs))
            except Exception as exc:
                db.session.rollback()   # never commit half-done work with the audit row
                ctx.details['error'] = type(exc).__name__
                _write(ctx, user, success=False)
                raise
            finally:
                g.pop('audit_context', None)

            success = response.status_code < 400
            if not success:
                db.session.rollback()
                body = response.get_json(silent=True) or {}
                ctx.details.setdefault('status', response.status_code)
                if body.get('error'):
                    ctx.details.setdefault('error', str(body['error'])[:200])
            _write(ctx, user, success)
            return response

        decorated.audit_action = action
        return decorated
    return wrapper


def _write(ctx, user, success):
    log_action(ctx.action, user=user, object_type=ctx.object_type, object_id=ctx.object_id,
               details=ctx.details or None, success=success)
    if success:
        for action, object_type, object_id, details in ctx._extra:
            log_action(action, user=user, object_type=object_type, object_id=object_id,
                       details=details, success=True)


def unaudited_privileged_routes(app):
    """Routes that need a staff permission but have no @audited."""
    missing = []
    for rule in app.url_map.iter_rules():
        view = app.view_functions[rule.endpoint]
        if (getattr(view, 'required_permission', None) in PRIVILEGED_PERMISSIONS
                and not getattr(view, 'audit_action', None)):
            missing.append(f'{rule.rule} ({rule.endpoint})')
    return sorted(missing)


def check_privileged_routes_audited(app):
    """Called from create_app: an unaudited privileged route stops the app starting."""
    missing = unaudited_privileged_routes(app)
    if missing:
        raise RuntimeError('Privileged routes without @audited (REQ-15): ' + ', '.join(missing))
