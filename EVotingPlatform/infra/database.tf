# PostgreSQL for the production deployment. It lives in the db subnets, is
# reachable only through aws_security_group.db, and is never public.

resource "aws_kms_key" "main" {
  description         = "evoting-${var.environment}: database, secrets and log encryption"
  enable_key_rotation = true
  policy              = data.aws_iam_policy_document.kms.json
}

resource "aws_kms_alias" "main" {
  name          = "alias/evoting-${var.environment}"
  target_key_id = aws_kms_key.main.key_id
}

data "aws_iam_policy_document" "kms" {
  statement {
    sid       = "AccountAdministration"
    actions   = ["kms:*"]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"]
    }
  }

  statement {
    sid = "CloudWatchLogs"
    actions = [
      "kms:Encrypt",
      "kms:Decrypt",
      "kms:ReEncrypt*",
      "kms:GenerateDataKey*",
      "kms:DescribeKey",
    ]
    resources = ["*"]
    principals {
      type        = "Service"
      identifiers = ["logs.${var.aws_region}.amazonaws.com"]
    }
  }
}

resource "aws_db_subnet_group" "main" {
  name       = "evoting-${var.environment}"
  subnet_ids = aws_subnet.db[*].id
}

resource "aws_db_parameter_group" "postgres" {
  name   = "evoting-${var.environment}-pg16"
  family = "postgres16"

  # Refuse unencrypted client connections.
  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }

  # Record logins, logouts and schema changes for the audit trail.
  parameter {
    name  = "log_connections"
    value = "1"
  }

  parameter {
    name  = "log_disconnections"
    value = "1"
  }

  parameter {
    name  = "log_statement"
    value = "ddl"
  }
}

resource "aws_db_instance" "main" {
  identifier     = "evoting-${var.environment}"
  engine         = "postgres"
  engine_version = "16.4"
  instance_class = var.db_instance_class

  allocated_storage     = 20
  max_allocated_storage = 100
  storage_encrypted     = true
  kms_key_id            = aws_kms_key.main.arn

  db_name  = "evoting"
  port     = var.db_port
  username = "evoting_admin"

  # RDS generates the master password and keeps it in Secrets Manager.
  manage_master_user_password   = true
  master_user_secret_kms_key_id = aws_kms_key.main.key_id

  # App users log in with short-lived IAM tokens instead of passwords
  # (see db_users.tf).
  iam_database_authentication_enabled = true

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.db.id]
  parameter_group_name   = aws_db_parameter_group.postgres.name
  publicly_accessible    = false

  multi_az                  = true
  backup_retention_period   = 14
  copy_tags_to_snapshot     = true
  deletion_protection       = true
  skip_final_snapshot       = false
  final_snapshot_identifier = "evoting-${var.environment}-final"

  auto_minor_version_upgrade      = true
  enabled_cloudwatch_logs_exports = ["postgresql", "upgrade"]
  performance_insights_enabled    = true
  performance_insights_kms_key_id = aws_kms_key.main.arn
}

data "aws_secretsmanager_secret_version" "db_master" {
  secret_id = aws_db_instance.main.master_user_secret[0].secret_arn
}

locals {
  db_master_password = jsondecode(data.aws_secretsmanager_secret_version.db_master.secret_string)["password"]
}
