# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------
ROLE_VOTER = 'voter'
ROLE_AEC_OFFICER = 'aec_officer'                  # registers citizens (spec: "AEC employees")
ROLE_DELEGATE = 'commissioner_delegate'           # manages candidate details, exclusions
ROLE_ADMIN = 'election_admin'                     # elections, terminals, tally, users
ALL_ROLES = [ROLE_VOTER, ROLE_AEC_OFFICER, ROLE_DELEGATE, ROLE_ADMIN]
PRIVILEGED_ROLES = [ROLE_AEC_OFFICER, ROLE_DELEGATE, ROLE_ADMIN]

# Sensitive actions that require maker-checker authorisation before execution.
SENSITIVE_ACTIONS = {
    'EXCLUDE_CANDIDATE': 'candidate exclusion',
    'OPEN_ELECTION': 'opening an election',
    'CLOSE_ELECTION': 'closing an election',
}
APPROVAL_REQUEST_TIMEOUT_HOURS = 24

# ---------------------------------------------------------------------------
# Voting channels (from the systems description)
# ---------------------------------------------------------------------------
CHANNEL_POLLING_STATION = 'polling_station'       # computer terminals at polling stations
CHANNEL_ONLINE = 'online'                         # voting from home within Australia
CHANNEL_DIPLOMATIC_MISSION = 'diplomatic_mission' # overseas voting, missions only
ALL_CHANNELS = [CHANNEL_POLLING_STATION, CHANNEL_ONLINE, CHANNEL_DIPLOMATIC_MISSION]
TERMINAL_CHANNELS = [CHANNEL_POLLING_STATION, CHANNEL_DIPLOMATIC_MISSION]

# ---------------------------------------------------------------------------
# Elections, contests, enrolment
# ---------------------------------------------------------------------------
ELECTION_DRAFT = 'draft'
ELECTION_OPEN = 'open'
ELECTION_CLOSED = 'closed'
ELECTION_COUNTED = 'counted'
ELECTION_STATUSES = [ELECTION_DRAFT, ELECTION_OPEN, ELECTION_CLOSED, ELECTION_COUNTED]

CONTEST_HOUSE = 'house'
CONTEST_SENATE = 'senate'

ENROLMENT_PENDING = 'pending'
ENROLMENT_ENROLLED = 'enrolled'
ENROLMENT_REMOVED = 'removed'

STATES = ['NSW', 'VIC', 'QLD', 'WA', 'SA', 'TAS', 'ACT', 'NT']

# Senators per state/territory, from the systems description.
# Elections can override this (Election.senate_seats_override) for testing.
SENATE_SEATS = {'NSW': 12, 'VIC': 12, 'QLD': 12, 'WA': 12, 'SA': 12, 'TAS': 12, 'ACT': 2, 'NT': 2}

UNGROUPED = 'UG'          # group code for Senate candidates with no party group box
VOTING_AGE = 18

# Minimum numbered preferences for a Senate vote to count as formal.
# Configurable - confirm the current numbers against the AEC rules before relying on them.
SENATE_ATL_MIN = 6        # above the line: boxes numbered
SENATE_BTL_MIN = 12       # below the line: candidates numbered

# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------
ERROR_USER_NOT_FOUND = 'User not found'
ERROR_USER_INACTIVE = 'User not found or inactive'
ERROR_ACCESS_DENIED = 'Access denied for your role'
ERROR_INVALID_CREDENTIALS = 'Invalid username or password'
ERROR_NOT_ENROLLED = 'You are not enrolled to vote'
ERROR_UNDERAGE = 'Voter is under the voting age on election day'
ERROR_NOT_CITIZEN = 'Voter is not an eligible citizen'
ERROR_ALREADY_VOTED = 'A ballot has already been cast for this election'
ERROR_ELECTION_NOT_OPEN = 'Election is not open for voting'
ERROR_ELECTION_NOT_FOUND = 'Election not found'
ERROR_VOTER_NOT_FOUND = 'Voter record not found'
ERROR_CANDIDATE_NOT_FOUND = 'Candidate not found'
ERROR_TERMINAL_INVALID = 'Unknown, inactive or mismatched voting terminal'

# HTTP status codes
HTTP_OK = 200
HTTP_CREATED = 201
HTTP_BAD_REQUEST = 400
HTTP_UNAUTHORIZED = 401
HTTP_FORBIDDEN = 403
HTTP_NOT_FOUND = 404
HTTP_CONFLICT = 409
HTTP_UNPROCESSABLE_ENTITY = 422
HTTP_INTERNAL_SERVER_ERROR = 500
