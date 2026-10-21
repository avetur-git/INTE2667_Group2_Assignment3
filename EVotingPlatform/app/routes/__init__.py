# This file makes the routes directory a Python package
from .main import main_bp
from .auth import auth_bp
from .voters import voters_bp
from .elections import elections_bp
from .candidates import candidates_bp
from .voting import voting_bp
from .terminals import terminals_bp
from .results import results_bp
from .admin import admin_bp
from .approvals import approvals_bp

__all__ = ['main_bp', 'auth_bp', 'voters_bp', 'elections_bp', 'candidates_bp',
           'voting_bp', 'terminals_bp', 'results_bp', 'admin_bp', 'approvals_bp']
