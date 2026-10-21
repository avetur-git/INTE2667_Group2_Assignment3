# REQ-06: the network, firewall and database access configuration for the
# production deployment, kept as code so every change is reviewed in a pull
# request and scanned by the IaC pipeline (.github/workflows/iac.yml).

terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.70"
    }
    postgresql = {
      source  = "cyrilgdn/postgresql"
      version = "~> 1.23"
    }
  }

  # Remote state, encrypted at rest and locked during apply. Bucket, key,
  # region and lock table are supplied at init time:
  #   terraform init -backend-config=backend.hcl
  # CI only validates, so it runs `terraform init -backend=false`.
  backend "s3" {
    encrypt = true
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "evoting-platform"
      Environment = var.environment
      ManagedBy   = "terraform"
      Requirement = "REQ-06"
    }
  }
}

# Database users and grants are applied through the RDS master account. The
# password is generated and stored by RDS in Secrets Manager; it is never
# written in this repository.
provider "postgresql" {
  host      = aws_db_instance.main.address
  port      = aws_db_instance.main.port
  username  = aws_db_instance.main.username
  password  = local.db_master_password
  sslmode   = "require"
  superuser = false
}
