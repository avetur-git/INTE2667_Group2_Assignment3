# Three tiers, each in its own subnets across two availability zones:
#   public  - the load balancer only (the single internet-facing entry point)
#   app     - the Flask containers, no route to or from the internet
#   db      - PostgreSQL, no route to or from the internet

data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_caller_identity" "current" {}

locals {
  azs = slice(data.aws_availability_zones.available.names, 0, 2)

  public_cidrs = [for i in range(2) : cidrsubnet(var.vpc_cidr, 8, i)]
  app_cidrs    = [for i in range(2) : cidrsubnet(var.vpc_cidr, 8, 10 + i)]
  db_cidrs     = [for i in range(2) : cidrsubnet(var.vpc_cidr, 8, 20 + i)]
}

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = "evoting-${var.environment}" }
}

# The default security group allows all traffic between its members. Taking it
# under management with no rules leaves it empty, so nothing can rely on it.
resource "aws_default_security_group" "default" {
  vpc_id = aws_vpc.main.id
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
}

resource "aws_subnet" "public" {
  count                   = 2
  vpc_id                  = aws_vpc.main.id
  cidr_block              = local.public_cidrs[count.index]
  availability_zone       = local.azs[count.index]
  map_public_ip_on_launch = false

  tags = { Name = "evoting-public-${count.index}", Tier = "public" }
}

resource "aws_subnet" "app" {
  count             = 2
  vpc_id            = aws_vpc.main.id
  cidr_block        = local.app_cidrs[count.index]
  availability_zone = local.azs[count.index]

  tags = { Name = "evoting-app-${count.index}", Tier = "app" }
}

resource "aws_subnet" "db" {
  count             = 2
  vpc_id            = aws_vpc.main.id
  cidr_block        = local.db_cidrs[count.index]
  availability_zone = local.azs[count.index]

  tags = { Name = "evoting-db-${count.index}", Tier = "db" }
}

# Only the public subnets get a route to the internet gateway. The app and db
# subnets use the VPC main route table, which has the local route only.
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }
}

resource "aws_route_table_association" "public" {
  count          = 2
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# Subnet-level firewall for the database tier, as a second layer behind the
# security groups: PostgreSQL in from the app subnets, replies back out to
# them, and nothing else in either direction.
resource "aws_network_acl" "db" {
  vpc_id     = aws_vpc.main.id
  subnet_ids = aws_subnet.db[*].id

  dynamic "ingress" {
    for_each = local.app_cidrs
    content {
      rule_no    = 100 + ingress.key
      action     = "allow"
      protocol   = "tcp"
      cidr_block = ingress.value
      from_port  = var.db_port
      to_port    = var.db_port
    }
  }

  dynamic "egress" {
    for_each = local.app_cidrs
    content {
      rule_no    = 100 + egress.key
      action     = "allow"
      protocol   = "tcp"
      cidr_block = egress.value
      from_port  = 1024
      to_port    = 65535
    }
  }

  tags = { Name = "evoting-db" }
}

# VPC flow logs record accepted and rejected connections, so blocked attempts
# against the firewall rules can be investigated.
resource "aws_flow_log" "main" {
  vpc_id          = aws_vpc.main.id
  traffic_type    = "ALL"
  log_destination = aws_cloudwatch_log_group.flow_logs.arn
  iam_role_arn    = aws_iam_role.flow_logs.arn
}

resource "aws_cloudwatch_log_group" "flow_logs" {
  name              = "/evoting/${var.environment}/vpc-flow-logs"
  retention_in_days = 365
  kms_key_id        = aws_kms_key.main.arn
}

data "aws_iam_policy_document" "flow_logs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["vpc-flow-logs.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "flow_logs" {
  name               = "evoting-${var.environment}-flow-logs"
  assume_role_policy = data.aws_iam_policy_document.flow_logs_assume.json
}

#tfsec:ignore:aws-iam-no-policy-wildcards Reviewed exception (REQ-06): flow log stream names are created by AWS per network interface, so the policy has to cover all streams in this one log group.
data "aws_iam_policy_document" "flow_logs_write" {
  statement {
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
      "logs:DescribeLogStreams",
    ]
    resources = ["${aws_cloudwatch_log_group.flow_logs.arn}:*"]
  }
}

resource "aws_iam_role_policy" "flow_logs" {
  name   = "write-flow-logs"
  role   = aws_iam_role.flow_logs.id
  policy = data.aws_iam_policy_document.flow_logs_write.json
}
