# Firewall rules between the tiers. Each group starts with no rules, and every
# allowed flow is a separate, described rule below:
#
#   internet --443--> alb --app_port--> app --db_port--> db
#
# Tiers behind the load balancer only accept traffic from the security group
# of the tier in front of them, never from an address range. Changes to this
# file need approval from the platform owner (.github/CODEOWNERS) and must pass
# the tfsec scan, which fails on any HIGH or CRITICAL finding.

resource "aws_security_group" "alb" {
  name        = "evoting-${var.environment}-alb"
  description = "Public load balancer: HTTPS in from the internet, out to the app tier only"
  vpc_id      = aws_vpc.main.id
}

resource "aws_security_group" "app" {
  name        = "evoting-${var.environment}-app"
  description = "Flask app tier: in from the load balancer, out to the database only"
  vpc_id      = aws_vpc.main.id
}

resource "aws_security_group" "db" {
  name        = "evoting-${var.environment}-db"
  description = "PostgreSQL: in from the app tier only, no outbound"
  vpc_id      = aws_vpc.main.id
}

# --- Load balancer -----------------------------------------------------------

#tfsec:ignore:aws-ec2-no-public-ingress-sgr Reviewed exception (REQ-06): the voting site is public, and HTTPS on the load balancer is the only port open to the internet.
resource "aws_security_group_rule" "alb_in_https" {
  type              = "ingress"
  description       = "HTTPS from voters on the internet"
  security_group_id = aws_security_group.alb.id
  protocol          = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_blocks       = ["0.0.0.0/0"]
}

resource "aws_security_group_rule" "alb_out_app" {
  type                     = "egress"
  description              = "Forward requests to the app tier"
  security_group_id        = aws_security_group.alb.id
  protocol                 = "tcp"
  from_port                = var.app_port
  to_port                  = var.app_port
  source_security_group_id = aws_security_group.app.id
}

# --- App tier ----------------------------------------------------------------

resource "aws_security_group_rule" "app_in_alb" {
  type                     = "ingress"
  description              = "Requests from the load balancer only"
  security_group_id        = aws_security_group.app.id
  protocol                 = "tcp"
  from_port                = var.app_port
  to_port                  = var.app_port
  source_security_group_id = aws_security_group.alb.id
}

resource "aws_security_group_rule" "app_out_db" {
  type                     = "egress"
  description              = "PostgreSQL connections to the database tier"
  security_group_id        = aws_security_group.app.id
  protocol                 = "tcp"
  from_port                = var.db_port
  to_port                  = var.db_port
  source_security_group_id = aws_security_group.db.id
}

# --- Database tier -----------------------------------------------------------
# No egress rules: the database never opens outbound connections.

resource "aws_security_group_rule" "db_in_app" {
  type                     = "ingress"
  description              = "PostgreSQL from the app tier only"
  security_group_id        = aws_security_group.db.id
  protocol                 = "tcp"
  from_port                = var.db_port
  to_port                  = var.db_port
  source_security_group_id = aws_security_group.app.id
}
