# Known-bad configuration for the scanner self-test in .github/workflows/iac.yml.
# NOT deployed. Each resource contains a mistake the IaC gate must catch; if
# tfsec ever stops failing on this folder, the self-test job fails.

resource "aws_security_group" "db" {
  name        = "insecure-db"
  description = "Database security group"
  vpc_id      = "vpc-00000000"
}

# Database port open to the whole internet.
resource "aws_security_group_rule" "db_open_to_all" {
  type              = "ingress"
  description       = "PostgreSQL from anywhere"
  security_group_id = aws_security_group.db.id
  protocol          = "tcp"
  from_port         = 5432
  to_port           = 5432
  cidr_blocks       = ["0.0.0.0/0"]
}

# Database with a public address and no encryption at rest.
resource "aws_db_instance" "public" {
  identifier             = "insecure-db"
  engine                 = "postgres"
  instance_class         = "db.t4g.micro"
  allocated_storage      = 20
  username               = "admin"
  password               = "Password123!"
  vpc_security_group_ids = [aws_security_group.db.id]
  publicly_accessible    = true
  storage_encrypted      = false
  skip_final_snapshot    = true
}
