"""Server-side input validation shared by the forms' API routes.

The browser checks (required, minlength, pattern...) are a convenience only:
anyone can call the API directly, so every rule is enforced here as well.
Each function returns (clean_value, error); error is None when the value is valid.
Limits match the database column sizes.
"""
import re
from datetime import date

_CONTROL = re.compile(r'[\x00-\x1f\x7f]')
_USERNAME = re.compile(r'^[A-Za-z0-9._-]{3,80}$')
_EMAIL = re.compile(r'^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$')
_POSTCODE = re.compile(r'^\d{4}$')               # Australian postcodes
_TERMINAL_ID = re.compile(r'^[A-Za-z0-9-]{3,50}$')

STAFF_PASSWORD_MIN = 12
VOTER_PASSWORD_MIN = 8


def clean_text(value, field, max_len, min_len=1):
    """Trimmed text: not blank or whitespace-only, no control characters, within limits."""
    if not isinstance(value, str):
        return None, f'{field} must be text'
    value = value.strip()
    if len(value) < min_len:
        return None, f'{field} is required' if min_len == 1 else f'{field} must be at least {min_len} characters'
    if len(value) > max_len:
        return None, f'{field} must be at most {max_len} characters'
    if _CONTROL.search(value):
        return None, f'{field} contains invalid characters'
    return value, None


def clean_username(value):
    if not isinstance(value, str) or not _USERNAME.match(value.strip()):
        return None, 'Username must be 3-80 characters: letters, digits, dot, dash or underscore'
    return value.strip(), None


def clean_email(value):
    if not isinstance(value, str):
        return None, 'Email must be text'
    value = value.strip().lower()
    if len(value) > 120 or not _EMAIL.match(value):
        return None, 'Enter a valid email address'
    return value, None


def check_password(value, username=None, staff=False):
    """Staff accounts need a longer password than voter logins."""
    minimum = STAFF_PASSWORD_MIN if staff else VOTER_PASSWORD_MIN
    if not isinstance(value, str) or len(value) < minimum:
        return None, f'Password must be at least {minimum} characters'
    if len(value) > 128:
        return None, 'Password must be at most 128 characters'
    if not (re.search(r'[A-Za-z]', value) and re.search(r'\d', value)):
        return None, 'Password must contain at least one letter and one number'
    if username and value.lower() == username.lower():
        return None, 'Password must not be the same as the username'
    return value, None


def clean_postcode(value):
    value = str(value).strip() if value is not None else ''
    if not _POSTCODE.match(value):
        return None, 'Postcode must be 4 digits'
    return value, None


def check_date_of_birth(value):
    """`value` is a date. Must be in the past and not before 1900."""
    if value >= date.today():
        return None, 'Date of birth must be in the past'
    if value.year < 1900:
        return None, 'Date of birth is not valid'
    return value, None


def check_election_date(value):
    """`value` is a date. A new election cannot be set in the past."""
    if value < date.today():
        return None, 'Election date cannot be in the past'
    return value, None


def clean_terminal_id(value):
    if not isinstance(value, str) or not _TERMINAL_ID.match(value.strip()):
        return None, 'Terminal ID must be 3-50 characters: letters, digits and dashes'
    return value.strip(), None   # case kept: other routes look terminals up by exact id
