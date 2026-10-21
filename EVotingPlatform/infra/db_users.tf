# Database users and what each one may do, as code. Any change to who can
# read or write which table goes through a reviewed pull request.
#
# Two databases, matching the two SQLAlchemy binds in app/__init__.py
# (REQ-01 keeps ballots apart from voter records):
#   evoting          DATABASE_URL         users, voters, elections, audit log, ...
#   evoting_ballots  BALLOT_DATABASE_URL  ballots, spent_tokens
#
# Login users authenticate with IAM tokens (rds_iam), so no database password
# exists for them. None of them can create, alter or drop tables; the schema is
# owned by evoting_owner, which cannot log in and is used only for migrations.
#
# Grants on named tables require the tables to exist, so apply this after the
# schema migration has run.

locals {
  main_db    = aws_db_instance.main.db_name
  ballots_db = postgresql_database.ballots.name

  # Tables the app reads and changes in normal operation.
  app_tables = [
    "users",
    "voters",
    "vote_records",
    "elections",
    "divisions",
    "candidates",
    "terminals",
    "tally_results",
  ]
}

resource "postgresql_database" "ballots" {
  name  = "evoting_ballots"
  owner = postgresql_role.schema_owner.name
}

# --- Roles -------------------------------------------------------------------

resource "postgresql_role" "schema_owner" {
  name  = "evoting_owner"
  login = false
}

resource "postgresql_role" "app" {
  name             = "evoting_app"
  login            = true
  roles            = ["rds_iam"]
  connection_limit = 100
}

resource "postgresql_role" "ballot_store" {
  name             = "evoting_ballot_store"
  login            = true
  roles            = ["rds_iam"]
  connection_limit = 50
}

resource "postgresql_role" "auditor" {
  name             = "evoting_auditor"
  login            = true
  roles            = ["rds_iam"]
  connection_limit = 5
}

# --- Remove default access ---------------------------------------------------
# By default every role (PUBLIC) may connect to a database and create objects
# in its public schema. Take both away so only the grants below apply.

resource "postgresql_grant" "revoke_public_connect_main" {
  database    = local.main_db
  role        = "public"
  object_type = "database"
  privileges  = []
}

resource "postgresql_grant" "revoke_public_connect_ballots" {
  database    = local.ballots_db
  role        = "public"
  object_type = "database"
  privileges  = []
}

resource "postgresql_grant" "revoke_public_schema_main" {
  database    = local.main_db
  role        = "public"
  schema      = "public"
  object_type = "schema"
  privileges  = []
}

resource "postgresql_grant" "revoke_public_schema_ballots" {
  database    = local.ballots_db
  role        = "public"
  schema      = "public"
  object_type = "schema"
  privileges  = []
}

# --- evoting_app: main database ----------------------------------------------

resource "postgresql_grant" "app_connect" {
  database    = local.main_db
  role        = postgresql_role.app.name
  object_type = "database"
  privileges  = ["CONNECT"]
}

resource "postgresql_grant" "app_schema" {
  database    = local.main_db
  role        = postgresql_role.app.name
  schema      = "public"
  object_type = "schema"
  privileges  = ["USAGE"]
}

resource "postgresql_grant" "app_tables" {
  database    = local.main_db
  role        = postgresql_role.app.name
  schema      = "public"
  object_type = "table"
  objects     = local.app_tables
  privileges  = ["SELECT", "INSERT", "UPDATE", "DELETE"]
}

# The audit log is append-only for the app: it can add entries and read them,
# but not change or remove them.
resource "postgresql_grant" "app_audit_log" {
  database    = local.main_db
  role        = postgresql_role.app.name
  schema      = "public"
  object_type = "table"
  objects     = ["audit_logs"]
  privileges  = ["SELECT", "INSERT"]
}

resource "postgresql_grant" "app_sequences" {
  database    = local.main_db
  role        = postgresql_role.app.name
  schema      = "public"
  object_type = "sequence"
  privileges  = ["USAGE", "SELECT"]
}

# --- evoting_ballot_store: ballots database ----------------------------------
# Ballots can be cast and counted, never edited or deleted.

resource "postgresql_grant" "ballot_store_connect" {
  database    = local.ballots_db
  role        = postgresql_role.ballot_store.name
  object_type = "database"
  privileges  = ["CONNECT"]
}

resource "postgresql_grant" "ballot_store_schema" {
  database    = local.ballots_db
  role        = postgresql_role.ballot_store.name
  schema      = "public"
  object_type = "schema"
  privileges  = ["USAGE"]
}

resource "postgresql_grant" "ballot_store_tables" {
  database    = local.ballots_db
  role        = postgresql_role.ballot_store.name
  schema      = "public"
  object_type = "table"
  objects     = ["ballots", "spent_tokens"]
  privileges  = ["SELECT", "INSERT"]
}

resource "postgresql_grant" "ballot_store_sequences" {
  database    = local.ballots_db
  role        = postgresql_role.ballot_store.name
  schema      = "public"
  object_type = "sequence"
  privileges  = ["USAGE", "SELECT"]
}

# --- evoting_auditor: read-only audit log ------------------------------------
# For reviewers of privileged actions (REQ-15). No access to voters or ballots.

resource "postgresql_grant" "auditor_connect" {
  database    = local.main_db
  role        = postgresql_role.auditor.name
  object_type = "database"
  privileges  = ["CONNECT"]
}

resource "postgresql_grant" "auditor_schema" {
  database    = local.main_db
  role        = postgresql_role.auditor.name
  schema      = "public"
  object_type = "schema"
  privileges  = ["USAGE"]
}

resource "postgresql_grant" "auditor_audit_log" {
  database    = local.main_db
  role        = postgresql_role.auditor.name
  schema      = "public"
  object_type = "table"
  objects     = ["audit_logs"]
  privileges  = ["SELECT"]
}
